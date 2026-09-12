# -*- coding: utf-8 -*-
"""excel_structure.py —— Excel(.xlsx/.xls) → 分类化结构 JSON（共享层，2026-09-10 v2）。

移植自用户参照实现（excel_to_json_v2.py，Desktop）并适配 **bytes 输入**，产出
table_structured 的「分类后合理结构」（schema=excel_classified_v2）：

  table_structured = [                       # list：每附件一个 workbook 对象
    {
      "kind": "excel_classified", "schema": "excel_classified_v2",
      "source_file": ..., "form_summary": {"统计表": n, "说明表": n, ...},
      "sheet_overview": [{sheet_name, sheet_index, type, row_count}],
      # —— 方案 C：相同维度行集去重，多表共享（row_sets 单份存储维度行）——
      "row_sets": {"dims_<hash>": {dimension_ref, columns, row_count, rows}},
      "table_sets": [{table_set_id:"ts_<hash>", dimension_ref, dimension_columns,
                      row_count, variants:[{sheet_name, sheet_index, table_id, type,
                                           row_count, metric_columns, metric_rows, meta}]}],
      # —— 方案 A：不同维度统计表 / 说明表 独立输出 ——
      "sheets": [
        # 说明表（subtype 细分）
        {table_id, type:"说明表", subtype:"narrative|key_value|indicator_doc",
         title, key_column, columns, item_count, items, raw_rows?, meta},
        # 统计表（单 variant 退回 / 无维度回退，均带完整数据行）
        {table_id, type:"统计表", row_count, dimension_columns, dimension_rows,
         metric_columns, metric_rows, meta}
      ]
    }
  ]

相对 v2 参照的两处**数据完整性加固**（v2 原版仅输出维度行+指标列名，指标值不落 JSON）：
  1) table_sets.variants[].metric_rows：与 row_sets 行一一对齐的指标值二维数组（去重
     存储维度、指标值不丢）；
  2) 无维度（维度列识别失败）统计表回退输出完整 columns+rows，避免整表数据丢失。

分类核心：前置/底部行剥离 → 表块切分（空行+合并相邻）→ 表单大类 classify_block
（统计表 / 说明表，长文本/列数/关键词判定）→
  · 统计表：表头区域检测（评分+合并行）→ 多级表头展开（path→key）→ 数据行提取 →
    维度列（fill 高/数值率低/短文本，序数容忍）→ 维度行集哈希 → table_sets 聚合；
  · 说明表：subtype 识别（narrative 逐段 / key_value 键值 / indicator_doc 指标表）→
    items / paragraphs 结构化 + raw_rows 留存。
"""
from __future__ import annotations

import datetime
import io
import re
from dataclasses import dataclass

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None

SCHEMA_VERSION = "excel_classified_v2"


# ===========================================================================
# 数据结构
# ===========================================================================



TITLE_PATTERNS = [r"^附录[一二三四五六七八九十\d]", r"^附件", r"^附表", r"统计表\s*$",
                  r"填制说明\s*$", r"采集表\s*$", r"目录\s*$", r"说明\s*$"]

PREAMBLE_PATTERNS = [r"^\d{4}\s*年.*月.*日", r"^20[×xX]+\s*年", r"^填报机构", r"^填报单位",
                     r"^填报日期", r"^单位[:：]", r"^金额单位", r"^制表单位", r"^报告期",
                     r"^公司名称", r"^机构名称", r"^被审计单位"]

FOOTER_PATTERNS = [r"^制表[:：]", r"^审核[:：]", r"^说明[:：]", r"^注[:：]", r"^填表人",
                   r"^负责人[:：]"]

HEADER_HINT_WORDS = {
    "序号", "代码", "编号", "名称", "地区", "区域", "项目", "指标", "主题域", "表名",
    "数据项", "字段", "值", "单位", "合计", "总计", "类型", "状态", "日期", "时间",
    "备注", "规则", "说明", "金额", "机构", "目录",
}

_ANNOT_RE = re.compile(r"^[\d.．]*\s*(其中|注[:：]?|说明[:：]|备注[:：])")

_NUMCODE_RE = re.compile(r"^\d+([-－./]\d+)*$")

_KEY_SAFE = re.compile(r"[^\w\u4e00-\u9fff]+")

DOC_HINT_WORDS = {"说明", "描述", "释义", "定义", "备注", "解释",
                  "填报", "要求", "规范", "示例", "内容", "格式"}

