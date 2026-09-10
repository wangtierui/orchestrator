# -*- coding: utf-8 -*-
"""excel_structure.py —— Excel(.xlsx/.xls) → 分类化结构 JSON（共享层，2026-09-10）。

移植自用户提供的通用 Excel→JSON 转换器参照实现（excel_to_json.py，Desktop），
适配为 **bytes 输入**（附件字节直接解析，不落盘），供 table_recovery/structured_table_fields
产出「分类后合理结构」的 table_structured：

  table_structured = [                       # list：每附件一个 workbook 对象（保持聚合遍历兼容）
    {
      "kind": "excel_classified", "schema": "excel_classified_v1",
      "source_file": ..., "form_category": "统计表|说明表|混合表|未知",
      "sheets": [ {"sheet_name","sheet_index","form_category","form_confidence",
                   "dimensions":{"rows","cols"},
                   "tables":[{"table_id","form_category","table_type","confidence",
                              "region","header_region","data_region",
                              "columns":[{key,name,path,col_index}],
                              "rows":[{col_key:value}],
                              "merged_regions","doc_content","meta"}]} ]
    }
  ]

分类核心（与参照一致）：前置/底部行识别 → 表块切分 → 表头区域检测（评分+合并行）→
多级表头展开（path→key）→ 表单大类 classify_block（统计表/说明表）→ 说明表走 doc_table
（保留原文不进 detector），统计表走 detector（validation_rule/hierarchy_catalog/stat_template/
matrix）+ parser（角色映射/层级目录）。
"""
from __future__ import annotations

import datetime
import io
import os
import re
from dataclasses import asdict, dataclass, field

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None

SCHEMA_VERSION = "excel_classified_v1"

# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass
class MergedRegion:
    r1: int
    c1: int
    r2: int
    c2: int


@dataclass
class Region:
    row_start: int
    row_end: int
    col_start: int
    col_end: int


@dataclass
class Column:
    key: str
    name: str
    path: list
    data_type: str = "string"
    col_index: int = -1


@dataclass
class TableBlock:
    table_id: str
    region: Region
    matrix: list
    merged: list
    header_region: Region | None = None
    data_region: Region | None = None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    table_type: str = "unknown"
    confidence: float = 0.0
    meta: dict = field(default_factory=dict)
    form_category: str = "unknown"
    form_confidence: float = 0.0
    doc_content: list = field(default_factory=list)


# ===========================================================================
# IO（bytes）
# ===========================================================================


