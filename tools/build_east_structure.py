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
import re
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
HEADER_ALIASES: dict[str, str] = {
    "数据项标识": "item_id", "数据项名称": "item_name", "数据项代码": "item_code",
    "数据项序号": "seq_all", "数据项全表序号": "seq_all", "数据项单表序号": "seq_in_table",
    "主题域": "domain", "主题域编号": "domain_code", "表名": "table_code",
    "表中文名": "table_name", "表编号": "table_no", "数据项说明": "item_desc",
    "数据元编码": "element_code", "数据元名称": "element_name", "数据元说明": "element_desc",
    "取值范围": "value_range", "数据格式": "data_format", "是否主键": "is_pk",
    "是否可空": "nullable", "是否空表": "is_empty_table", "标准默认值注释": "default_note",
    "是否变化": "changed", "变化场景": "change_scene", "升级类型": "upgrade_type",
    "升级属性": "upgrade_attr", "升级说明": "upgrade_note", "超链接": "link", "链接": "link",
    "数据表说明": "table_desc", "数据表性质": "table_nature", "描述": "item_desc",
    # 1-4 / 1-5 用到的列
    "代码": "code", "名称": "name", "说明": "desc", "序号": "no",
    "校验规则": "rule", "检核规则": "rule", "中文名称": "name",
}
CARRY_KEYS = ("table_no", "table_code", "table_name", "domain", "domain_code")
ITEM_DEFAULTS: dict[str, str] = {
    "seq_all": "", "seq_in_table": "", "item_id": "", "item_code": "", "item_name": "",
    "is_pk": "", "nullable": "", "data_format": "", "item_desc": "", "value_range": "",
    "element_code": "", "element_name": "", "changed": "", "change_scene": "",
    "upgrade_type": "", "upgrade_note": "",
}
RULE_SHEETS = {
    "A": ("条件必填校验规则-A", "条件必填"),
    "B": ("表内数据项值合理性规则-B", "表内值合理性"),
    "C": ("码表校验规则-C", "码表校验"),
    "D": ("表内数据项逻辑性校验规则-D", "表内逻辑"),
    "E": ("表间数据项关联性校验规则-E", "表间关联"),
}
#: 表单类附件（只取行×列与表头，不做结构化解析）
SHEET_LIKE = ("2", "3", "4", "5", "6")
TEXT_LIKE = ("1", "1-1", "1-2")


# ---------------------------------------------------------------- 通用 Excel
def _hkey(cell: object) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s/]+", "", str(cell).replace("\u3000", " "))


def _s(v: object) -> str:
    if v is None:
        return ""
    return str(v).replace("\r", " ").replace("\n", " ").strip()


def _map_header(row: tuple, min_hits: int = 3) -> dict[int, str] | None:
    m: dict[int, str] = {}
    for i, cell in enumerate(row):
        f = HEADER_ALIASES.get(_hkey(cell))
        if f and f not in m.values():
            m[i] = f
    return m if len(m) >= min_hits else None


def _read_sheet(ws, *, need: str = "", scan: int = 6, min_hits: int = 3):
    """→ `(表头行下标, 列映射, 数据行)`；识别不出表头返回 `(None, None, [])`。"""
    head: list[tuple] = []
    for row in ws.iter_rows(values_only=True):
        head.append(row)
        if len(head) > scan:
            break
    for idx, row in enumerate(head[:scan]):
        m = _map_header(row, min_hits=min_hits)
        if m and (not need or need in m.values()):
            body = list(ws.iter_rows(min_row=idx + 2, values_only=True))
            return idx, m, body
    return None, None, []


def _row_dict(row: tuple, cols: dict[int, str]) -> dict[str, str]:
    return {f: (_s(row[i]) if i < len(row) else "") for i, f in cols.items()}