DIM_MAX_NUM_RATIO = 0.15

DIM_MIN_FILL_RATIO = 0.5

DIM_MAX_AVG_LEN = 30

DIM_SCAN_LIMIT = 12

METRIC_COL_MIN_FILL = 3

METRIC_COL_MIN_FILL_RATIO = 0.03

METRIC_COL_MIN_FILL_CAP = 20

_IDENT_COL_RE = re.compile(r"序号|编号|代码|行次|行号")

DOC_KEY_WORDS = ("项目名称", "数据格式", "字段", "名称", "项目", "指标")

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


def _cell_to_value(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)  # v2 语义：整数值浮点归一为 int（JSON 干净 + 维度哈希稳定）
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


def _is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _row_is_empty(row):
    return all(_is_blank(v) for v in row)


def _row_fill(row):
    return sum(1 for v in row if not _is_blank(v))


def _text_len(v):
    return len(str(v).strip()) if v is not None else 0


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _norm_matrix(matrix):
    n_cols = max((len(r) for r in matrix), default=0)
    return [list(r) + [None] * (n_cols - len(r)) for r in matrix]


def _is_preamble_row(row):
    if _row_is_empty(row):
        return False
    filled = [(i, v) for i, v in enumerate(row) if not _is_blank(v)]
    if not filled:
        return False
    text = " ".join(str(v).strip() for _, v in filled)
    text_norm = re.sub(r"\s+", "", text)
    if len(filled) == 1:
        if any(re.search(p, text_norm) for p in TITLE_PATTERNS):
            return True
        # 单值长文本标题行（"附件11-1"/表名长标题等）：≥6 字且无字段关键词 → 前导行。
        if len(text_norm) >= 6 and not any(
                w in text_norm for w in ("名称", "日期", "金额", "类型", "代码", "编号")):
            return True
    for p in PREAMBLE_PATTERNS + FOOTER_PATTERNS:
        if re.search(p, text_norm):
            return True
    return False


def _find_header_start(matrix):
    r, n = 0, len(matrix)
    while r < n and (_row_is_empty(matrix[r]) or _is_preamble_row(matrix[r])):
        r += 1
    return r


def _trim(matrix, r0, r1, c0, c1):
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


def split_blocks(matrix, merged):
    """按空行切分 block（合并间距≤1 的相邻块），返回 (sub, (r0,r1,c0,c1), local_merged)。"""
    if not matrix:
        return []
    norm = _norm_matrix(matrix)
    n_rows = len(norm)
    n_cols = len(norm[0])
    spans, inside, start = [], False, 0
    for r in range(n_rows):
        empty = _row_is_empty(norm[r])
        if not empty and not inside:
            start, inside = r, True
        elif empty and inside:
            spans.append([start, r])
            inside = False
    if inside:
        spans.append([start, n_rows])
    merged_spans = []
    for s in spans:
        if merged_spans and s[0] - merged_spans[-1][1] <= 1:
            merged_spans[-1][1] = s[1]
        else:
            merged_spans.append(s)
    out = []
    for r0, r1 in merged_spans:
        sub, nr0, nc0 = _trim(norm, r0, r1, 0, n_cols)
        if not sub:
            continue
        nr1, nc1 = nr0 + len(sub), nc0 + len(sub[0])
        local_merged = [MergedRegion(max(m.r1, nr0) - nr0, max(m.c1, nc0) - nc0,
                                     min(m.r2, nr1 - 1) - nr0, min(m.c2, nc1 - 1) - nc0)
                        for m in merged
                        if not (m.r2 < nr0 or m.r1 >= nr1 or m.c2 < nc0 or m.c1 >= nc1)]
        out.append((sub, (nr0, nr1, nc0, nc1), local_merged))
    return out


def _header_score(row):
    cells = [v for v in row if not _is_blank(v)]
    if not cells:
        return 0.0
    n = len(cells)
    hit = sum(1 for v in cells if isinstance(v, str)
              and any(w in v for w in HEADER_HINT_WORDS))
    text_ratio = sum(1 for v in cells if isinstance(v, str)) / n
    return 0.5 * text_ratio + 0.5 * (hit / n)


