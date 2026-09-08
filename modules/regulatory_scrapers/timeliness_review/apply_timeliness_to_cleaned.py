# -*- coding: utf-8 -*-
"""
apply_timeliness_to_cleaned.py —— 核验结果 → 五源 cleaned 时效字段回写（方案 A 等价同步器）

背景（2026-09-07 实测）：
  清洗重跑以 raw 重新生成 cleaned，五个 MAPPER 均硬编码 `timeliness_status: ""`，
  因此**重跑会整体覆盖**既有北大法宝回写层（编排顺序缺陷）。

用途：
  清洗重跑之后立即执行本脚本，按「文号优先 → 标题兜底」将既有核验结果
  （`时效性标注结果清单_*.jsonl`）回写进最新 cleaned 的三字段：
  timeliness_status / replacement_document / verification_source。
  保证「清洗基态 → 时效回写」顺序不可颠倒。

重要：
  - **零核验资源消耗**：仅读取既有清单做回写，不调用北大法宝、不发起任何核验请求。
  - 清单标注日期在 90 日内方可复用（reuse 窗口），超期应重新核验后再回写。
  - **不匹配者保持原值（空）**，不做清空、不做臆造。

用法：
  python apply_timeliness_to_cleaned.py --dry-run              # 仅报告不写盘
  python apply_timeliness_to_cleaned.py                        # 回写（自动备份）
  python apply_timeliness_to_cleaned.py --source nfra          # 仅指定源
  python apply_timeliness_to_cleaned.py --ledger <清单路径>     # 指定核验结果清单
  python apply_timeliness_to_cleaned.py --no-backup            # 跳过备份（不推荐）
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import re
import shutil
import sys

# 正文等字段可能远超默认 131072 上限，放宽以允许大字段读写
csv.field_size_limit(sys.maxsize)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # regulatory_scrapers/
sys.path.insert(0, os.path.join(ROOT, "std_lib"))

from scraper_std.doc_number import normalize_doc_number  # noqa: E402

REVIEW = os.path.join(ROOT, "timeliness_review")
CLEANED = os.path.join(ROOT, "data", "cleaned")
BACKUP_ROOT = os.path.join(ROOT, "backups")

# 受控枚举（《三项目状态码值统一规范》v3）——非规范值一律不回写
TIMELINESS_STATUS = frozenset({
    "valid", "amended", "repealed", "partially_repealed",
    "expired", "pending", "uncertain",
})
TARGET_FIELDS = ("timeliness_status", "replacement_document", "verification_source")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _date(s: str) -> str:
    """发布日期归一到 YYYY-MM-DD（用于区分同名不同版本）。"""
    return (s or "")[:10]


def _norm_docno(s: str) -> str:
    v = (s or "").strip()
    if not v or v.upper() in ("N/A", "NA", "NULL"):
        return ""
    return _norm(normalize_doc_number(v))


def pick_latest_ledger() -> str:
    """取最新一份时效性标注结果清单（按文件名日期降序）。

    B2 修复（2026-09-08）：清单缺失时给出修复指引而非裸 FileNotFound——
    全量清单由 consolidate_timeliness 输出（时效性标注结果清单_全量_<date>.jsonl，
    其种子基线 时效性标注结果清单_20260824.jsonl 在旧仓 timeliness_review，需复制到 REVIEW 后先跑 consolidate）。
    """
    cands = sorted(glob.glob(os.path.join(REVIEW, "时效性标注结果清单_*.jsonl")))
    if not cands:
        raise FileNotFoundError(
            "未找到 时效性标注结果清单_*.jsonl（REVIEW=%s）。修复：先复制旧仓基线"
            " 时效性标注结果清单_20260824.jsonl 至 REVIEW，再运行 consolidate_timeliness.py 产出全量清单。"
            % REVIEW)
    return cands[-1]


def load_ledger(path: str):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_index(rows, source: str):
    """按源构建索引（引入「标题+发布日期」以区分同名不同版本）。

    返回 (by_no, by_title_date, by_title_unique, conflicts)
      by_no           文号 → 条目（文号唯一性最好，优先匹配）
      by_title_date   (标题, 发布日期) → 条目（区分《X办法》现行版 / 已废止旧版）
      by_title_unique 标题 → 条目（**仅当该标题在清单中状态唯一**时可用，避免版本误配）
      conflicts       同键多状态的冲突（仅报告）
    """
    by_no: dict[str, dict] = {}
    by_title_date: dict[tuple, dict] = {}
    title_states: dict[str, set] = {}
    conflicts: list[tuple] = []
    picked: list[dict] = []
    for r in rows:
        if r.get("source") != source:
            continue
        st = (r.get("timeliness_status") or "").strip()
        if st not in TIMELINESS_STATUS:
            continue                       # 非规范值不回写
        picked.append(r)
        k_no = _norm_docno(r.get("document_number"))
        if k_no:
            prev = by_no.get(k_no)
            if prev is not None and prev.get("timeliness_status") != st:
                conflicts.append(("docno", k_no, prev.get("timeliness_status"), st))
            by_no.setdefault(k_no, r)
        k_ti = _norm(r.get("title"))
        if not k_ti:
            continue
        title_states.setdefault(k_ti, set()).add(st)
        by_title_date.setdefault((k_ti, _date(r.get("publish_date"))), r)
    by_title_unique: dict[str, dict] = {}
    for r in picked:
        k_ti = _norm(r.get("title"))
        if k_ti and len(title_states.get(k_ti, ())) == 1:
            by_title_unique.setdefault(k_ti, r)
    return by_no, by_title_date, by_title_unique, conflicts


def apply_source(source: str, by_no, by_title_date, by_title_unique,
                 dry_run: bool, no_backup: bool):
    """对单源最新 cleaned 回写三字段（jsonl + csv 双轨）。"""
    sys.path.insert(0, ROOT)
    from clean_index import get_clean_index
    idx = get_clean_index()
    jf = idx.latest_jsonl_path(source)
    if not jf:
        return {"source": source, "reason": "无 cleaned 产物"}
    if not os.path.isabs(jf):                 # clean_index 返回相对 ROOT 的路径
        jf = os.path.join(ROOT, jf)
    cf = jf.replace(".jsonl", ".csv")
    if not os.path.exists(jf):
        return {"source": source, "reason": "无 cleaned 产物"}

    recs = []
    with open(jf, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))

    stat = {"source": source, "total": len(recs), "hit_docno": 0,
            "hit_title_date": 0, "hit_title": 0, "skipped": 0,
            "written": 0, "unchanged": 0}
    for r in recs:
        hit = None
        k_no = _norm_docno(r.get("document_number"))
        if k_no and k_no in by_no:
            hit = by_no[k_no]
            stat["hit_docno"] += 1
        else:
            k_ti = _norm(r.get("title"))
            if k_ti and (k_ti, _date(r.get("publish_date"))) in by_title_date:
                hit = by_title_date[(k_ti, _date(r.get("publish_date")))]
                stat["hit_title_date"] += 1
            elif k_ti and k_ti in by_title_unique:
                hit = by_title_unique[k_ti]
                stat["hit_title"] += 1
        if not hit:
            stat["skipped"] += 1
            continue                       # 无核验结果 / 状态歧义 → 保持原值（空）
        before = tuple(r.get(k, "") for k in TARGET_FIELDS)
        for k in TARGET_FIELDS:
            r[k] = hit.get(k, "") or ""
        if tuple(r.get(k, "") for k in TARGET_FIELDS) != before:
            stat["written"] += 1
        else:
            stat["unchanged"] += 1

    if dry_run:
        stat["dry_run"] = True
        return stat

    # 备份
    if not no_backup:
        bdir = os.path.join(
            BACKUP_ROOT,
            "cleaned_before_timeliness_%s" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(bdir, exist_ok=True)
        for p in (jf, cf):
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(bdir, os.path.basename(p)))
        stat["backup"] = bdir

    # 写 jsonl（原子）
    tmp = jf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jf)

    # 写 csv（按行号与 jsonl 对齐，保持列序与 BOM）
    if os.path.exists(cf):
        with open(cf, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            fields = rd.fieldnames or []
            rows = list(rd)
        if len(rows) != len(recs):
            stat["csv_warn"] = "行数不一致(%d vs %d)，跳过 CSV" % (len(rows), len(recs))
        else:
            for i, r in enumerate(recs):
                for k in TARGET_FIELDS:
                    if k in fields:
                        rows[i][k] = r.get(k, "")
            tmp = cf + ".tmp"
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in rows:
                    w.writerow(row)
            os.replace(tmp, cf)
    return stat


def main() -> int:
    ap = argparse.ArgumentParser(description="核验结果 → cleaned 时效字段回写（方案 A）")
    ap.add_argument("--source", help="仅处理指定源（nfra/mof/pbc/gov/supp）")
    ap.add_argument("--ledger", help="核验结果清单 JSONL（默认取最新）")
    ap.add_argument("--dry-run", action="store_true", help="仅报告不写盘")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（不推荐）")
    args = ap.parse_args()

    ledger = args.ledger or pick_latest_ledger()
    m = re.search(r"(\d{8})", os.path.basename(ledger))
    age = ""
    if m:
        d = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        age = "标注日期 %s（距今 %d 天%s）" % (
            m.group(1), (datetime.date.today() - d).days,
            "，≤90 日可复用" if (datetime.date.today() - d).days <= 90 else "，⚠️超 90 日")
    print("核验结果清单: %s  %s" % (os.path.basename(ledger), age))

    rows = load_ledger(ledger)
    print("清单条数: %d" % len(rows))
    sources = [args.source] if args.source else ["nfra", "mof", "pbc", "gov", "supp"]

    stats = []
    for s in sources:
        by_no, by_td, by_tu, conflicts = build_index(rows, s)
        if not by_no and not by_td and not by_tu:
            print("\n[%s] 清单无该源核验结果，跳过" % s)
            continue
        st = apply_source(s, by_no, by_td, by_tu, args.dry_run, args.no_backup)
        st["ledger_keys"] = len(by_no) + len(by_td) + len(by_tu)
        st["conflict"] = len(conflicts)
        stats.append(st)

    print("\n" + "=" * 76)
    print("%-6s %7s %8s %9s %10s %8s %7s %7s" % (
        "源", "总记录", "清单键", "文号命中", "标题+日期", "唯一标题", "写回", "跳过"))
    print("-" * 76)
    for st in stats:
        if "reason" in st:
            print("%-6s %s" % (st["source"], st["reason"]))
            continue
        print("%-6s %7d %8d %9d %10d %8d %7d %7d" % (
            st["source"], st["total"], st.get("ledger_keys", 0),
            st["hit_docno"], st["hit_title_date"], st["hit_title"],
            st["written"], st["skipped"]))
        if st.get("backup"):
            print("       备份: %s" % st["backup"])
        if st.get("csv_warn"):
            print("       ⚠️ %s" % st["csv_warn"])
    print("=" * 68)
    print("模式: %s" % ("DRY-RUN（未写盘）" if args.dry_run else "已回写"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
