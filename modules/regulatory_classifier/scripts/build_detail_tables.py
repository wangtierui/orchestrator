# -*- coding: utf-8 -*-
"""
build_detail_tables.py —— 10 份「逐份条款引用与上位法依据明细表」全量生成/校验（③ 代码化）

背景（2026-08-29 审查记录，原「已记录未改数据」）：
  明细表此前**仓库内无全量生成脚本**（sync_supp_latest.py 仅覆盖 T1；align_artifacts.py
  仅做缺失补齐）。本脚本确立其可复现能力：以 归属表（唯一权威）+ clean_index 最新快照为源，
  对 T1–T10 全主题重建/补齐明细表，并**强制「正文状态」4 值口径**（完整/摘要/核心要点/无正文）。

字段口径（与既有 12 列明细表一致）：
  监管文件编号 / seq / 标题 / 发文字号 / 子主题 / 来源标记 / 文件来源 / 正文状态 /
  立法依据 / 条款引用 / 来源类型 / 备注

正文状态 4 值计算规则（② 收敛口径，2026-08-29）：
  - 正文长度 ≥200 → 完整（原 完整(≥200字) / 有正文（…官网全文） / supp库正文）
  - 0 < 正文长度 < 200 → 摘要（原 摘要(<200字) / 短(N字) 全部）
  - 正文为空 → 无正文（原 无正文 / 无正文（法律，官方发布） / 无正文（国务院文件，官方发布））
  - 核心要点：**保留既有标注**（人工判定，不可由正文长度推导）

数据纪律：
  - cleaned 一律经 clean_index.get_clean_index().latest_csv_path(src) 动态取最新（禁硬编码快照日期）；
  - 既有行的 子主题/来源标记/立法依据/条款引用/核心要点 一律**保留**（不改写历史叙述），
    仅对缺失行按正文自动抽取（备注标注「自动抽取」）；
  - 原子写 + 写入前备份 + 默认校验模式（--apply 才写入）。

用法：
  python scripts/build_detail_tables.py                # 校验模式：报告 覆盖缺口 + 口径收敛数
  python scripts/build_detail_tables.py --apply        # 重建（补齐缺失行 + 收敛正文状态 4 值）
  python scripts/build_detail_tables.py --theme T3     # 仅单主题
"""
import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime

csv.field_size_limit(10 ** 9)  # cleaned body_text 超默认字段上限
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # modules/regulatory_classifier/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from rfn import THEME_MAP  # noqa: E402  单一事实源：主题码->完整主题名

# P4（2026-09-08）：scrapers 模块同仓相对解析（env 覆盖保留；默认不再盘符）
SCRAPERS_ROOT = os.environ.get("REG_SCRAPERS_ROOT",
                               os.path.join(os.path.dirname(ROOT), "regulatory_scrapers"))
if SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, SCRAPERS_ROOT)
_ORCH_ROOT = os.path.dirname(os.path.dirname(SCRAPERS_ROOT))  # orchestrator 根（供 std_lib）
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)

from clean_index import get_clean_index  # noqa: E402

from std_lib.common_lib import fs_lock  # noqa: E402  (旧 `import fs_lock` 语义收口至共享库)

ATTR_CSV = os.path.join(ROOT, "data", "人身保险公司-文件归属表.csv")
DATA = os.path.join(ROOT, "data")
RFN_RE = re.compile(r"^RFN-[0-9a-f]{16}$")
THEMES = ["T0"] + [f"T{i}" for i in range(1, 11)]

# 正文状态 4 值口径（② 收敛）
BODY_FULL, BODY_SUMMARY, BODY_CORE, BODY_NONE = "完整", "摘要", "核心要点", "无正文"
BODY_ENUM = {BODY_FULL, BODY_SUMMARY, BODY_CORE, BODY_NONE}

# 2026-08-31 schema 变更：删除「来源标记」「来源类型」两列（0 消费端 + 与 文件来源/正文状态 冗余）
# R10 provenance（2026-09-08）：明细展示态追加 generated_by/generated_at 血缘列。
# 列契约唯一事实源 = interfaces/contract.DETAIL_TABLE_FIELDS（gate_contract 同读，防字面漂移）
from interfaces.contract import DETAIL_TABLE_FIELDS  # noqa: E402

FIELDS: list[str] = DETAIL_TABLE_FIELDS

from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）
from std_lib.common_lib.norm import norm_title_strict as _norm_title  # A-10：SSOT 收敛（保守层）

# R-F01 收敛（2026-09-14）：条款链 / 书名号跨度 / 立法词表上收 std_lib.common_lib.relations
# （原为本文件字面量，与 build_clause_graph.P1/ART_IN、verify.TITLE_PAT 三份重复，口径靠人眼对齐）。
# 语义**不变**：跨度 2..40、条款链 `第N条[第M款][第K项]`、立法结尾词表同原值。
from std_lib.common_lib.relations import (
    ARTICLE_CHAIN_CAPTURE,
    LAW_SUFFIX_ALT,
    QUOTE_TITLE_MAX,
    QUOTE_TITLE_MIN,
    quote_title_capture,
)