def _is_header_annotation_row(row):
    """表头注释行：位于数据区首部、维度区（前 3 列）全空、所有非空值均带注释前缀
    （"其中:交强险"/"1.3.4其中：责任保险"等多级列注释，1178105 实证）。"""
    vals = [v for v in row if not _is_blank(v)]
    if not vals:
        return False
    if not all(isinstance(v, str) for v in vals):
        return False
    if any(not _is_blank(v) for v in row[:3]):
        return False
    return all(_ANNOT_RE.match(str(v).strip()) for v in vals)


def _is_single_title_row(row):
    """单值长文本行（如分组标题 '一、常规指标'）→ 不作表头延续行（1187908 实证列名污染）。"""
    vals = [v for v in row if not _is_blank(v)]
    return len(vals) <= 1 and bool(vals) and _text_len(vals[0]) >= 6


def _looks_like_data_row(row):
    """数据样行：非空值 ≥2 且满足其一：①首非空值呈编号模式（'1-1'/'1.1'/'0001'）；
    ②数字值 ≥2 个；③任一非首值为 ≥3 位纯数字串（编码类：'001'/'001001'——EAST 数据结构/
    数据元说明表实证，此类行为数据行，防止 header 延续把数据值并入列名 path）。
    用于表头行延续判定（eff95012 s1 实证吃 3 行数据）。"""
    vals = [v for v in row if not _is_blank(v)]
    if len(vals) < 2:
        return False
    if _NUMCODE_RE.match(str(vals[0]).strip()):
        return True
    if sum(1 for v in vals if _is_number(v)) >= 2:
        return True
    return any(re.fullmatch(r"\d{3,}", str(v).strip()) for v in vals[1:])


def detect_header(matrix, merged):
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if matrix else 0
    if n_rows == 0 or n_cols == 0:
        return Region(0, 0, 0, 0)
    start = _find_header_start(matrix)
    if start >= n_rows:
        return Region(0, 1, 0, n_cols)
    max_scan = min(n_rows, start + 6)
    scores = [_header_score(matrix[r]) for r in range(start, max_scan)]
    merged_rows = set()
    for m in merged:
        # 仅「表头级跨行合并」（跨度 ≤3 行）作为表头证据；大跨度合并是数据区分组列
        # （EAST 实证：「数据元分类」c0 跨 74 行会把 header 拉满上限 → 列名被数据值污染）。
        if m.r2 > m.r1 and (m.r2 - m.r1) <= 3:
            for r in range(max(m.r1, start), min(m.r2 + 1, max_scan)):
                merged_rows.add(r)
    first = None
    for i, r in enumerate(range(start, max_scan)):
        if r in merged_rows:
            first = r
            break
        if scores[i] >= 0.4:
            # 单值标题样行（如 '附件11-1'/'压力测试明细表…'）不作表头起始（eff95012 实证：
            # 标题占 first 会把真表头区留在数据区 → 维度/指标错位）。
            vals = [v for v in matrix[r] if not _is_blank(v)]
            if len(vals) <= 1 and _text_len(vals[0]) >= 6 and not any(
                    w in str(vals[0]) for w in HEADER_HINT_WORDS):
                continue
            first = r
            break
    if first is None:
        first = start
    last = first
    for i, r in enumerate(range(first + 1, max_scan), start=1):
        if i > 3:   # 多级表头至多 4 行（含列号辅助行）
            break
        if r in merged_rows and not _is_single_title_row(matrix[r]) \
                and not _looks_like_data_row(matrix[r]):
            last = r
            continue
        if scores[i] >= 0.25 and not _looks_like_data_row(matrix[r]) \
                and not _is_single_title_row(matrix[r]):
            last = r
        else:
            break
    # 表头注释行并入（数据区首部 ≤2 行，如"其中:交强险"等子列注释）→ 列名更可读，
    # 且不再作为数据行产生稀疏 null。
    r, ext = last + 1, 0
    while r < n_rows and ext < 2 and _is_header_annotation_row(matrix[r]):
        last = r
        r += 1
        ext += 1
    return Region(first, last + 1, 0, n_cols)


def _fill_merged_header(matrix, merged, header):
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
        if _is_blank(val):
            continue
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


__all__ = ["MergedRegion", "Region", "_cell_to_value", "_fill_merged_header", "_find_header_start", "_forward_fill_row", "_header_score", "_is_blank", "_is_header_annotation_row", "_is_number", "_is_preamble_row", "_is_single_title_row", "_looks_like_data_row", "_norm_matrix", "_read_xls_bytes", "_read_xlsx_bytes", "_row_fill", "_row_is_empty", "_text_len", "_trim", "detect_header", "split_blocks"]
