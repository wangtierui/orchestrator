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

派生刷新（顺序纪律，2026-09-19 补齐）：
  回写会改变 cleaned 内容 → 必须同轮刷新其**派生产物**，否则下游取到"改前版本"：
    ① `clean_index`（F-C05，既有）② `clauses` 五源条文产物（本次补：clauses 在编排链
    阶段 1 由 clean 尾部构建，**早于**本步 → 不刷新则条款行的时效投影系统性滞后一轮）。

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # modules/regulatory_scrapers/
sys.path.insert(0, os.path.join(ROOT, "std_lib"))
# R4 适配：std_lib 上收 orchestrator 根（scrapers/std_lib 旧仓路径已失效）
_ORCH_ROOT = os.path.dirname(os.path.dirname(ROOT))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
sys.path.insert(0, os.path.join(_ORCH_ROOT, "std_lib"))

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


# norm-specialization: 修复型文号（normalize_doc_number 残渣剥离 + 标准层归一，双段组合）
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


def writeback_source(source: str, fields_for, *, dry_run: bool = False,
                     no_backup: bool = False, backup_tag: str = "apply") -> dict:
    """统一回写单点（C-12 收敛，2026-09-12）：按 fields_for(rec)->dict|None 回写单源
    cleaned 三字段——jsonl+csv 双轨、原子写、备份、status 派生（H-07）。

    —— 全仓 cleaned 时效回写**唯一实现**：apply_timeliness（ledger 三级匹配）、
    sync_three_modules（核验变更增量）、classifier_pkulaw_verify（核验变更）一律调用本函数，
    禁止各自实现（原三份分叉：字段集/双轨性/原子性/备份各不同——审查 C-12/C-10）。
    """
    sys.path.insert(0, ROOT)
    from clean_index import get_clean_index  # noqa: PLC0415
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

    stat = {"source": source, "total": len(recs), "skipped": 0,
            "written": 0, "unchanged": 0, "status_derived": 0}
    for r in recs:
        fields = fields_for(r)
        if not fields:
            # H-07 全量派生：status 恒等于 timeliness_status（未核验时同为空——不再是假判据；
            # 覆盖"未命中回写"记录，保证派生不变量全量成立）。
            _ns = r.get("timeliness_status") or ""
            if (r.get("status") or "") != _ns:
                r["status"] = _ns
                stat["status_derived"] += 1
            stat["skipped"] += 1
            continue                       # 无核验结果 / 状态歧义 → 三字段保持原值（空）
        before = tuple(r.get(k, "") for k in TARGET_FIELDS) + (r.get("status", ""),)
        for k in TARGET_FIELDS:
            r[k] = fields.get(k, "") or ""
        # H-07（2026-09-12）：status 由 timeliness_status 派生（英文）——源特异假判据
        # （gov 死分支/mof "4"/pbc "ok"/supp 中文）废弃后的统一口径落地。
        r["status"] = r.get("timeliness_status") or ""
        if (tuple(r.get(k, "") for k in TARGET_FIELDS) + (r.get("status", ""),)) != before:
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
            "cleaned_before_%s_%s" % (backup_tag, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
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
                # H-07：status 派生同步（CSV 轨，与 jsonl 一致）
                if "status" in fields:
                    rows[i]["status"] = r.get("status", "")
            tmp = cf + ".tmp"
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in rows:
                    w.writerow(row)
            os.replace(tmp, cf)
    return stat


def apply_source(source: str, by_no, by_title_date, by_title_unique,
                 dry_run: bool, no_backup: bool):
    """ledger 全量三级匹配 → 统一回写（C-12：匹配逻辑留本层，写盘经 writeback_source）。"""
    hits = {"hit_docno": 0, "hit_title_date": 0, "hit_title": 0}

    def fields_for(r):
        k_no = _norm_docno(r.get("document_number"))
        if k_no and k_no in by_no:
            hits["hit_docno"] += 1
            return by_no[k_no]
        k_ti = _norm(r.get("title"))
        if k_ti and (k_ti, _date(r.get("publish_date"))) in by_title_date:
            hits["hit_title_date"] += 1
            return by_title_date[(k_ti, _date(r.get("publish_date")))]
        if k_ti and k_ti in by_title_unique:
            hits["hit_title"] += 1
            return by_title_unique[k_ti]
        return None

    stat = writeback_source(source, fields_for, dry_run=dry_run,
                            no_backup=no_backup, backup_tag="timeliness")
    stat.update(hits)
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
    # F-C05：回写后重建 clean_index——同日期内容已变，必须刷新索引（含内容 sha），
    # 否则 classify/validate/签名与"同日改写"脱节（M-10 四重隐身组合项之一）。
    if not args.dry_run:
        total_written = sum((st.get("written") or 0) for st in stats if "reason" not in st)
        # F-C05 补：status 派生（status_derived）同样改变 cleaned 内容 → 必须触发索引重建
        # （P3B 实测：第二轮全量派生 written=0 但 status 列已变，原条件漏 rebuild → recall
        #  clean gate 索引哈希失配）。
        total_derived = sum((st.get("status_derived") or 0) for st in stats if "reason" not in st)
        if total_written > 0 or total_derived > 0:
            try:
                if ROOT not in sys.path:
                    sys.path.insert(0, ROOT)
                from clean_index import get_clean_index  # noqa: PLC0415
                get_clean_index(rebuild=True)
                print("[apply] clean_index 已重建（index.json 刷新，纳入最新内容）")
            except Exception as e:  # noqa: BLE001
                print(f"[apply] WARN clean_index 重建失败: {e!r}")
            # 2026-09-19（同 F-C05 理由，补一环）：**同步刷新条款产物**。
            # 动因：`clauses` 由 cleaned 派生，而编排链的顺序是「阶段 1 clean 尾部建 clauses
            # → 阶段 2 本步回写 cleaned」→ 不刷新则 `clauses.timeliness_status` **系统性滞后
            # 一个 apply 轮次**（实测 2026-09-19：五源 16,557 条 clauses 时效全为空，而 cleaned
            # 已有 3k+ 判定）；且发布层 `published:external` 声明 `clauses:{src}` 为依赖
            # → 其水位版本必须取到终态。增量建（按 input_sha 判定），仅受影响源重算。
            try:
                if ROOT not in sys.path:
                    sys.path.insert(0, ROOT)
                from clause_index import build_clause_index  # noqa: PLC0415
                _cr = build_clause_index()
                _built = [k for k, v in _cr.items()
                          if isinstance(v, dict) and v.get("built")]
                print(f"[apply] 条款产物已刷新（重算源: {_built or '无变更'}）")
            except Exception as e:  # noqa: BLE001
                print(f"[apply] WARN 条款产物刷新失败: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