# ---------------------------------------------------------------- 附件1-3
def parse_tables(path: str) -> dict:
    """附件1-3 → 表/数据项/数据元/格式图例。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    toc: dict[str, dict] = {}
    idx, cols, body = _read_sheet(wb["数据结构目录"], need="table_no", scan=4)
    if idx is not None:
        for row in body:
            rec = _row_dict(row, cols)
            key = rec.get("table_no") or rec.get("table_code")
            if key:
                toc[key] = rec

    elements: dict[str, dict] = {}
    if "数据元说明" in wb.sheetnames:
        i2, c2, b2 = _read_sheet(wb["数据元说明"], need="element_code")
        if i2 is not None:
            for row in b2:
                rec = _row_dict(row, c2)
                if rec.get("element_code"):
                    elements[rec["element_code"]] = rec

    legend: dict[str, str] = {}
    if "数据格式说明" in wb.sheetnames:
        i3, c3, b3 = _read_sheet(wb["数据格式说明"], need="data_format", scan=4, min_hits=2)
        if i3 is not None:
            for row in b3:
                rec = _row_dict(row, c3)
                k = rec.get("data_format", "")
                if k and k not in legend:
                    legend[k] = rec.get("item_desc", "")

    idx, cols, body = _read_sheet(wb["数据结构"], need="item_id")
    if idx is None or not cols:
        raise ValueError("附件1-3「数据结构」表头无法识别")
    tables: dict[str, dict] = {}
    ctx = dict.fromkeys(CARRY_KEYS, "")
    n_rows = n_skipped = 0
    for row in body:
        n_rows += 1
        rec = _row_dict(row, cols)
        for k in CARRY_KEYS:
            if rec.get(k):
                ctx[k] = rec[k]
        if not rec.get("item_id"):
            n_skipped += 1
            continue
        for k in CARRY_KEYS:
            if not rec.get(k):
                rec[k] = ctx.get(k, "")
        # `rec` 的值类型是 str（`_s()` 已归一），mypy 看到的是 `Any|None`：
        # 显式 `str()` 收窄；**空键跳过**（既无表编号又无表名的行不是数据项）
        key = str(rec.get("table_no") or rec.get("table_code") or "")
        if not key:
            n_skipped += 1
            continue
        tbl = tables.get(key)
        if tbl is None:
            meta = toc.get(key, {})
            tbl = {"table_no": key,
                   "table_code": rec.get("table_code") or meta.get("table_code", ""),
                   "table_name": rec.get("table_name") or meta.get("table_name", ""),
                   "domain": rec.get("domain") or meta.get("domain", ""),
                   "domain_code": rec.get("domain_code", ""),
                   "table_desc": meta.get("table_desc", ""),
                   "table_nature": meta.get("table_nature", ""),
                   "items": []}
            tables[key] = tbl
        item = {k: rec.get(k, d) or d for k, d in ITEM_DEFAULTS.items()}
        el = elements.get(item.get("element_code", ""), {})
        if el:
            item["data_format"] = item.get("data_format") or el.get("data_format", "")
            item["value_range"] = item.get("value_range") or el.get("value_range", "")
            item["element_name"] = item.get("element_name") or el.get("element_name", "")
            if not item.get("item_desc") and el.get("element_desc"):
                item["item_desc"] = el["element_desc"]
        tbl["items"].append(item)
    wb.close()
    return {
        "tables": [tables[k] for k in sorted(tables)],
        "elements": [dict(v, element_code=k) for k, v in sorted(elements.items())],
        "format_legend": legend,
        "rows_scanned": n_rows,
        "rows_skipped": n_skipped,
    }


# ---------------------------------------------------------------- 附件1-4
def parse_codes(path: str) -> list[dict]:
    """附件1-4 → 代码表全量（**保留原始列**；标准两列表另给 code/name/desc 视图）。

    见文件头「为什么代码表要保留原始列」：多级/复合表塞进两列会丢维度。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    id_by_sheet: dict[str, dict] = {}
    if "目录" in wb.sheetnames:
        i0, c0, b0 = _read_sheet(wb["目录"], need="code", scan=6)
        if i0 is not None:
            for row in b0:
                rec = _row_dict(row, c0)
                if rec.get("name"):
                    id_by_sheet[rec["name"]] = rec

    #: 代码列/名称列的关键词（覆盖 代码取值 / 工种代码 / 一级代码 / 代碼名稱 等写法）
    CODE_HINT = ("代码", "取值", "编码")
    NAME_HINT = ("名称", "含义", "说明")

    out: list[dict] = []
    for sh in wb.sheetnames:
        if sh == "目录":
            continue
        ws = wb[sh]
        # 找表头：前 6 行里第一行"非空单元格 ≥2 且含代码类词"
        header: list[str] | None = None
        for row in ws.iter_rows(max_row=6, values_only=True):
            cells = [_s(c) for c in row]
            nonempty = [c for c in cells if c]
            if len(nonempty) >= 2 and any(any(h in c for h in CODE_HINT) for c in nonempty):
                header = cells
                break
        if not header:
            continue
        width = max((i for i, c in enumerate(header) if c), default=-1) + 1
        cols = header[:width]
        rows: list[list[str]] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            cells = [_s(c) for c in row][:width]
            if not any(cells):
                continue
            if any(c in ("返回目录",) for c in cells):
                continue
            if cells[:1] and cells[0] in ("代码", "代码取值", "一级代码"):
                continue          # 重复表头
            rows.append(cells)
        norm: list[dict] = []
        ci = next((i for i, c in enumerate(cols) if any(h in c for h in CODE_HINT)), None)
        ni = next((i for i, c in enumerate(cols)
                   if any(h in c for h in NAME_HINT) and i != ci), None)
        if ci is not None and ni is not None:
            for cells in rows:
                code = cells[ci] if ci < len(cells) else ""
                name = cells[ni] if ni < len(cells) else ""
                if code:
                    norm.append({"code": code, "name": name,
                                 "desc": cells[ni + 1] if ni + 1 < len(cells) else ""})
        meta = id_by_sheet.get(sh, {})
        out.append({"sheet": sh, "id": meta.get("code", ""),
                    "name": meta.get("name", "") or sh,
                    "columns": cols, "rows": rows, "count": len(rows),
                    "normalized": len(norm) == len(rows) and bool(norm),
                    "items": norm if len(norm) == len(rows) else []})
    wb.close()
    return out


