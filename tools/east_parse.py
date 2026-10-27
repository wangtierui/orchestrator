# -*- coding: utf-8 -*-
"""tools.east_parse — EAST 附件解析（1-3 数据结构 / 1-4 代码表 / 1-5 规则 / E-R）。

**纯函数、无落盘、无 CLI**：进 xlsx 路径，出 dict。编排（取附件 → 落四件 → 自检）
在 `tools/build_east_structure.py`。

为什么单独成文件（2026-10-26）：这一层是**会随总局改版而变**的部分（列名、版式、
目录与工作表名不一致…），而编排很稳定；混在 900 行里每次改版都要重新找位置，
且触发单文件预算门禁（J11）。
"""
from __future__ import annotations

import collections
import re

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
    "element_code": "", "element_name": "", "element_desc": "",
    "changed": "", "change_scene": "",
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

#: 「数据元说明」取值范围里的**代码表引用**（2026-10-26 实测的唯一形态）
#:
#:    参照《业务代码表》“保险机构代码”，编号CD000002。
#:    枚举值：1；0。“1”代表是，“0”代表否。      ← 内联枚举，不是代码表
#:
#: 为什么在**抽取层**解析而不是页面里找：页面拿不到"这张代码表存不存在"的事实，
#: 只能猜字符串；抽取层有 63 张表的 id，能给出**可点击且必然存在**的引用。
#: 代码表编号（**稳定标识**：`CD` + 6 位数字）
CODE_ID_RE = re.compile(r"(?P<id>CD\d{6})")
#: 引号内的代码表名（中文引号/英文引号都收）
CODE_NAME_RE = re.compile(r"[“\"](?P<name>[^”\"]{2,40})[”\"]")
#: 内联枚举前缀（说明"值就在本条里"，无需跳转）
ENUM_PREFIX = ("枚举值",)

#: 附件1-4「目录」表的列名（**与 1-3 的别名表分开**：这里的"名称"指代码表名）
TOC_ALIASES: dict[str, str] = {
    "编号": "id",
    "代码表编号": "id",
    "中文名称": "name",
    "名称": "name",
    "升级类型": "upgrade_type",
    "升级说明": "upgrade_note",
}

#: 代码表 sheet 的列角色词（代码/名称/说明三类；其余具名列原样保留）
CODE_HINTS = ("代码", "取值", "编码")
NAME_HINTS = ("名称", "含义", "中文名称")
DESC_HINTS = ("说明", "描述", "备注", "释义")
#: **必须剔除的无效列**（xlsm 里每行都带的"返回目录"超链接列）
DROP_COL_HINTS = ("返回目录", "返回目錄")


# ---------------------------------------------------------------- 通用 Excel
def _hkey(cell: object) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s/]+", "", str(cell).replace("\u3000", " "))


def _code_ref(text: str) -> dict | None:
    """取值范围 → 业务代码表引用 `{id, name}`（无引用返回 None）。

    判据：**先认 `CDxxxxxx`**，再取它前面最近的引号内容作名称。这样下列同义写法
    都能命中（它们此前全部漏掉 —— 用户报 CD000027/34/35 点不开就是这原因）：

    ```
    参照《业务代码表》“保险机构代码”，编号CD000002。
    参照《业务代码表》“交易市场类型代码”，CD000027。      ← 省略"编号"
    参考《业务代码表》中的“人际关系代码”，编号CD000049。    ← "参考"+"中的"
    对应《业务代码表》中的“伤残等级代码”，编号CD000035。    ← "对应"
    ```

    误报防线：文本里**没有**"代码表"且名称不以"代码"结尾时，不认 ——
    正文里偶然出现的 CD 编号（如内部流水）不该变成跳转。
    `枚举值：…` 一律不认（值就在本条里，给了链接反而误导）。
    """
    s = str(text or "")
    if not s or any(s.strip().startswith(p) for p in ENUM_PREFIX):
        return None
    m = CODE_ID_RE.search(s)
    if not m:
        return None
    before = s[:m.start()]
    names = CODE_NAME_RE.findall(before)
    name = names[-1].strip() if names else ""
    if "代码表" not in s and not name.endswith("代码"):
        return None
    return {"id": m.group("id"), "name": name}


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
            # 数据元说明：**优先用字典**（官方版数据项行没有这一列），
            # 与 item_desc 分开存 —— 一个是"该数据项在本表里怎么填"，
            # 一个是"这个数据元在全局怎么定义"，两者口径不同，混用会互相覆盖。
            item["element_desc"] = el.get("element_desc", "")
            if not item.get("item_desc") and el.get("element_desc"):
                item["item_desc"] = el["element_desc"]
        ref = _code_ref(item.get("value_range", ""))
        item["code_ref"] = ref or {}
        tbl["items"].append(item)
    wb.close()
    return {
        "tables": [tables[k] for k in sorted(tables)],
        "elements": [dict(v, element_code=k,
                          code_ref=_code_ref(v.get("value_range", "")) or {})
                     for k, v in sorted(elements.items())],
        "format_legend": legend,
        "rows_scanned": n_rows,
        "rows_skipped": n_skipped,
    }