# 放宽版条款正则（提升条/款/项级召回）：允许《X》与「第N条」之间最多 12 字间隔，
# 覆盖「《X》第N条」「《X》的规定第N条」「《X》中第N条第M款」等；不含裸「第N条」以防自条款噪声。
ART_RE = re.compile(quote_title_capture() + r"[^。；\n]{0,12}?" + ARTICLE_CHAIN_CAPTURE)
LAW_RE = re.compile(
    rf"《([^《》]{{{QUOTE_TITLE_MIN},{QUOTE_TITLE_MAX}}}(?:{LAW_SUFFIX_ALT}))》")


def load_attr():
    """读文件归属表（权威）+ 主题归属表（主题列），合并为带主题的行。"""
    rows = list(csv.DictReader(open(ATTR_CSV, encoding="utf-8-sig", newline="")))
    tmap = {}
    tcsv = os.path.join(DATA, "人身保险公司-主题归属表.csv")
    if os.path.exists(tcsv):
        for r in csv.DictReader(open(tcsv, encoding="utf-8-sig", newline="")):
            tmap[r.get("监管文件编号", "")] = r.get("主题", "")
    for r in rows:
        r["主题"] = tmap.get(r.get("监管文件编号", ""), "")
    return rows


def load_cleaned():
    """(source, docno|title) -> row（clean_index 最新快照，SSOT，无硬编码日期）。"""
    idx = get_clean_index()
    cleaned = {}
    for src in ("gov", "mof", "nfra", "pbc", "supp"):
        p = idx.latest_csv_path(src)
        if not p or not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                nd = _norm_docno(r.get("document_number", ""))
                nt = _norm_title(r.get("title", ""))
                if nd:
                    cleaned[(src, "D:" + nd)] = r
                if nt:
                    cleaned.setdefault((src, "T:" + nt), r)
    return cleaned


def get_body(row):
    if not row:
        return ""
    return (row.get("body_text") or row.get("body_text_webpage")
            or row.get("body_text_doc") or row.get("summary") or "")


def body_status(body):
    n = len(body or "")
    if n >= 200:
        return BODY_FULL
    if n > 0:
        return BODY_SUMMARY
    return BODY_NONE


def extract_refs(body):
    arts = Counter()
    for name, art, para, item in ART_RE.findall(body or ""):
        label = f"《{name}》第{art}条"
        if para:
            label += f"第{para}款"
        if item:
            label += f"第{item}项"
        arts[label] += 1
    art_str = "；".join(f"{k}x{v}" for k, v in sorted(arts.items(), key=lambda x: (-x[1], x[0])))
    basis, seen = [], set()
    for nm in LAW_RE.findall(body or ""):
        if nm not in seen:
            seen.add(nm)
            basis.append(nm)
    return art_str, basis


def load_final_clusters():
    """final.json (RFN -> cluster)，供缺失行 子主题 兜底。"""
    out = {}
    for f in os.listdir(DATA):
        if not re.match(r"^_(?:t)?(?:[1-9]|10)(?:_\d+)?_final\.json$", f):
            continue
        for r in json.load(open(os.path.join(DATA, f), encoding="utf-8")):
            if r.get("监管文件编号"):
                out[r["监管文件编号"]] = r.get("cluster", "")
    return out


def load_existing():
    """{rfn: row}（全局按 RFN 唯一去重）+ {theme: (path, fieldnames)}。
    既有的『逐份条款引用』明细表按 RFN 前缀归组；重分类后 主题列 可能与 RFN 前缀不一致，
    故按全局 RFN 查表以保留迁移行的子主题/立法依据/条款引用，避免回退『待分类』。"""
    rows, paths = {}, {}
    for f in os.listdir(DATA):
        m = re.match(r"^(T0|T[1-9]|T10)_(\d+)逐份条款引用与上位法依据明细表\.csv$", f)
        if not m:
            continue
        t = m.group(1)
        with open(os.path.join(DATA, f), encoding="utf-8-sig", newline="") as fh:
            rd = csv.DictReader(fh)
            fn = rd.fieldnames
            for r in rd:
                rows[r["监管文件编号"]] = r   # RFN 全局唯一，跨主题查表
        paths[t] = (os.path.join(DATA, f), fn)
    return rows, paths