# ---------------------------------------------------------------- 附件1-5
def parse_rules(path: str) -> dict:
    """附件1-5 → 规则 A~E（含全量条目）。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, dict] = {}
    for key, (sheet, label) in RULE_SHEETS.items():
        if sheet not in wb.sheetnames:
            out[key] = {"label": label, "count": 0, "items": [], "missing": True}
            continue
        idx, cols, body = _read_sheet(wb[sheet], need="rule", scan=6, min_hits=3)
        items = []
        if idx is not None:
            for row in body:
                rec = _row_dict(row, cols)
                if not rec.get("no"):
                    continue
                items.append({"no": rec.get("no", ""), "domain": rec.get("domain", ""),
                              "table": rec.get("table_code", ""),
                              "item": rec.get("item_name", ""),
                              "code": rec.get("item_code", ""),
                              "rule": rec.get("rule", "")})
        out[key] = {"label": label, "count": len(items), "items": items}
    wb.close()
    return out


# ---------------------------------------------------------------- E-R
_RE_SRC = re.compile(r"【(?P<t>[^】]+)】\s*中的?\s*[“\"](?P<f>[^”\"]+)[”\"]")
_RE_DST = re.compile(r"须在\s*【(?P<t>[^】]+)】")


def build_er(tables: list[dict], rules: dict) -> dict:
    """表间关系：1-5-E（主）+ 1-3「关联数据项」（字段级补充）。"""
    by_name: dict[str, dict] = {}
    dup: list[str] = []
    for t in tables:
        nm = str(t.get("table_name") or "").strip()
        if not nm:
            continue
        if nm in by_name:
            dup.append(nm)
        by_name.setdefault(nm, t)

    edges: list[dict] = []
    seen: set[tuple] = set()

    def add(src_t: dict, dst_t: dict, *, field: str, field_code: str,
            rule_no: str, source: str, rule: str = "", dst_field: str = "") -> None:
        sig = (src_t["table_no"], dst_t["table_no"], field, dst_field)
        if sig in seen or src_t["table_no"] == dst_t["table_no"]:
            return
        seen.add(sig)
        edges.append({"from": src_t["table_no"], "to": dst_t["table_no"],
                      "from_table": src_t.get("table_name", ""),
                      "to_table": dst_t.get("table_name", ""),
                      "field": field, "field_code": field_code,
                      "dst_field": dst_field, "rule_no": rule_no,
                      "source": source, "rule": rule[:200]})

    # ① 1-5-E：`【源表】中的“字段”，须在【目标表】中存在`
    for it in (rules.get("E") or {}).get("items") or []:
        txt = str(it.get("rule") or "")
        src_m = _RE_SRC.search(txt)
        dst_m = _RE_DST.search(txt)
        src_name = src_m.group("t") if src_m else str(it.get("table") or "")
        field = src_m.group("f") if src_m else str(it.get("item") or "")
        dst_name = dst_m.group("t") if dst_m else ""
        s_t, d_t = by_name.get(src_name), by_name.get(dst_name)
        if s_t and d_t:
            add(s_t, d_t, field=field, field_code=str(it.get("code") or ""),
                rule_no=str(it.get("no") or ""), source="1-5E", rule=txt)

    # ② 附件1-3 数据项说明里的「关联数据项：表-项」
    n_rel = 0
    for t in tables:
        for it in t.get("items") or []:
            desc = str(it.get("item_desc") or "")
            if "关联数据项" not in desc:
                continue
            tail = desc.split("关联数据项", 1)[1].lstrip("：: ")
            tail = re.split(r"[。；;]", tail, maxsplit=1)[0]
            for part in re.split(r"[、，,]", tail):
                part = part.strip()
                if "-" not in part and "－" not in part:
                    continue
                nm, fld = re.split(r"[-－]", part, maxsplit=1)
                d_t = by_name.get(nm.strip())
                if not d_t:
                    continue
                n_rel += 1
                add(t, d_t, field=str(it.get("item_name") or ""),
                    field_code=str(it.get("item_code") or ""),
                    rule_no=f"1-3:{it.get('item_id', '')}", source="1-3关联",
                    dst_field=fld.strip())

    deg: collections.Counter = collections.Counter()
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    nodes = [{"id": t["table_no"], "code": t.get("table_code", ""),
              "name": t.get("table_name", ""), "domain": t.get("domain", ""),
              "degree": deg.get(t["table_no"], 0), "items": len(t.get("items") or [])}
             for t in tables]
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {"nodes": len(nodes), "linked_nodes": sum(1 for n in nodes if n["degree"]),
                  "edges": len(edges),
                  "by_source": dict(collections.Counter(e["source"] for e in edges)),
                  "dup_table_names": dup[:5]},
        "note": ("关系来自两处机器可读证据：附件1-5 的「表间数据项关联性校验规则」"
                 "（表→表，320 条）与附件1-3 数据项说明里的「关联数据项」"
                 "（字段级）。附件1-6 的 E-R 图是**官方示意图**，已在附件区提供原图，"
                 "本图不试图还原它的排版。"),
    }


# ---------------------------------------------------------------- 文本/表单类附件
def describe_text(att: dict, text: str) -> dict:
    """正文类附件：只记规模与首段（全文留在事实源，页面走检索）。

    ⚠️ 首段必须**截断到 160 字**：`.doc` 经转换后常是"一整行 300 万字符"，
    原样放进产物会让结构件从 <1MB 涨到 7MB（实测 6997KB 全在这一处）。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return {"chars": len(text or ""), "lines": len(lines),
            "head": [ln[:160] for ln in lines[:3]],
            "headings": _headings(lines)[:12]}