# ---------------------------------------------------------------- 附件1-4
def _read_code_toc(wb) -> dict[str, dict]:
    """「目录」表 → `{代码表中文名: {id, name, upgrade_type, upgrade_note}}`。

    表头在第 3 行（`编号 | 中文名称 | 升级类型 | 升级说明`），故先扫前 12 行找含
    `编号` 的那一行，再按名取列 —— **不按列序**。
    2026-10-26 实测：原实现找 `need="code"` 的列，而别名表里根本没有 `编号→code`，
    于是 63 张表的编号全空（页面显示不出 CD000002）。
    """
    if "目录" not in wb.sheetnames:
        return {}
    ws = wb["目录"]
    rows = list(ws.iter_rows(max_row=12, values_only=True))
    cols: dict[int, str] = {}
    head_at: int | None = None
    for idx, row in enumerate(rows):
        m = {i: TOC_ALIASES[_hkey(c)] for i, c in enumerate(row) if _hkey(c) in TOC_ALIASES}
        if "id" in m.values() and "name" in m.values():
            cols, head_at = m, idx
            break
    if head_at is None:
        return {}
    out: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=head_at + 2, values_only=True):
        rec = {f: _s(row[i]) if i < len(row) else "" for i, f in cols.items()}
        if rec.get("id") and rec.get("name"):
            out[rec["name"]] = rec
    return out


#: xlsm 导出残留：部分「说明」单元格末尾粘着导航字样 `…标准体。 返回目录`
_NAV_TAIL = re.compile(r"\s*返回目[录錄]\s*$")


def _strip_nav(v: str) -> str:
    """去掉单元格末尾的「返回目录」导航残留（**抽取层清洗**，不是展示层遮盖）。

    实测（2026-10-26）：`返回目录` 不只是一列 —— 有些说明文字末尾也被拼进了这四个字
    （`承保的，称为标准体。 返回目录`）。只删列不管单元格，读者仍会在说明里看到它。
    """
    return _NAV_TAIL.sub("", str(v or "")).strip()


def _code_sheet(ws) -> dict | None:
    """单个代码表 sheet → `{columns, rows, view, hierarchy}`；表头识别失败返回 None。"""
    raw = [tuple(_strip_nav(_s(c)) for c in row) for row in ws.iter_rows(values_only=True)]
    head_at: int | None = None
    header: list[str] = []
    # 表头判据（2026-10-26 修）：**至少两个非空单元格**，且既有代码类词、又有名称/说明类词。
    # 原判据只要求"任一单元格含代码类词" ⇒ 第 1 行的**标题**（`中介机构类别代码`，单格）
    # 被当成表头，于是真正的表头行（`代码|名称|…`）落进数据里，整表读歪。
    for i, cells in enumerate(raw[:8]):
        nonempty = [c for c in cells if c]
        if len(nonempty) < 2:
            continue
        has_code = any(any(h in c for h in CODE_HINTS) for c in nonempty)
        has_other = any(any(h in c for h in NAME_HINTS + DESC_HINTS) for c in nonempty)
        if has_code and has_other:
            head_at, header = i, list(cells)
            break
    if head_at is None:
        return None

    data = [list(c) for c in raw[head_at + 1:] if any(c)]
    # 重复表头行（部分 sheet 每屏重复一次）：首列是代码类词且整行无数字
    data = [r for r in data
            if not (r and any(h in r[0] for h in CODE_HINTS)
                    and not any(ch.isdigit() for ch in "".join(r)))]

    width = max([len(header)] + [len(r) for r in data]) if data else len(header)
    header = header + [""] * (width - len(header))
    keep: list[int] = []
    for i in range(width):
        h = header[i]
        if any(d in h for d in DROP_COL_HINTS):
            continue                      # ★ 抽取层剔除「返回目录」无效列
        if h or any((r[i] if i < len(r) else "") for r in data):
            keep.append(i)                # 具名列，或虽无表头但确有数据的层级列
    if not keep:
        return None

    columns = [header[i] if header[i] else f"(无表头)第{i + 1}列" for i in keep]
    rows = [[(r[i] if i < len(r) else "") for i in keep] for r in data]

    cpos = [k for k, i in enumerate(keep) if any(h in header[i] for h in CODE_HINTS)]
    npos = [k for k, i in enumerate(keep) if any(h in header[i] for h in NAME_HINTS)]
    dpos = [k for k, i in enumerate(keep) if any(h in header[i] for h in DESC_HINTS)]

    def _looks_code(s: str) -> bool:
        return bool(s) and len(s) <= 24 and all(
            (ch.isalnum() or ch in "-_./") for ch in s) and any(ch.isdigit() for ch in s)

    view: list[dict] = []
    for r in rows:
        code_i = next((k for k in cpos if r[k]), None)
        if code_i is None:
            code_i = next((k for k, v in enumerate(r) if _looks_code(v)), None)
        if code_i is None:
            continue
        used = {code_i}
        name_i = next((k for k in npos if r[k] and k not in used), None)
        if name_i is None:
            name_i = next((k for k, v in enumerate(r)
                           if v and k not in used and k not in dpos and k > code_i), None)
        if name_i is not None:
            used.add(name_i)
        desc_i = next((k for k in dpos if r[k] and k not in used), None)
        if desc_i is None:
            desc_i = next((k for k, v in enumerate(r)
                           if v and k not in used and k > (name_i if name_i is not None
                                                           else code_i)), None)
        view.append({
            "code": r[code_i],
            "name": r[name_i] if name_i is not None else "",
            "desc": r[desc_i] if desc_i is not None else "",
            "_col": name_i if name_i is not None else code_i,
        })

    # 层级 = **名称所在列**相对本表"基准列"的偏移（1 起）。
    #
    # 多级代码表把"下级名称"往右挪一格来表达父子关系（销售渠道 `100/110/111`
    # 的代码都在第 1 列，名称分别落在第 2/3/4 列）。基准列取本表**最常出现**的
    # 名称列 —— 不能直接用绝对列号：各表的 名称 列位置本来就不同（有的第 2 列、
    # 有的第 3 列），用绝对列号会把 57 张单层表全判成"多级"（实测踩到）。
    base = collections.Counter(v["_col"] for v in view).most_common(1)
    base_col = base[0][0] if base else 0
    for v in view:
        v["level"] = max(1, v.pop("_col") - base_col + 1)
    # 阈值 30%：个别行因"名称落在说明列"产生的偶发偏移，不该把整张表标成多级
    nested = sum(1 for v in view if v["level"] > 1)
    return {"columns": columns, "rows": rows, "view": view,
            # 判据用**绝对条数**（≥2 行有下级）而不是比例：实测 销售渠道类型代码 只有
            # 6/29 = 21% 是下级（110/111/112/113…），按 30% 比例会被判成单层表，
            # 而"有下级"这件事本身与占比无关。
            "hierarchy": nested >= 2}