def build_theme_rows(theme, attr_rows, cleaned, existing, cluster_map):
    """按归属表『主题列』归组构造明细表行（主题列=权威口径，覆盖 RFN 前缀≠主题列的 248 行）：
    既有行按全局 RFN 查表保留（子主题/立法依据/条款引用不变），仅缺失行自动补齐。"""
    out = []
    target_theme = THEME_MAP[theme]
    # R10：血缘列本批次写者/时间（全行同批）
    gb, g_at = "build_detail_tables", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for a in attr_rows:
        if (a.get("主题") or "").strip() != target_theme:
            continue
        rfn = (a.get("监管文件编号") or "").strip()
        old = existing.get(rfn)            # 全局 RFN 查表（跨主题保留既有子主题）
        src = (a.get("文件来源") or "").strip()
        if old is not None:
            row = dict(old)
            st = (row.get("正文状态") or "").strip()
            _c = cleaned.get((src, "D:" + _norm_docno(a.get("发文字号")))) \
                or cleaned.get((src, "T:" + _norm_title(a.get("文件名称"))))
            if st not in BODY_ENUM:
                row["正文状态"] = body_status(get_body(_c))
            # F-L03（2026-09-12）：法宝核验标记联入——时效状态/核验来源按 clean 权威字段投影刷新
            row["时效状态"] = (_c or {}).get("timeliness_status", "") or row.get("时效状态", "")
            row["核验来源"] = (_c or {}).get("verification_source", "") or row.get("核验来源", "")
            row["监管文件编号"], row["主题"] = rfn, target_theme
            # R12（2026-09-08）：叙述列（标题/发文字号/文件来源）按归属表权威快照投影刷新，
            # 防归属表改标题/文号后明细表保留旧叙述（"只补缺不刷"陈旧）；人工列（立法依据/条款引用/备注）保留。
            row["标题"] = a.get("文件名称", "")
            row["发文字号"] = a.get("发文字号", "")
            row["文件来源"] = src
            # F-D03：子主题按 final（cluster_map）刷新——原 old 分支仅刷新叙述列，子主题
            # 停留旧值（实测 T1 明细 S5消保/信息 vs final S3 分裂）。
            row["子主题"] = cluster_map.get(rfn, row.get("子主题", ""))
            row["generated_by"], row["generated_at"] = gb, g_at
            out.append(row)
            continue
        c = cleaned.get((src, "D:" + _norm_docno(a.get("发文字号")))) \
            or cleaned.get((src, "T:" + _norm_title(a.get("文件名称"))))
        body = get_body(c)
        art_str, basis = extract_refs(body)
        out.append({
            "监管文件编号": rfn, "主题": target_theme, "标题": a.get("文件名称", ""),
            "发文字号": a.get("发文字号", ""), "文件来源": src, "正文状态": body_status(body),
            # F-L03（2026-09-12）：法宝核验标记联入（clean 权威字段）
            "时效状态": (c or {}).get("timeliness_status", ""),
            "核验来源": (c or {}).get("verification_source", ""),
            "立法依据": "；".join(basis), "条款引用": art_str,
            "备注": "2026-08-31 build_detail_tables 补齐|条款/依据自动抽取(需复核)",
            "子主题": cluster_map.get(rfn, ""),
            "generated_by": gb, "generated_at": g_at,
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="明细表全量生成/校验（③）")
    ap.add_argument("--apply", action="store_true", help="重建并写入（默认仅校验）")
    ap.add_argument("--theme", choices=THEMES, default="", help="仅处理指定主题")
    args = ap.parse_args()

    attr = load_attr()
    cleaned = load_cleaned()
    existing, paths = load_existing()
    clusters = load_final_clusters()

    themes = [args.theme] if args.theme else THEMES
    n_missing = n_converge = n_written = 0
    for t in themes:
        rows = build_theme_rows(t, attr, cleaned, existing, clusters)
        missing = [r for r in rows if r["监管文件编号"] not in existing]
        converge = [r for r in rows if r["监管文件编号"] in existing
                    and (r.get("正文状态") or "").strip() not in BODY_ENUM]
        n_missing += len(missing)
        n_converge += len(converge)
        fname = f"{t}_{len(rows)}逐份条款引用与上位法依据明细表.csv"
        print(f"  {t}: {len(rows)} 行 | 缺失补齐 {len(missing)} | 口径收敛 {len(converge)} -> {fname}")

        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(ROOT, "backups", f"detail_tables_{stamp}")
            os.makedirs(bak, exist_ok=True)
            oldp = paths.get(t, (None, None))[0]
            if oldp and os.path.exists(oldp):
                shutil.copy2(oldp, os.path.join(bak, os.path.basename(oldp)))
            sio = io.StringIO()
            w = csv.DictWriter(sio, fieldnames=FIELDS)
            w.writeheader()
            # 2026-09-03 兼容修复：既有明细表含历史附加列（子主题NEW/子主题分等），
            # 写回前投影至 FIELDS 权威列，避免 DictWriter 报 "fields not in fieldnames"。
            w.writerows([{k: r.get(k) for k in FIELDS} for r in rows])
            newp = os.path.join(DATA, fname)
            fs_lock.atomic_write_text(newp, "\ufeff" + sio.getvalue(), encoding="utf-8")
            # 旧命名（条数变化）清理：仅删除同主题旧名文件（已在备份中）
            if oldp and oldp != newp and os.path.exists(oldp):
                os.remove(oldp)
            n_written += 1
            print(f"    已写入 {fname}（备份→{os.path.basename(bak)}/）")

    mode = "已写入" if args.apply else "校验（--apply 写入）"
    print(f"\n合计：缺失补齐 {n_missing} 行 | 口径收敛 {n_converge} 行 | {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