def _headings(lines: list[str]) -> list[str]:
    """粗提标题行（短、无句号、可能带编号）——用于页面"目录"预览。"""
    out = []
    for ln in lines:
        s = ln.strip()
        if not (2 <= len(s) <= 40):
            continue
        if s.endswith(("。", "；", "，", ":", "：")):
            continue
        if re.match(r"^(第[一二三四五六七八九十百]+[章节条]|[0-9]+(\.[0-9]+)*\s|附件[0-9])", s):
            out.append(s)
        elif len(s) <= 18 and re.search(r"(说明|概述|要求|接口|规范|报送|流程|规则)$", s):
            out.append(s)
    return out


def describe_sheet(att: dict, path: str) -> dict:
    """表单类附件：行×列 + 表头（不解析业务）。"""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sh = wb.sheetnames[0]
        ws = wb[sh]
        head = []
        for row in ws.iter_rows(max_row=3, values_only=True):
            head = [("" if c is None else str(c).strip())[:40] for c in row if c is not None]
            if head:
                break
        info = {"sheet": sh, "rows": ws.max_row, "cols": ws.max_column, "head": head[:12]}
        wb.close()
        return info
    except Exception as e:  # noqa: BLE001  表单类只是"看得见有个表"，失败不该阻断
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


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
                        "count": c["count"], "columns": c["columns"],
                        "normalized": c["normalized"]} for c in codes],
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