def _norm_code_name(s: str) -> str:
    """代码表名归一：去空白/括号/斜杠/顿号与"的"。"""
    return re.sub(r"[\s（）()【】\[\]/、·\-—_]+", "", str(s or "")).replace("的", "")


def _match_toc(sheet: str, toc: dict[str, dict]) -> dict:
    """工作表名 → 「目录」条目（**允许名称不一致**，但绝不在多义时猜）。

    实测（2026-10-26）监管原文自身就有三处名称不同：

    | 目录（带编号） | 工作表 |
    |---|---|
    | 处罚方式代码 `CD000053` | 问责处罚方式代码 |
    | 保单/险种终止原因代码 | 保单终止原因代码 |
    | 人身伤残代码 | 人身险伤残代码 |

    规则：精确 → 归一化精确 → 双向包含（唯一）→ 相似度最佳（≥0.72 且显著高于次优）。
    都不满足就返回 `{}`（页面显示"目录未列出"）—— **不臆造编号**。
    """
    if sheet in toc:
        return toc[sheet]
    key = _norm_code_name(sheet)
    for name, meta in toc.items():
        if _norm_code_name(name) == key:
            return meta
    subs = [m for n, m in toc.items()
            if key and (key in _norm_code_name(n) or _norm_code_name(n) in key)]
    if len(subs) == 1:
        return subs[0]
    import difflib

    scored = sorted(((difflib.SequenceMatcher(None, key, _norm_code_name(n)).ratio(), m)
                     for n, m in toc.items()), key=lambda x: -x[0])
    if len(scored) >= 1 and scored[0][0] >= 0.72:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05:
            return scored[0][1]
    return {}


def parse_codes(path: str) -> list[dict]:
    """附件1-4 → 代码表全量。

    **原始列**（剔除「返回目录」与全空列）与**归一视图**（代码/名称/说明/层级）都保留：
    原始列保真（复合表不丢维度），归一视图可直接读（不必让人猜哪列是名称）。
    表编号取自「目录」表的 `CDxxxxxx`（名称可能与工作表不同，见 `_match_toc`）。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    toc = _read_code_toc(wb)
    out: list[dict] = []
    for sh in wb.sheetnames:
        if sh == "目录":
            continue
        got = _code_sheet(wb[sh])
        if not got:
            continue
        meta = _match_toc(sh, toc)
        out.append({
            "sheet": sh,
            "id": meta.get("id", ""),
            "name": sh,                    # 目录名与工作表名不同时，**以工作表名为准**（读者点的是这张表）
            "toc_name": meta.get("name", ""),
            "upgrade_type": meta.get("upgrade_type", ""),
            "upgrade_note": meta.get("upgrade_note", ""),
            "columns": got["columns"],
            "rows": got["rows"],
            "view": got["view"],
            "hierarchy": got["hierarchy"],
            "count": len(got["rows"]),
        })
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

