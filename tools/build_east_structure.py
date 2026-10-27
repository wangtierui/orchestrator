# -*- coding: utf-8 -*-
"""tools/build_east_structure.py — EAST 规范**全附件**结构化产物（v2.0）。

产物：`data/east/east_structure.json`

## 数据从哪来（单一事实源，不重复造轮子）
附件清单与原文路径**早就在采集事实源里**：`supplementary_regulations.jsonl` 的
`attachments[]`（`name / local_path / sha256 / bytes / attachment_no / text`）。
本工具一律经 `interfaces/attachment_api` 按 `attachment_no` 取文件，
**不在目录里 glob**、不自己判断"哪个文件是最新版"——
那正是"同一批文件在仓里存多份、改一边漏一边"的来源。

## 覆盖哪些附件（2026-10-26 扩到全部）
| 附件 | 内容 | 本工具产出 |
|---|---|---|
| 附件1 | 规范全文（.doc，300 万字符） | 只记**规模与首段**（正文在事实源里，页面走检索，不复制） |
| 附件1-1 | 报送说明（.docx） | 同上 |
| 附件1-2 | 采集技术接口说明（.docx） | 同上 |
| 附件1-3 | 数据结构一览表（.xlsx） | **数据表 112 / 数据项 3062 / 数据元 308 / 格式图例** |
| 附件1-4 | 业务代码表（.xlsx，66 sheet） | **代码表 65 张 / 代码项全量** |
| 附件1-5 | 数据检核规则（.xlsx，5 sheet） | **规则 A~E 全量**（A 条件必填 / B 值域 / C 码表 / D 表内逻辑 / E 表间关联） |
| 附件1-6 | E-R 图（.jpg） | 官方图元数据（尺寸/大小），**边由 1-5-E 与 1-3 关联数据项生成** |
| 附件2/3/4/5/6 | 报送清单/检核结果/备案表/更正表/联系人 | 表单类元数据（行×列、表头） |

## E-R 图怎么来（机器可读，不靠读图）
1. **附件1-5 sheet E「表间数据项关联性校验规则」**：320 条，规则文本是
   `【源表】中的“字段”，须在【目标表】中存在` —— 天然是"表 → 表"的边；
2. **附件1-3 数据项说明里的「关联数据项：表-项」**：字段级边（更细，用于展示字段对）。
两路合并，节点为数据表（用 `表编号` 作 id），边带 `source` 以便读者分辨来源。

## 用法
    python -m tools.build_east_structure                # 生成（默认文号见 NOTICE_DOCNO）
    python -m tools.build_east_structure --docno "…"     # 换文号
    python -m tools.build_east_structure --check         # 校验产物结构
    python -m tools.build_east_structure --dry-run       # 只统计
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import sys

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

OUT_DIR = os.path.join(paths.DATA_DIR, "east")
OUT_JSON = os.path.join(OUT_DIR, "east_structure.json")
OUT_CODES = os.path.join(OUT_DIR, "east_codes.json")
OUT_ITEMS = os.path.join(OUT_DIR, "east_items.json")
OUT_RULES = os.path.join(OUT_DIR, "east_rules.json")
SCHEMA_VERSION = "2.0"

#: 默认文号（EAST 2024 版规范的修订通知）
NOTICE_DOCNO = "金非银检函〔2025〕82号"

#: 表头归一化后 → 字段名（1-3 的两种版式共用；新增列在此加一行）

#: 正文类附件（只给规模+标题预览）与表单类附件（只取行×列与表头）
TEXT_LIKE = ("1", "1-1", "1-2")
SHEET_LIKE = ("2", "3", "4", "5", "6")

# ---- 解析层 re-export（既有调用方 `from tools.build_east_structure import
#      parse_tables/parse_codes/...` 保持不变；实现已迁到 tools/east_parse.py）----
from tools.east_parse import (  # noqa: F401
    CARRY_KEYS,
    CODE_HINTS,
    CODE_ID_RE,
    CODE_NAME_RE,
    DESC_HINTS,
    DROP_COL_HINTS,
    ENUM_PREFIX,
    HEADER_ALIASES,
    ITEM_DEFAULTS,
    NAME_HINTS,
    RULE_SHEETS,
    TOC_ALIASES,
    _code_ref,
    _code_sheet,
    _headings,
    _hkey,
    _map_header,
    _match_toc,
    _norm_code_name,
    _read_code_toc,
    _read_sheet,
    _row_dict,
    _s,
    _strip_nav,
    build_er,
    describe_sheet,
    describe_text,
    parse_codes,
    parse_rules,
    parse_tables,
)


# ---------------------------------------------------------------- 主流程
def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(docno: str = NOTICE_DOCNO) -> dict:
    """组装产物（纯函数；`--check` 与守卫都复用它）。"""
    from interfaces import attachment_api as ATT

    d = ATT.attachments_for_docno(docno, with_text=True)
    if not d.get("available"):
        raise FileNotFoundError(d.get("reason") or f"未找到 {docno}")
    items = {str(it["no"]): it for it in d["items"]}

    def pick(no: str) -> dict:
        it = items.get(no)
        if not it:
            raise FileNotFoundError(f"{docno} 缺少附件{no}")
        return it

    def _first_file(no: str) -> str:
        """附件号 → 首个物理文件（缺附件时抛错：**早失败**胜过静默降级）。"""
        hits = ATT.attachment_file(docno, no)
        if not hits:
            raise FileNotFoundError(f"{docno} 缺少附件{no}")
        return hits[0]

    pick("1-3")   # 断言附件1-3 存在（缺失即抛，早失败）
    parsed = parse_tables(_first_file("1-3"))
    codes = parse_codes(_first_file("1-4")) if "1-4" in items else []
    rules = parse_rules(_first_file("1-5")) if "1-5" in items else {}
    er = build_er(parsed["tables"], rules)

    docs = []
    for no in TEXT_LIKE:
        if no not in items:
            continue
        att = items[no]
        docs.append({"no": no, "name": att["name"], "kind": att["kind"],
                     "text_len": att["text_len"],
                     **describe_text(att, att.get("text") or "")})
    sheets = []
    for no in SHEET_LIKE:
        if no not in items:
            continue
        att = items[no]
        sheets.append({"no": no, "name": att["name"], "kind": att["kind"],
                       "text_len": att["text_len"],
                       **describe_sheet(att, _first_file(no))})

    att_list = [{"no": it["no"], "name": it["name"], "kind": it["kind"],
                 "bytes": it["bytes"], "sha256": (it["sha256"] or "")[:16],
                 "text_len": it["text_len"], "extract_status": it["extract_status"],
                 "local_path": it["local_path"]}
                for it in d["items"]]

    items_total = sum(len(t["items"]) for t in parsed["tables"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generated_by": "tools/build_east_structure.py",
        "notice": {"docno": d.get("docno", ""), "title": d.get("title", ""),
                   "publish_date": d.get("publish_date", ""),
                   "issue_organ": d.get("issue_organ", "")},
        "source": {"attachment_root": "modules/regulatory_scrapers/data/docs/"
                                      "supplementary_regulations_scraper",
                   "index": "modules/regulatory_scrapers/data/raw/"
                            "supplementary_regulations.jsonl",
                   "attachments": att_list},
        "stats": {
            "attachments": len(att_list),
            "tables": len(parsed["tables"]), "items": items_total,
            "pk_items": sum(1 for t in parsed["tables"] for i in t["items"] if i["is_pk"] == "Y"),
            "with_format": sum(1 for t in parsed["tables"] for i in t["items"]
                               if i.get("data_format")),
            "elements": len(parsed["elements"]),
            "code_tables": len(codes), "codes": sum(c["count"] for c in codes),
            "rules": sum(v["count"] for v in rules.values()),
            "rules_by_kind": {k: v["count"] for k, v in rules.items()},
            "er_nodes": er["stats"]["nodes"], "er_edges": er["stats"]["edges"],
            "rows_scanned": parsed["rows_scanned"],
            "rows_skipped": parsed["rows_skipped"],
            "by_domain": dict(collections.Counter(
                t["domain"] for t in parsed["tables"]).most_common()),
        },
        "format_legend": parsed["format_legend"],
        "elements": parsed["elements"],
        "tables": [{k: v for k, v in tb.items() if k != "items"} |
                   {"item_count": len(tb["items"]),
                    "pk_count": sum(1 for i in tb["items"] if i.get("is_pk") == "Y")}
                   for tb in parsed["tables"]],
        "items_by_table": {tb["table_no"]: tb["items"] for tb in parsed["tables"]},
        "rules_full": rules,      # 落到 east_rules.json；结构件只留 rule_index
        "rule_index": {k: {"label": v["label"], "count": v["count"]} for k, v in rules.items()},
        # 注意：**不**在这里放 `rules`（全量在 east_rules.json）。
        # 首版同时给了 rules + rules_full，结构件因此多出 451KB 纯冗余。
        "code_index": [{"sheet": c["sheet"], "id": c["id"], "name": c["name"],
                        "toc_name": c.get("toc_name", ""),
                        "count": c["count"], "columns": c["columns"],
                        "hierarchy": c["hierarchy"],
                        "upgrade_type": c.get("upgrade_type", "")} for c in codes],
        "codes_full": codes,
        "er": er,
        "docs": docs,
        "sheets": sheets,
    }


def check(data: dict) -> list[str]:
    """产物结构不变量（`--check` 与守卫共用）。"""
    bad: list[str] = []
    st = data.get("stats") or {}
    tables = data.get("tables") or []
    if not tables:
        return ["tables 为空"]
    if len(tables) != int(st.get("tables") or -1):
        bad.append("stats.tables 与 tables 不一致")
    total = sum(int(t.get("item_count") or 0) for t in tables)
    if total != int(st.get("items") or -1):
        bad.append("stats.items 与 table_index 不一致")
    nos = [t.get("table_no") for t in tables]
    if len(nos) != len(set(nos)):
        bad.append("表编号重复")
    for t in tables:
        if not t.get("item_count"):
            bad.append(f"表 {t.get('table_no')} 无数据项")
            break
        if not t.get("table_code") or not t.get("table_name"):
            bad.append(f"表 {t.get('table_no')} 缺表名")
            break
    if not data.get("format_legend"):
        bad.append("缺数据格式图例")
    if not data.get("elements"):
        bad.append("缺数据元字典")
    codes = data.get("code_index") or []
    if len(codes) != int(st.get("code_tables") or -1):
        bad.append("stats.code_tables 与 code_index 不一致")
    if sum(c.get("count") or 0 for c in codes) != int(st.get("codes") or -1):
        bad.append("stats.codes 与 code_index 不一致")
    for c in codes:
        if not c.get("columns"):
            bad.append(f"代码表 {c.get('sheet')} 缺列定义")
            break
    rules = data.get("rule_index") or {}
    for k in RULE_SHEETS:
        if k not in rules:
            bad.append(f"缺规则类别 {k}")
        elif not rules[k].get("count"):
            bad.append(f"规则 {k} 无条目")
    er = data.get("er") or {}
    es = (er.get("stats") or {})
    if len(er.get("edges") or []) != int(es.get("edges") or -1):
        bad.append("er.stats.edges 与明细不一致")
    node_ids = {n["id"] for n in er.get("nodes") or []}
    orphan = [e for e in (er.get("edges") or [])
              if e["from"] not in node_ids or e["to"] not in node_ids]
    if orphan:
        bad.append(f"{len(orphan)} 条 E-R 边指向不存在的节点")
    if not any(e.get("source") == "1-5E" for e in (er.get("edges") or [])):
        bad.append("E-R 缺 1-5-E 来源的边（表间关联规则未解析出来）")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_east_structure",
                                 description="EAST 全附件结构化产物")
    ap.add_argument("--docno", default=NOTICE_DOCNO, help=f"文号（默认 {NOTICE_DOCNO}）")
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--codes-out", default=OUT_CODES, help=f"代码表全量（默认 {OUT_CODES}）")
    ap.add_argument("--items-out", default=OUT_ITEMS, help=f"数据项全量（默认 {OUT_ITEMS}）")
    ap.add_argument("--rules-out", default=OUT_RULES, help=f"规则全量（默认 {OUT_RULES}）")
    ap.add_argument("--check", action="store_true", help="校验已落盘产物")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.check:
        if not os.path.exists(a.out):
            print(f"[east] 产物不存在：{a.out}")
            return ExitCode.FAIL
        data = json.load(open(a.out, encoding="utf-8"))
        bad = check(data)
        if bad:
            print("[east] 校验失败：")
            for b in bad[:12]:
                print("  -", b)
            return ExitCode.FAIL
        st = data["stats"]
        print(f"[east] 校验通过：{st['attachments']} 个附件 / {st['tables']} 表 / "
              f"{st['items']} 项 / {st['code_tables']} 代码表({st['codes']} 项) / "
              f"规则 {st['rules']} / E-R {st['er_nodes']} 节点 {st['er_edges']} 边")
        return ExitCode.OK

    data = collect(a.docno)
    bad = check(data)
    st = data["stats"]
    print(f"[east] 通知：{data['notice']['docno']} {data['notice']['publish_date']}")
    print(f"[east] 附件 {st['attachments']} 个｜表 {st['tables']} / 项 {st['items']}"
          f"（主键 {st['pk_items']}，带格式 {st['with_format']}）｜数据元 {st['elements']}")
    print(f"[east] 代码表 {st['code_tables']} 张 / {st['codes']} 项｜"
          f"规则 {st['rules']} 条 {st['rules_by_kind']}")
    print(f"[east] E-R：{st['er_nodes']} 节点（有边 {data['er']['stats']['linked_nodes']}）"
          f" / {st['er_edges']} 边 {data['er']['stats']['by_source']}")
    if bad:
        print("[east][warn] 自检问题：")
        for b in bad[:8]:
            print("  -", b)
    if a.dry_run:
        print("[east] dry-run：不落盘")
        return ExitCode.OK
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    codes_full = data.pop("codes_full", [])
    items_by_table = data.pop("items_by_table", {})
    rules_full = data.pop("rules_full", {})
    for path, payload in (
        (a.out, data),
        (a.items_out, {"generated_at": data["generated_at"],
                       "notice_docno": data["notice"]["docno"],
                       "tables": items_by_table}),
        (a.rules_out, {"generated_at": data["generated_at"],
                       "notice_docno": data["notice"]["docno"],
                       "kinds": rules_full}),
        (a.codes_out, {"generated_at": data["generated_at"],
                       "notice_docno": data["notice"]["docno"],
                       "count": len(codes_full), "tables": codes_full}),
    ):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"[east] 已写入 {path}（{os.path.getsize(path)/1024:.0f}KB）")
    return ExitCode.OK if not bad else ExitCode.FAIL


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