def _cell_to_value(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else v.isoformat()
    return v


def _read_xlsx_bytes(data: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    out = []
    for ws in wb.worksheets:
        max_row, max_col = ws.max_row or 0, ws.max_column or 0
        matrix = [[_cell_to_value(ws.cell(row=r, column=c).value)
                   for c in range(1, max_col + 1)] for r in range(1, max_row + 1)]
        merged = [MergedRegion(mc.min_row - 1, mc.min_col - 1, mc.max_row - 1, mc.max_col - 1)
                  for mc in ws.merged_cells.ranges]
        out.append((ws.title, matrix, merged))
    wb.close()
    return out


def _read_xls_bytes(data: bytes):
    book = xlrd.open_workbook(file_contents=data)
    out = []
    for si in range(book.nsheets):
        sh = book.sheet_by_index(si)
        matrix = [[_cell_to_value(sh.cell_value(r, c)) for c in range(sh.ncols)]
                  for r in range(sh.nrows)]
        merged = [MergedRegion(rlo, clo, rhi - 1, chi - 1)
                  for (rlo, rhi, clo, chi) in sh.merged_cells]
        out.append((sh.name, matrix, merged))
    return out


# ===========================================================================
# 前置行 / 底部行识别
# ===========================================================================

TITLE_PATTERNS = [r"^附录[一二三四五六七八九十\d]", r"统计表\s*$", r"填制说明\s*$",
                  r"采集表\s*$", r"目录\s*$", r"说明\s*$"]
PREAMBLE_PATTERNS = [r"^\d{4}\s*年.*月.*日", r"^20[×xX]+\s*年", r"^填报机构", r"^填报单位",
                     r"^填报日期", r"^单位[:：]", r"^金额单位", r"^制表单位", r"^报告期"]
FOOTER_PATTERNS = [r"^制表[:：]", r"^审核[:：]", r"^说明[:：]", r"^注[:：]", r"^填表人",
                   r"^负责人[:：]"]


def _is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _row_is_empty(row):
    return all(_is_blank(v) for v in row)


def _is_preamble_row(row, *, allow_title=True):
    if _row_is_empty(row):
        return False
    filled = [(i, v) for i, v in enumerate(row) if not _is_blank(v)]
    if not filled:
        return False
    text = " ".join(str(v).strip() for _, v in filled)
    text_norm = re.sub(r"\s+", "", text)
    if allow_title and len(filled) == 1:
        if any(re.search(p, text_norm) for p in TITLE_PATTERNS):
            return True
        if len(text_norm) >= 8 and not any(
                w in text_norm for w in ("名称", "日期", "金额", "类型", "代码", "编号")):
            return True
    for p in PREAMBLE_PATTERNS:
        if re.search(p, text_norm):
            return True
    for p in FOOTER_PATTERNS:
        if re.search(p, text_norm):
            return True
    return False


def _find_header_start(matrix):
    r, n = 0, len(matrix)
    while r < n and (_row_is_empty(matrix[r]) or _is_preamble_row(matrix[r])):
        r += 1
    return r


# ===========================================================================
# 表格切分 / 表头识别 / 列构建
# ===========================================================================


def _trim_block(matrix, r0, r1, c0, c1):
    while r0 < r1 and _row_is_empty(matrix[r0][c0:c1]):
        r0 += 1
    while r1 > r0 and _row_is_empty(matrix[r1 - 1][c0:c1]):
        r1 -= 1
    while c0 < c1 and all(_is_blank(matrix[r][c0]) if c0 < len(matrix[r]) else True
                          for r in range(r0, r1)):
        c0 += 1
    while c1 > c0 and all(_is_blank(matrix[r][c1 - 1]) if c1 - 1 < len(matrix[r]) else True
                          for r in range(r0, r1)):
        c1 -= 1
    if r0 >= r1 or c0 >= c1:
        return [], r0, c0
    return [row[c0:c1] for row in matrix[r0:r1]], r0, c0


def split_tables(matrix, merged):
    if not matrix:
        return []
    n_rows = len(matrix)
    n_cols = max((len(r) for r in matrix), default=0)
    norm = [list(r) + [None] * (n_cols - len(r)) for r in matrix]
    blocks, in_block, start = [], False, 0
    for r in range(n_rows):
        empty = _row_is_empty(norm[r])
        if not empty and not in_block:
            start, in_block = r, True
        elif empty and in_block:
            blocks.append((start, r))
            in_block = False
    if in_block:
        blocks.append((start, n_rows))
    merged_blocks = []
    for b in blocks:
        if merged_blocks and b[0] - merged_blocks[-1][1] <= 1:
            merged_blocks[-1][1] = b[1]
        else:
            merged_blocks.append([b[0], b[1]])
    out = []
    for (r0, r1) in merged_blocks:
        sub, nr0, nc0 = _trim_block(norm, r0, r1, 0, n_cols)
        if not sub:
            continue
        nr1, nc1 = nr0 + len(sub), nc0 + len(sub[0])
        if len(sub) <= 2 and _is_preamble_row(sub[0]):
            continue
        local_merged = [MergedRegion(max(m.r1, nr0) - nr0, max(m.c1, nc0) - nc0,
                                     min(m.r2, nr1 - 1) - nr0, min(m.c2, nc1 - 1) - nc0)
                        for m in merged
                        if not (m.r2 < nr0 or m.r1 >= nr1 or m.c2 < nc0 or m.c1 >= nc1)]
        out.append((sub, Region(nr0, nr1, nc0, nc1), local_merged))
    return out


HEADER_HINT_WORDS = {
    "序号", "代码", "编号", "名称", "地区", "区域", "项目", "指标", "主题域", "表名",
    "数据项", "字段", "值", "单位", "合计", "总计", "类型", "状态", "日期", "时间",
    "备注", "规则", "说明", "事件", "金额", "损失", "机构", "目录",
}


def _row_header_score(row):
    cells = [c for c in row if not _is_blank(c)]
    if not cells:
        return 0.0
    n = len(cells)
    hint = sum(1 for c in cells if isinstance(c, str)
               and any(h in c for h in HEADER_HINT_WORDS))
    text_ratio = sum(1 for c in cells if isinstance(c, str)) / n
    return 0.5 * text_ratio + 0.5 * (hint / n)


def detect_header_region(matrix, merged):
    if not matrix:
        return None
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if matrix else 0
    if n_cols == 0:
        return None
    start = _find_header_start(matrix)
    if start >= n_rows:
        return None
    max_scan = min(n_rows, start + 8)
    scores = [_row_header_score(matrix[r]) for r in range(start, max_scan)]
    merged_rows = set()
    for m in merged:
        if m.r2 > m.r1:
            for r in range(max(m.r1, start), min(m.r2 + 1, max_scan)):
                merged_rows.add(r)
    first = None
    for r in range(start, max_scan):
        if scores[r - start] >= 0.4 or r in merged_rows:
            first = r
            break
    if first is None:
        for r in range(start, max_scan):
            if not _row_is_empty(matrix[r]):
                first = r
                break
    if first is None:
        return None
    last = first
    for r in range(first + 1, max_scan):
        if scores[r - start] >= 0.25 or r in merged_rows:
            last = r
        else:
            break
    return Region(first, last + 1, 0, n_cols)


def _fill_merged_for_header(matrix, merged, header):
    h = [list(matrix[r][header.col_start:header.col_end])
         for r in range(header.row_start, header.row_end)]
    for m in merged:
        if m.r2 < header.row_start or m.r1 >= header.row_end:
            continue
        if m.c2 < header.col_start or m.c1 >= header.col_end:
            continue
        top_r, top_c = max(m.r1, header.row_start), max(m.c1, header.col_start)
        if top_r >= len(matrix) or top_c >= len(matrix[top_r]):
            continue
        val = matrix[top_r][top_c]
        for r in range(max(m.r1, header.row_start), min(m.r2, header.row_end - 1) + 1):
            for c in range(max(m.c1, header.col_start), min(m.c2, header.col_end - 1) + 1):
                if _is_blank(h[r - header.row_start][c - header.col_start]):
                    h[r - header.row_start][c - header.col_start] = val
    return h


def _forward_fill_row(row):
    out, last = [], None
    for v in row:
        if _is_blank(v):
            out.append(last)
        else:
            out.append(v)
            last = v
    return out


_KEY_SAFE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _make_key(path, idx):
    parts = []
    for p in path:
        p2 = _KEY_SAFE.sub("_", str(p)).strip("_")
        if p2:
            parts.append(p2)
    return "__".join(parts) if parts else f"col_{idx + 1}"


def build_columns(matrix, merged, header):
    h = [_forward_fill_row(row)
         for row in _fill_merged_for_header(matrix, merged, header)]
    n_cols = header.col_end - header.col_start
    columns = []
    for c in range(n_cols):
        path = []
        for r in range(len(h)):
            v = h[r][c]
            if _is_blank(v):
                continue
            s = str(v).strip()
            if not path or path[-1] != s:
                path.append(s)
        if not path:
            path = [f"col_{c + 1}"]
        columns.append(Column(key=_make_key(path, c), name=path[-1], path=path, col_index=c))
    seen: dict = {}
    for col in columns:
        if col.key in seen:
            seen[col.key] += 1
            col.key = f"{col.key}__{seen[col.key]}"
        else:
            seen[col.key] = 0
    return columns


def _fill_merged_in_data(matrix, merged, data_region):
    if data_region is None:
        return [list(r) for r in matrix]
    m2 = [list(r) for r in matrix]
    for mc in merged:
        r1 = max(mc.r1, data_region.row_start)
        r2 = min(mc.r2, data_region.row_end - 1)
        c1 = max(mc.c1, data_region.col_start)
        c2 = min(mc.c2, data_region.col_end - 1)
        if r2 < r1 or c2 < c1:
            continue
        val = m2[mc.r1][mc.c1] if mc.r1 < len(m2) and mc.c1 < len(m2[mc.r1]) else None
        if _is_blank(val):
            continue
        for r in range(r1, r2 + 1):
            if r >= len(m2):
                continue
            for c in range(c1, c2 + 1):
                if c < len(m2[r]) and _is_blank(m2[r][c]):
                    m2[r][c] = val
    return m2


# ===========================================================================
# detector（类型识别）
# ===========================================================================


@dataclass
class StructuralFeatures:
    n_rows: int
    n_cols: int
    header_rows: int
    has_merged_header: bool
    merged_ratio: float
    numeric_col_ratio: float
    first_col_is_sequence: bool
    second_col_is_code: bool
    has_summary_rows: bool
    is_wide: bool
    is_tall: bool
    text_col_ratio: float


@dataclass
class DetectorConfig:
    type_name: str
    priority: float = 1.0
    header_keywords: list = field(default_factory=list)
    optional_header_keywords: list = field(default_factory=list)
    min_columns: int = 0
    max_columns: int = 0
    min_rows: int = 0
    max_rows: int = 0
    header_rows_range: tuple | None = None
    require_merged_header: bool | None = None
    min_numeric_col_ratio: float | None = None
    max_numeric_col_ratio: float | None = None
    require_first_col_sequence: bool | None = None
    require_second_col_code: bool | None = None
    require_summary_rows: bool | None = None
    require_wide: bool | None = None
    require_tall: bool | None = None
    row_key_pattern: str | None = None
    keyword_weight: float = 0.6
    structural_weight: float = 0.4


_SEQ_RE = re.compile(r"^\d+$")
_CODE_RE = re.compile(r"^[A-Za-z0-9\-_\.]{3,24}$")
_SUMMARY_WORDS = ("合计", "总计", "小计", "汇总", "Total", "total")


def extract_features(block):
    matrix = block.matrix
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if matrix else 0
    header = block.header_region or Region(0, 1, 0, n_cols)
    data = block.data_region or Region(header.row_end, n_rows, 0, n_cols)
    header_rows = header.row_end - header.row_start
    has_merged_header = any(m.r2 > m.r1 and m.r1 < header.row_end and m.r2 >= header.row_start
                            for m in block.merged)
    header_cells = max(1, header_rows * n_cols)
    merged_cells = 0
    for m in block.merged:
        r1, r2 = max(m.r1, header.row_start), min(m.r2, header.row_end - 1)
        c1, c2 = max(m.c1, header.col_start), min(m.c2, header.col_end - 1)
        if r2 >= r1 and c2 >= c1:
            merged_cells += (r2 - r1 + 1) * (c2 - c1 + 1)
    merged_ratio = merged_cells / header_cells
    numeric_cols = text_cols = total_data_cols = 0
    for c in range(data.col_start, data.col_end):
        vals = [matrix[r][c] for r in range(data.row_start, data.row_end)
                if r < len(matrix) and c < len(matrix[r]) and not _is_blank(matrix[r][c])]
        if not vals:
            continue
        total_data_cols += 1
        if sum(1 for v in vals if isinstance(v, (int, float))) / len(vals) >= 0.6:
            numeric_cols += 1
        if sum(1 for v in vals if isinstance(v, str)) / len(vals) >= 0.6:
            text_cols += 1
    numeric_col_ratio = numeric_cols / total_data_cols if total_data_cols else 0.0
    text_col_ratio = text_cols / total_data_cols if total_data_cols else 0.0
    first_col_vals = []
    for r in range(data.row_start, data.row_end):
        if r < len(matrix) and data.col_start < len(matrix[r]):
            v = matrix[r][data.col_start]
            if isinstance(v, (int, float)):
                first_col_vals.append(int(v))
            elif isinstance(v, str) and _SEQ_RE.match(v.strip()):
                first_col_vals.append(int(v.strip()))
    first_col_is_sequence = (len(first_col_vals) >= 3 and
                             all(b >= a for a, b in
                                 zip(first_col_vals, first_col_vals[1:], strict=False)))
    second_col_is_code = False
    if data.col_end - data.col_start >= 2:
        vals = [matrix[r][data.col_start + 1] for r in range(data.row_start, data.row_end)
                if r < len(matrix) and data.col_start + 1 < len(matrix[r])
                and isinstance(matrix[r][data.col_start + 1], str)
                and matrix[r][data.col_start + 1].strip()]
        if vals:
            second_col_is_code = sum(1 for v in vals if _CODE_RE.match(v)) / len(vals) >= 0.5
    has_summary_rows = any(isinstance(v, str) and any(w in v for w in _SUMMARY_WORDS)
                           for r in range(data.row_start, min(data.row_end, len(matrix)))
                           for v in matrix[r])
    is_wide = n_cols >= 2 * max(1, n_rows)
    is_tall = n_rows >= 2 * max(1, n_cols)
    return StructuralFeatures(n_rows, n_cols, header_rows, has_merged_header, merged_ratio,
                              numeric_col_ratio, first_col_is_sequence, second_col_is_code,
                              has_summary_rows, is_wide, is_tall, text_col_ratio)


def _norm(s):
    return re.sub(r"\s+", "", str(s)) if s is not None else ""


def _table_header_text(columns):
    return "|".join(_norm(p) for c in columns for p in c.path)


def _keyword_score(block, cfg):
    req = [_norm(k) for k in cfg.header_keywords]
    opt = [_norm(k) for k in cfg.optional_header_keywords]
    if not req and not opt:
        return None
    header_text = _table_header_text(block.columns)
    req_hit = sum(1 for k in req if k and k in header_text)
    opt_hit = sum(1 for k in opt if k and k in header_text)
    if req and req_hit == 0:
        return -1.0
    return 0.7 * (req_hit / len(req) if req else 0.0) + 0.3 * (opt_hit / len(opt) if opt else 0.0)


def _structural_score(f, cfg):
    checks = []
    if cfg.min_columns:
        checks.append(1.0 if f.n_cols >= cfg.min_columns else 0.0)
    if cfg.max_columns:
        checks.append(1.0 if f.n_cols <= cfg.max_columns else 0.0)
    if cfg.min_rows:
        checks.append(1.0 if f.n_rows >= cfg.min_rows else 0.0)
    if cfg.max_rows:
        checks.append(1.0 if f.n_rows <= cfg.max_rows else 0.0)
    if cfg.header_rows_range:
        lo, hi = cfg.header_rows_range
        checks.append(1.0 if lo <= f.header_rows <= hi else 0.0)
    for attr, want in (("require_merged_header", f.has_merged_header),
                       ("require_first_col_sequence", f.first_col_is_sequence),
                       ("require_second_col_code", f.second_col_is_code),
                       ("require_summary_rows", f.has_summary_rows),
                       ("require_wide", f.is_wide), ("require_tall", f.is_tall)):
        v = getattr(cfg, attr)
        if v is not None:
            checks.append(1.0 if want == v else 0.0)
    if cfg.min_numeric_col_ratio is not None:
        checks.append(1.0 if f.numeric_col_ratio >= cfg.min_numeric_col_ratio else 0.0)
    if cfg.max_numeric_col_ratio is not None:
        checks.append(1.0 if f.numeric_col_ratio <= cfg.max_numeric_col_ratio else 0.0)
    if not checks:
        return None
    return sum(checks) / len(checks)


def match_detector(block, f, cfg):
    if not block.columns:
        return 0.0
    kw = _keyword_score(block, cfg)
    st = _structural_score(f, cfg)
    if kw is not None and kw < 0:
        return 0.0
    if kw is None and st is None:
        return 0.0
    if kw is None:
        return st or 0.0
    if st is None:
        return kw
    return (cfg.keyword_weight * kw + cfg.structural_weight * st) / (
        cfg.keyword_weight + cfg.structural_weight)


def detect_table_type(block, detectors):
    f = extract_features(block)
    scored = [(cfg.type_name, match_detector(block, f, cfg) * cfg.priority, cfg)
              for cfg in detectors]
    if not scored:
        return "unknown", 0.0
    scored.sort(key=lambda x: x[1], reverse=True)
    top_type, top_score, _ = scored[0]
    if top_score < 0.35:
        return "unknown", top_score
    return top_type, top_score


DEFAULT_DETECTORS = [
    DetectorConfig(type_name="validation_rule", priority=100,
                   header_keywords=["序号", "主题域", "表名", "数据项名称", "数据项代码"],
                   optional_header_keywords=["校验规则", "检核规则"],
                   min_columns=4, max_columns=10, require_tall=True,
                   require_merged_header=False, max_numeric_col_ratio=0.2,
                   row_key_pattern=r"^[A-Z]\d{4,6}$",
                   keyword_weight=0.5, structural_weight=0.5),
    DetectorConfig(type_name="hierarchy_catalog", priority=95,
                   header_keywords=["1级目录"],
                   optional_header_keywords=["2级目录", "3级目录", "编号", "定义", "事件类型"],
                   min_columns=3, require_tall=True, require_merged_header=False,
                   keyword_weight=0.8, structural_weight=0.2),
    DetectorConfig(type_name="stat_template", priority=90,
                   header_keywords=["行政区域代码", "地区"],
                   optional_header_keywords=[
                       "是否设立分支机构", "是否设立虚拟机构", "原保险保费收入", "赔付支出",
                       "退保金", "期末有效保险金额", "本年累计新增保险金额", "期末从业人员"],
                   min_columns=6, require_merged_header=True, header_rows_range=(1, 5),
                   min_numeric_col_ratio=0.2, keyword_weight=0.3, structural_weight=0.7),
    DetectorConfig(type_name="stat_template", priority=60, min_columns=8,
                   require_merged_header=True, min_numeric_col_ratio=0.3,
                   keyword_weight=0.0, structural_weight=1.0),
]

PARSERS: dict = {}


def register(table_type):
    def deco(fn):
        PARSERS[table_type] = fn
        return fn
    return deco


def _filter_data_rows(matrix, region):
    keep = []
    for r in range(region.row_start, region.row_end):
        if r >= len(matrix):
            break
        row = matrix[r]
        if _row_is_empty(row) or _is_preamble_row(row, allow_title=False):
            continue
        keep.append(r)
    return keep


def _matrix_to_rows(block):
    if not block.data_region:
        return []
    filled = _fill_merged_in_data(block.matrix, block.merged, block.data_region)
    rows = []
    for r in _filter_data_rows(filled, block.data_region):
        row = filled[r]
        item = {}
        for col in block.columns:
            idx = block.data_region.col_start + col.col_index
            item[col.key] = row[idx] if idx < len(row) else None
        rows.append(item)
    return rows


@register("matrix")
def parse_matrix(block):
    block.rows = _matrix_to_rows(block)
    return block


@register("validation_rule")
def parse_validation_rule(block):
    block.rows = _matrix_to_rows(block)
    role_map = {}
    for col in block.columns:
        text = _norm("".join(col.path))
        if "序号" in text and "rule_id" not in role_map.values():
            role_map[col.key] = "rule_id"
        elif "主题域" in text:
            role_map[col.key] = "domain"
        elif "表名" in text:
            role_map[col.key] = "table_name"
        elif "数据项名称" in text:
            role_map[col.key] = "field_name"
        elif "数据项代码" in text:
            role_map[col.key] = "field_code"
        elif "校验规则" in text or "检核规则" in text:
            role_map[col.key] = "rule_text"
    rules = []
    for row in block.rows:
        rule = {}
        for k, v in row.items():
            rule[role_map.get(k, k)] = v if role_map.get(k) else v
        if rule.get("rule_id"):
            m = re.match(r"^([A-Z])", str(rule["rule_id"]))
            if m:
                rule["rule_type"] = m.group(1)
        rules.append(rule)
    block.rows = rules
    block.meta["role_map"] = role_map
    return block


@register("stat_template")
def parse_stat_template(block):
    block.rows = _matrix_to_rows(block)
    return block


@register("hierarchy_catalog")
def parse_hierarchy_catalog(block):
    block.rows = _matrix_to_rows(block)
    level_cols = []
    for col in block.columns:
        text = _norm("".join(col.path))
        m = re.search(r"(\d+)级目录", text)
        if m:
            level_cols.append({"level": int(m.group(1)), "key": col.key})
    level_cols.sort(key=lambda x: x["level"])
    if level_cols:
        block.meta["levels"] = level_cols
    return block


def compute_regions(block):
    header = detect_header_region(block.matrix, block.merged)
    if header is None:
        header = Region(0, 1, 0, len(block.matrix[0]) if block.matrix else 0)
    block.header_region = header
    block.data_region = Region(header.row_end, len(block.matrix),
                               header.col_start, header.col_end)
    block.columns = build_columns(block.matrix, block.merged, header)


# ===========================================================================
# 表单大类分类：统计表 vs 说明表
# ===========================================================================

_QUASI_BLANK_TOKENS = {"0", "-", "—", "–", "/", "\\", "n/a", "na", "null", "none",
                       "无", "不适用", "未记录", "空", "不填", "待填", "／"}
DOC_HINT_WORDS = {"说明", "描述", "释义", "定义", "备注", "解释", "填报", "要求", "规范",
                  "示例", "内容", "格式"}
STAT_HEADER_WORDS = {"序号", "数据项序号", "数据项标识", "主题域", "主题域编号", "表名",
                     "表中文名", "表编号", "数据项名称", "数据项代码", "数据元编码",
                     "是否主键", "是否可空", "升级类型", "数据元分类", "数据元名称",
                     "取值范围", "数据格式", "数据表说明", "行政区域代码", "地区",
                     "保费收入", "赔付支出", "退保金", "保单数", "机构数", "人数", "金额"}


def _is_quasi_blank(v):
    if _is_blank(v):
        return True
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) == 0.0
    return _norm(v).lower() in _QUASI_BLANK_TOKENS


def _text_len(v):
    return len(str(v).strip()) if v is not None else 0


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fill_merged_for_classify(matrix, merged):
    if not merged:
        return [list(r) for r in matrix]
    m2 = [list(r) for r in matrix]
    for mc in merged:
        if mc.r1 >= len(m2) or mc.c1 >= len(m2[mc.r1]):
            continue
        val = m2[mc.r1][mc.c1]
        if _is_blank(val):
            continue
        for r in range(mc.r1, min(mc.r2, len(m2) - 1) + 1):
            for c in range(mc.c1, min(mc.c2, len(m2[r]) - 1) + 1):
                if _is_blank(m2[r][c]):
                    m2[r][c] = val
    return m2


def _column_stats(matrix):
    n_rows = len(matrix)
    if n_rows == 0:
        return []
    n_cols = max(len(r) for r in matrix)
    stats = []
    for c in range(n_cols):
        vals = [r[c] if c < len(r) else None for r in matrix]
        valid = [v for v in vals if not _is_quasi_blank(v)]
        n_valid = len(valid)
        fill_ratio = n_valid / n_rows
        if n_valid:
            avg_len = sum(_text_len(v) for v in valid) / n_valid
            long_ratio = sum(1 for v in valid if _text_len(v) >= 30) / n_valid
            num_ratio = sum(1 for v in valid if _is_number(v)) / n_valid
        else:
            avg_len = long_ratio = num_ratio = 0.0
        stats.append({"fill_ratio": fill_ratio, "avg_len": avg_len,
                      "long_ratio": long_ratio, "num_ratio": num_ratio})
    return stats


def _find_header_rows(matrix, merged, max_scan=8):
    n_rows = len(matrix)
    if n_rows == 0:
        return 0, 0
    start = _find_header_start(matrix)
    if start >= n_rows:
        return 0, 0
    end = start
    for r in range(start, min(start + 3, n_rows)):
        row = matrix[r]
        valid = [v for v in row if not _is_quasi_blank(v)]
        if not valid:
            break
        n_cols = len(row)
        if (len(valid) / n_cols if n_cols else 0) >= 0.5:
            end = r + 1
        else:
            break
    return start, max(end, start + 1)


def classify_block(matrix, merged):
    """单表块表单大类判定。返回 (form_category, confidence, meta)：统计表|说明表|未知。"""
    meta = {}
    if not matrix:
        return "未知", 0.0, meta
    n_rows = len(matrix)
    n_cols = max((len(r) for r in matrix), default=0)
    if n_rows == 0 or n_cols == 0:
        return "未知", 0.0, meta
    meta["n_rows"], meta["n_cols"] = n_rows, n_cols
    filled = _fill_merged_for_classify(matrix, merged)
    header_start, header_end = _find_header_rows(filled, merged)
    meta["header_start"], meta["header_end"] = header_start, header_end
    data_rows = filled[header_end:]
    meta["data_rows"] = len(data_rows)
    col_stats = _column_stats(filled)
    data_col_stats = _column_stats(data_rows) if data_rows else col_stats
    desc_cols = [i for i, s in enumerate(col_stats)
                 if s["avg_len"] >= 30 and s["long_ratio"] >= 0.6]
    meta["desc_cols"] = desc_cols
    if data_rows:
        data_cells = [v for r in data_rows for v in r]
        data_blank_ratio = (sum(1 for v in data_cells if _is_quasi_blank(v)) / len(data_cells)
                            if data_cells else 1.0)
    else:
        data_blank_ratio = 1.0
    meta["data_blank_ratio"] = round(data_blank_ratio, 4)
    dim_cols = [i for i, s in enumerate(data_col_stats)
                if s["fill_ratio"] >= 0.5 and s["avg_len"] < 20]
    empty_cols = [i for i, s in enumerate(data_col_stats) if s["fill_ratio"] <= 0.15]
    meta["dim_cols"], meta["empty_cols"] = dim_cols, empty_cols
    left_right_pattern = bool(dim_cols and len(empty_cols) >= 2 and max(dim_cols) < min(empty_cols))
    meta["left_right_pattern"] = left_right_pattern
    right_blank_ratio = 0.0
    if n_cols >= 4 and data_col_stats:
        right_half = data_col_stats[n_cols // 2:]
        right_blank_ratio = (sum(1 for s in right_half if s["fill_ratio"] <= 0.15)
                             / len(right_half)) if right_half else 0.0
    meta["right_blank_ratio"] = round(right_blank_ratio, 4)
    all_valid = [v for r in filled for v in r if not _is_quasi_blank(v)]
    overall_long = (sum(1 for v in all_valid if _text_len(v) >= 30) / len(all_valid)
                    if all_valid else 0.0)
    meta["overall_long"] = round(overall_long, 4)
    header_text = "".join(str(v) for r in filled[header_start:header_end]
                          for v in r if not _is_quasi_blank(v))
    doc_hit = sum(1 for w in DOC_HINT_WORDS if w in header_text)
    stat_hit = sum(1 for w in STAT_HEADER_WORDS if w in header_text)
    meta["doc_hit"], meta["stat_hit"] = doc_hit, stat_hit

    if n_cols <= 2 and overall_long >= 0.3:
        return "说明表", 0.9, meta
    if desc_cols:
        return "说明表", 0.9, meta
    if left_right_pattern:
        return "统计表", 0.9, meta
    if right_blank_ratio >= 0.6:
        return "统计表", 0.85, meta
    if data_blank_ratio >= 0.8 and n_cols >= 5:
        return "统计表", 0.85, meta
    if doc_hit >= 1 and data_blank_ratio < 0.5:
        return "说明表", 0.8, meta
    if overall_long >= 0.25:
        return "说明表", 0.8, meta
    if n_cols <= 3 and not empty_cols:
        return "说明表", 0.75, meta
    if stat_hit >= 3 and dim_cols:
        return "统计表", 0.7, meta
    return "未知", 0.3, meta


def classify_sheet(block_results):
    if not block_results:
        return "未知", 0.0
    categories = [c for c, _ in block_results]
    confidences = [cf for _, cf in block_results]
    n_stat, n_doc = categories.count("统计表"), categories.count("说明表")
    if n_stat > 0 and n_doc == 0:
        return "统计表", sum(confidences) / len(confidences)
    if n_doc > 0 and n_stat == 0:
        return "说明表", sum(confidences) / len(confidences)
    if n_stat > 0 and n_doc > 0:
        return "混合表", sum(confidences) / len(confidences)
    return "未知", 0.0


# ===========================================================================
# 主入口（bytes）
# ===========================================================================


def _table_to_dict(t: TableBlock) -> dict:
    out = {
        "table_id": t.table_id,
        "form_category": t.form_category,
        "form_confidence": t.form_confidence,
        "table_type": t.table_type,
        "confidence": t.confidence,
        "region": asdict(t.region),
        "header_region": asdict(t.header_region) if t.header_region else None,
        "data_region": asdict(t.data_region) if t.data_region else None,
        "columns": [asdict(c) for c in t.columns],
        "rows": t.rows,
        "merged_regions": [asdict(m) for m in t.merged],
    }
    if t.meta:
        out["meta"] = t.meta
    if t.doc_content:
        out["doc_content"] = t.doc_content
    return out


def _sheet_to_dict(s_name, s_index, s_form, s_conf, dims, merged, tables) -> dict:
    return {
        "sheet_name": s_name, "sheet_index": s_index,
        "form_category": s_form, "form_confidence": round(s_conf, 3),
        "dimensions": dims,
        "merged_regions": [asdict(m) for m in merged],
        "tables": tables,
    }


def process_workbook_bytes(data: bytes, source_file: str = "",
                           detectors: list | None = None) -> dict:
    """Excel bytes → 分类化结构（excel_classified_v1）。xls 需 xlrd、xlsx 需 openpyxl。"""
    detectors = detectors or DEFAULT_DETECTORS
    suffix = os.path.splitext(source_file or "")[1].lower()
    if suffix == ".xls":
        raw_sheets = _read_xls_bytes(data)
    else:
        raw_sheets = _read_xlsx_bytes(data)

    sheets_out = []
    block_cats_all = []
    for idx, (name, matrix, merged) in enumerate(raw_sheets, start=1):
        dims = {"rows": len(matrix), "cols": max((len(r) for r in matrix), default=0)}
        tables_out, block_cats = [], []
        if matrix:
            for ti, (sub, region, local_merged) in enumerate(
                    split_tables(matrix, merged), start=1):
                blk = TableBlock(table_id=f"{idx}-{ti}", region=region, matrix=sub,
                                 merged=local_merged)
                cat, cat_conf, cat_meta = classify_block(sub, local_merged)
                blk.form_category = cat
                blk.form_confidence = round(cat_conf, 3)
                blk.meta["form_classify"] = cat_meta
                block_cats.append((cat, cat_conf))
                if cat == "说明表":
                    blk.table_type = "doc_table"
                    blk.confidence = round(cat_conf, 3)
                    blk.doc_content = [row for row in sub if not _row_is_empty(row)]
                    tables_out.append(_table_to_dict(blk))
                    continue
                compute_regions(blk)
                ttype, conf = detect_table_type(blk, detectors)
                blk.table_type = ttype
                blk.confidence = round(conf, 3)
                blk = PARSERS.get(ttype, parse_matrix)(blk)
                tables_out.append(_table_to_dict(blk))
        sheet_cat, sheet_conf = classify_sheet(block_cats)
        block_cats_all.extend(block_cats)
        sheets_out.append(_sheet_to_dict(name, idx, sheet_cat, sheet_conf, dims,
                                         merged, tables_out))
    form_cat, form_conf = classify_sheet(block_cats_all)
    return {
        "kind": "excel_classified",
        "schema": SCHEMA_VERSION,
        "source_file": source_file,
        "form_category": form_cat,
        "form_confidence": round(form_conf, 3),
        "sheets": sheets_out,
    }
