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

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

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


# ---- 拆分（2026-09-13，P3）：下列符号迁 excel_matrix，re-export 保持外部调用兼容 ----
# 注：std_lib 包内模块须用相对导入（collectors 目录为 sys.path 场景用裸名；2026-09-13 实证）
from .excel_matrix import (  # noqa: F401  拆分 re-export（显式；规避 F405）
    MergedRegion,
    Region,
    _cell_to_value,
    _fill_merged_header,
    _find_header_start,
    _forward_fill_row,
    _header_score,
    _is_blank,
    _is_header_annotation_row,
    _is_number,
    _is_preamble_row,
    _is_single_title_row,
    _looks_like_data_row,
    _norm_matrix,
    _read_xls_bytes,
    _read_xlsx_bytes,
    _row_fill,
    _row_is_empty,
    _text_len,
    _trim,
    detect_header,
    split_blocks,
)


@dataclass
class Column:
    key: str
    name: str
    path: list
    col_index: int = -1


@dataclass
class TableBlock:
    table_id: str
    matrix: list
    merged: list
    header_region: Region | None = None
    data_region: Region | None = None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    form_category: str = "unknown"
    doc_subtype: str | None = None
    meta: dict = field(default_factory=dict)


# ===========================================================================
# IO（bytes）
# ===========================================================================


# ===========================================================================
# 基础工具
# ===========================================================================


TITLE_PATTERNS = [
    r"^附录[一二三四五六七八九十\d]",
    r"^附件",
    r"^附表",
    r"统计表\s*$",
    r"填制说明\s*$",
    r"采集表\s*$",
    r"目录\s*$",
    r"说明\s*$",
]
PREAMBLE_PATTERNS = [
    r"^\d{4}\s*年.*月.*日",
    r"^20[×xX]+\s*年",
    r"^填报机构",
    r"^填报单位",
    r"^填报日期",
    r"^单位[:：]",
    r"^金额单位",
    r"^制表单位",
    r"^报告期",
    r"^公司名称",
    r"^机构名称",
    r"^被审计单位",
]
FOOTER_PATTERNS = [
    r"^制表[:：]",
    r"^审核[:：]",
    r"^说明[:：]",
    r"^注[:：]",
    r"^填表人",
    r"^负责人[:：]",
]


# ===========================================================================
# 表块切分
# ===========================================================================


# ===========================================================================
# 表头识别 + 多级列构建
# ===========================================================================

HEADER_HINT_WORDS = {
    "序号",
    "代码",
    "编号",
    "名称",
    "地区",
    "区域",
    "项目",
    "指标",
    "主题域",
    "表名",
    "数据项",
    "字段",
    "值",
    "单位",
    "合计",
    "总计",
    "类型",
    "状态",
    "日期",
    "时间",
    "备注",
    "规则",
    "说明",
    "金额",
    "机构",
    "目录",
}


_ANNOT_RE = re.compile(r"^[\d.．]*\s*(其中|注[:：]?|说明[:：]|备注[:：])")


_NUMCODE_RE = re.compile(r"^\d+([-－./]\d+)*$")


_KEY_SAFE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _make_key(path, idx):
    parts = []
    for p in path:
        p2 = _KEY_SAFE.sub("_", str(p)).strip("_")
        if p2:
            parts.append(p2)
    return "__".join(parts) if parts else f"col_{idx + 1}"


def build_columns(matrix, merged, header):
    h = [_forward_fill_row(row) for row in _fill_merged_header(matrix, merged, header)]
    # 列号辅助行（多级表头下的 '1 2 3 4…' 序号行，eff95012 实证）不参与列名 path：
    # 其纯数字段会把列名污染成 '…__1'；真实年份（4 位数）不受影响（_is_index_number_row 需全行数字）。
    h = [row for row in h if not _is_index_number_row(row)]
    n_cols = header.col_end - header.col_start
    columns = []
    for c in range(n_cols):
        path = []
        for r in range(len(h)):
            v = h[r][c]
            if _is_blank(v):
                continue
            s = re.sub(r"\s+", " ", str(v).strip())  # 列名空白折叠（\n 换行 → 空格）
            if re.fullmatch(r"[0-9]{1,3}", s):
                continue  # 列序号注释段（'1'/'2'…）不进列名（真年份 4 位不受影响）
            if not path or path[-1] != s:
                path.append(s)
        if not path:
            path = [f"col_{c + 1}"]
        columns.append(Column(key=_make_key(path, c), name=path[-1], path=path, col_index=c))
    seen = {}
    for col in columns:
        if col.key in seen:
            seen[col.key] += 1
            col.key = f"{col.key}__{seen[col.key]}"
        else:
            seen[col.key] = 0
    return columns


# ===========================================================================
# 数据行提取
# ===========================================================================


def _fill_merged_in_data(matrix, merged, data, horizontal=False):
    """数据区合并单元格填充（2026-09-10 修正语义）：
    - **纵向**（同列跨行）：默认填充 → 维度值/层级延续（"地区"跨行等）；
    - **横向**（同行跨列）：默认**不扩散**（值仅保留在合并区起始列）→ 跨列合并值本属
      首列，扩散会造成冗余重复值并污染指标列（"其中：xx"行/汇总行等实证）；
      表头区多级列名需扩散，由 _fill_merged_header 单独处理（horizontal=True 可显式打开）。
    """
    m2 = [list(r) for r in matrix]
    for m in merged:
        r1, r2 = max(m.r1, data.row_start), min(m.r2, data.row_end - 1)
        c1, c2 = max(m.c1, data.col_start), min(m.c2, data.col_end - 1)
        if r2 < r1 or c2 < c1:
            continue
        if m.r1 >= len(m2) or m.c1 >= len(m2[m.r1]):
            continue
        val = m2[m.r1][m.c1]
        if _is_blank(val):
            continue
        target_cols = range(c1, c2 + 1) if (horizontal or c1 == c2) else [c1]
        for r in range(r1, r2 + 1):
            if r >= len(m2):
                continue
            for c in target_cols:
                if c < len(m2[r]) and _is_blank(m2[r][c]):
                    m2[r][c] = val
    return m2


def _is_index_number_row(row):
    """列号辅助行：非空值 ≥3 且全为整数数字（如多级表头下的 '1 2 3 4 …' 列序号行，
    eff95012 实证）→ 属表头残留，不应作为数据行。"""
    vals = [v for v in row if not _is_blank(v)]
    if len(vals) < 3:
        return False
    for v in vals:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if not float(v).is_integer() or abs(v) > 100000:
            return False
    return True


def extract_rows(matrix, merged, data, columns, filter_preamble=True):
    filled = _fill_merged_in_data(matrix, merged, data)
    rows = []
    head_limit = min(data.row_start + 2, data.row_end)  # 仅对数据区首 2 行做列号行剔除
    for r in range(data.row_start, data.row_end):
        if r >= len(filled):
            break
        src = filled[r]
        if filter_preamble and _is_preamble_row(src):
            continue
        if filter_preamble and r < head_limit and _is_index_number_row(src):
            continue
        row, has_value = {}, False
        for col in columns:
            idx = data.col_start + col.col_index
            v = src[idx] if idx < len(src) else None
            row[col.key] = v
            if not _is_blank(v):
                has_value = True
        if has_value:
            rows.append(row)
    return rows


# ===========================================================================
# 统计表 / 说明表 分类（长文本 / 列数 / 关键词）
# ===========================================================================

DOC_HINT_WORDS = {
    "说明",
    "描述",
    "释义",
    "定义",
    "备注",
    "解释",
    "填报",
    "要求",
    "规范",
    "示例",
    "内容",
    "格式",
}


def classify_block(matrix, merged):
    """返回 (form_category, meta)：统计表 | 说明表 | 未知。"""
    if not matrix:
        return "未知", {}
    n_rows = len(matrix)
    n_cols = max((len(r) for r in matrix), default=0)
    if n_rows == 0 or n_cols == 0:
        return "未知", {}
    max_avg, max_long_ratio = 0.0, 0.0
    for c in range(n_cols):
        vals = [r[c] for r in matrix if c < len(r) and not _is_blank(r[c])]
        if not vals:
            continue
        avg = sum(_text_len(v) for v in vals) / len(vals)
        lr = sum(1 for v in vals if _text_len(v) >= 30) / len(vals)
        max_avg = max(max_avg, avg)
        max_long_ratio = max(max_long_ratio, lr)
    overall = [v for r in matrix for v in r if not _is_blank(v)]
    long_ratio = sum(1 for v in overall if _text_len(v) >= 30) / len(overall) if overall else 0
    header_text = "".join(str(v) for v in matrix[0] if not _is_blank(v))
    doc_hit = sum(1 for w in DOC_HINT_WORDS if w in header_text)
    meta = {
        "n_rows": n_rows,
        "n_cols": n_cols,
        "max_avg_len": round(max_avg, 1),
        "max_long_ratio": round(max_long_ratio, 3),
        "overall_long_ratio": round(long_ratio, 3),
        "doc_hit": doc_hit,
    }
    if max_long_ratio >= 0.6:
        return "说明表", meta
    if n_cols <= 2 and long_ratio >= 0.3:
        return "说明表", meta
    if doc_hit >= 1 and long_ratio >= 0.2:
        return "说明表", meta
    if long_ratio >= 0.25:
        return "说明表", meta
    if n_cols <= 3:
        return "说明表", meta
    return "统计表", meta


# ===========================================================================
# 维度列 / 维度行（方案 C 核心）
# ===========================================================================

DIM_MAX_NUM_RATIO = 0.15
DIM_MIN_FILL_RATIO = 0.5
DIM_MAX_AVG_LEN = 30
DIM_SCAN_LIMIT = 12

# 指标列保留阈值（2026-09-10）：列在保留行上的非空数 ≥ clamp(3%×行数, 3, 20) 才输出。
# 治理 Excel 格式残留"幽灵列"（f24b3723 实证：16384 列中 16376 列仅 1-2 个杂值，
# 产生 140 万 null）；同时不误伤真实稀疏列（3379 行表阈值 20，>=20 值保留）。
METRIC_COL_MIN_FILL = 3
METRIC_COL_MIN_FILL_RATIO = 0.03
METRIC_COL_MIN_FILL_CAP = 20


_IDENT_COL_RE = re.compile(r"序号|编号|代码|行次|行号")


def detect_dimension_columns(rows, columns):
    """维度列：前缀连续满足（fill 高 / 数值率低 / 短文本）。
    行标识列（序号/编号/代码）容忍数值继续扫描（工程增强，f24b 公司编号实证）。"""
    if not rows or not columns:
        return []
    n = len(rows)
    dim_cols = []
    for col in columns[:DIM_SCAN_LIMIT]:
        vals = [r.get(col.key) for r in rows]
        valid = [v for v in vals if not _is_blank(v)]
        if not valid:
            break
        fill = len(valid) / n
        num_ratio = sum(1 for v in valid if _is_number(v)) / len(valid)
        avg_len = sum(_text_len(v) for v in valid) / len(valid)
        ok = (
            fill >= DIM_MIN_FILL_RATIO
            and num_ratio <= DIM_MAX_NUM_RATIO
            and avg_len <= DIM_MAX_AVG_LEN
        )
        if ok:
            dim_cols.append(col.key)
            continue
        if _IDENT_COL_RE.search(col.name or ""):
            dim_cols.append(col.key)  # 行标识维度（序号/编号/代码）
            continue
        break
    return dim_cols


def extract_dimension_rows(rows, dim_cols):
    """维度行：与 rows **同索引一一对齐**（不过滤，保证与 metric_rows 行对齐）。"""
    return [[r.get(k) for k in dim_cols] for r in rows]


def extract_metric_rows(rows, metric_cols):
    return [[r.get(k) for k in metric_cols] for r in rows]


def hash_dimension(columns, rows):
    payload = {"columns": list(columns), "rows": rows}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# 说明表 subtype 处理（narrative / key_value / indicator_doc）
# ===========================================================================

DOC_KEY_WORDS = ("项目名称", "数据格式", "字段", "名称", "项目", "指标")


def _detect_doc_subtype(matrix):
    n_cols = max((len(r) for r in matrix), default=0)
    if n_cols <= 1:
        return "narrative", {}
    if n_cols == 2:
        return "key_value", {"key_column_index": 0, "value_column_index": 1}
    filled = [r for r in matrix if not _row_is_empty(r)]
    header_row = filled[0] if filled else []
    key_idx = 0
    for i, v in enumerate(header_row[:3]):
        if isinstance(v, str) and any(w in v for w in DOC_KEY_WORDS):
            key_idx = i
            break
    return "indicator_doc", {"key_column_index": key_idx}


def _extract_doc_title(matrix):
    for row in matrix:
        vals = [v for v in row if not _is_blank(v)]
        if vals and _text_len(vals[0]) >= 8:
            return str(vals[0]).strip()
    return None


def process_doc_block(block):
    matrix = block.matrix
    merged = block.merged
    subtype, _ = _detect_doc_subtype(matrix)
    title = _extract_doc_title(matrix)
    filled = [r for r in matrix if not _row_is_empty(r)]
    block.doc_subtype = subtype
    block.meta["doc_subtype"] = subtype
    block.meta["title"] = title

    if subtype == "narrative":
        paragraphs = []
        for r in filled:
            for v in r:
                if not _is_blank(v):
                    paragraphs.append(str(v).strip())
                    break
        block.columns = [Column(key="paragraph", name="paragraph", path=["paragraph"], col_index=0)]
        block.rows = [{"paragraph": p} for p in paragraphs]
        block.meta["paragraph_count"] = len(paragraphs)
        return

    # ---- key_value / indicator_doc：多级表头 + 合并单元格处理（2026-09-10 重构）----
    # ① 全矩阵纵向合并填充（键列跨行延续；横向不扩散）；
    # ② 表头检测（跳标题/前导行；表头区纵横填充）→ 多级列名 path→key；
    # ③ 数据行提取（跳过空行/前导行/列号行）；④ 全空列裁剪。
    n_cols = max((len(r) for r in matrix), default=0)
    m2 = _fill_merged_in_data(matrix, merged, Region(0, len(matrix), 0, n_cols))
    header = detect_header(m2, merged)
    block.header_region = header
    columns = build_columns(m2, merged, header)
    data = Region(header.row_end, len(m2), 0, n_cols)
    rows = extract_rows(m2, merged, data, columns)
    keep = [c for c in columns if any(not _is_blank(r.get(c.key)) for r in rows)] or columns
    items = [{c.key: r.get(c.key) for c in keep} for r in rows]
    raw_rows = (
        [[r.get(c.key) for c in keep] for r in rows]
        if any((c.key or "").startswith("col_") for c in keep)
        else None
    )
    block.columns = keep
    block.rows = items
    key_col = next(
        (c for c in keep if any(w in (c.name or "") for w in DOC_KEY_WORDS)),
        keep[0] if keep else None,
    )
    block.meta["key_column"] = key_col.key if key_col else None
    block.meta["item_count"] = len(items)
    block.meta["dropped_empty_columns"] = [c.name for c in columns if c not in keep]
    if raw_rows is not None:
        block.meta["raw_rows"] = raw_rows


# ===========================================================================
# Block 处理 / 单元构建 / 聚合
# ===========================================================================


def process_block(sheet_index, block_index, matrix, merged):
    block = TableBlock(table_id=f"{sheet_index}-{block_index}", matrix=matrix, merged=merged)
    form_cat, meta = classify_block(matrix, merged)
    block.form_category = form_cat
    block.meta.update(meta)
    if form_cat == "说明表":
        process_doc_block(block)
        return block
    header = detect_header(matrix, merged)
    block.header_region = header
    n_rows = len(matrix)
    n_cols = max((len(r) for r in matrix), default=0)
    block.data_region = Region(header.row_end, n_rows, 0, n_cols)
    block.columns = build_columns(matrix, merged, header)
    block.rows = extract_rows(matrix, merged, block.data_region, block.columns)
    return block


def _build_stat_unit(block, sheet_name, sheet_index):
    columns = block.columns
    rows = block.rows
    dim_cols = detect_dimension_columns(rows, columns)
    metric_all = [c.key for c in columns if c.key not in dim_cols]

    def _has_metric(r):
        return any(not _is_blank(r.get(k)) for k in metric_all)

    # ① 行级：指标有值行 ≥ 阈值（max(2, 1%)）才视为「有数据表」→ 裁剪名录行；
    #    否则视为空填报模板 → 保留维度名录行、不输出 metric_rows（meta.no_metric_data=True），
    #    消除海量 null（1178105 实证：3379 行名录 + 21 列全空 → null 71k → 0）。
    metric_value_rows = [r for r in rows if _has_metric(r)] if metric_all else []
    any_metric = len(metric_value_rows) >= max(2, int(0.01 * len(rows)))
    if any_metric:
        rows = metric_value_rows
    # ② 列级：剔除稀疏/空指标列（幽灵列只在**超宽表**出现；小表数据完整性优先不裁）。
    #    大表阈值 clamp(3%×行数, 3, 20)；裁剪后若为空则回退保留有值列（防全丢）。
    kept_metric_cols, dropped_count, dropped_sample = [], 0, []
    if any_metric:
        _fills = {
            c.key: sum(1 for r in rows if not _is_blank(r.get(c.key)))
            for c in columns
            if c.key in metric_all
        }
        if len(columns) < 32:
            kept_metric_cols = [c for c in columns if c.key in metric_all and _fills[c.key] >= 1]
        else:
            min_fill = min(
                max(METRIC_COL_MIN_FILL, int(METRIC_COL_MIN_FILL_RATIO * len(rows) + 0.999)),
                METRIC_COL_MIN_FILL_CAP,
            )
            kept_metric_cols = [
                c for c in columns if c.key in metric_all and _fills[c.key] >= min_fill
            ]
            if not kept_metric_cols:  # 回退：防"阈值高于所有列"时整表数据丢失
                kept_metric_cols = [
                    c for c in columns if c.key in metric_all and _fills[c.key] >= 1
                ]
        dropped = [
            c
            for c in columns
            if c.key in metric_all and c.key not in {k.key for k in kept_metric_cols}
        ]
        dropped_count = len(dropped)
        dropped_sample = [c.name for c in dropped[:20]]

    # 维度哈希用「末级列名 + 维度行值」：不同表块的列 key 因多级表头 path 前缀（各表标题）
    # 不同，但维度语义（序号/行政区域代码/地区…）与行值相同时应合并为同一 table_set。
    dim_names = [c.name for c in columns if c.key in dim_cols]
    dim_rows = extract_dimension_rows(rows, dim_cols)
    dim_hash = hash_dimension(dim_names, dim_rows) if dim_rows else ""

    # 输出列投影：维度列 + 保留指标列；空模板（无数据）保留 fill≥2 的列（原始阅读）。
    if any_metric:
        kept_keys = dim_cols + [c.key for c in kept_metric_cols]
    else:
        kept_keys = [
            c.key
            for c in columns
            if c.key in dim_cols or sum(1 for r in rows if not _is_blank(r.get(c.key))) >= 2
        ]
    projection = [{k: r.get(k) for k in kept_keys} for r in rows]

    meta = dict(block.meta)
    meta["row_count"] = len(rows)
    if not any_metric:
        meta["no_metric_data"] = True
        meta["declared_metric_columns"] = [c.name for c in columns if c.key in metric_all]
    elif dropped_count:
        meta["dropped_metric_columns"] = {"count": dropped_count, "sample": dropped_sample}
    return {
        "sheet_name": sheet_name,
        "sheet_index": sheet_index,
        "table_id": block.table_id,
        "type": "统计表",
        "row_count": len(rows),
        "columns": kept_keys,  # 输出列（无维度回退用）
        "rows": projection,  # 输出行（无维度回退用）
        "dimension_columns": dim_cols,
        "dimension_names": dim_names,
        "dimension_rows": dim_rows,
        "dimension_hash": dim_hash,
        "metric_columns": [c.key for c in kept_metric_cols],
        "metric_names": [c.name for c in kept_metric_cols],
        "metric_rows": extract_metric_rows(rows, [c.key for c in kept_metric_cols])
        if kept_metric_cols
        else [],
        "meta": meta,
    }


def _build_doc_unit(block, sheet_name, sheet_index):
    meta = dict(block.meta)
    raw_rows_meta = meta.pop("raw_rows", None)  # meta 不留大数组副本（去冗余 null）
    unit = {
        "sheet_name": sheet_name,
        "sheet_index": sheet_index,
        "table_id": block.table_id,
        "type": "说明表",
        "subtype": block.doc_subtype or "narrative",
        "title": meta.get("title"),
        "key_column": meta.get("key_column"),
        "columns": [c.key for c in block.columns],
        "item_count": meta.get("item_count", len(block.rows)),
        "items": block.rows,
        "meta": meta,
    }
    # raw_rows 仅当列名缺失（col_N 兜底）时保留原貌；列名明确时 items 已含全信息。
    col_keys = [c.key for c in block.columns]
    if raw_rows_meta and any(k.startswith("col_") for k in col_keys):
        unit["raw_rows"] = raw_rows_meta
    return unit


def _stat_sheet(unit, *, with_dim_rows=True, full_rows=False):
    """统计表独立单元（单 variant 退回 / 无维度回退）。空指标/空数组键省略。"""
    out = {
        "sheet_name": unit["sheet_name"],
        "sheet_index": unit["sheet_index"],
        "table_id": unit["table_id"],
        "type": unit["type"],
        "row_count": unit["row_count"],
        "meta": unit["meta"],
    }
    if full_rows:
        out["columns"] = unit["columns"]
        out["rows"] = unit["rows"]  # 行已含指标值，不再单列 metric_rows
    if with_dim_rows:
        out["dimension_columns"] = unit["dimension_columns"]
        out["dimension_rows"] = unit["dimension_rows"]
    if unit["metric_columns"] and not full_rows:
        out["metric_columns"] = unit["metric_columns"]
        out["metric_names"] = unit.get("metric_names")
        out["metric_rows"] = unit["metric_rows"]  # 数据完整性加固（v2 原版无）
    return out


def aggregate_units(units):
    row_sets, table_sets_map, standalone = {}, {}, []
    for u in units:
        if u["type"] == "说明表":
            sheet = {
                "sheet_name": u["sheet_name"],
                "sheet_index": u["sheet_index"],
                "table_id": u["table_id"],
                "type": "说明表",
                "subtype": u["subtype"],
                "title": u["title"],
                "key_column": u.get("key_column"),
                "columns": u["columns"],
                "item_count": u["item_count"],
                "items": u["items"],
                "meta": u["meta"],
            }
            if u.get("raw_rows"):
                sheet["raw_rows"] = u["raw_rows"]
            standalone.append(sheet)
            continue
        # 维度列为空 → 无有效维度（防 dimension_rows 为"空列表行"数组时误建 table_set，
        # eff95012 实证：dimension_columns=[] 却因 rows=[[],[]…] 非空建了 dim=[] 的 table_set）
        if not (u["dimension_columns"] and u["dimension_rows"]):
            standalone.append(_stat_sheet(u, with_dim_rows=False, full_rows=True))
            continue
        h = u["dimension_hash"]
        if h not in row_sets:
            row_sets[h] = {
                "dimension_ref": f"dims_{h}",
                "columns": u.get("dimension_names") or u["dimension_columns"],
                "row_count": len(u["dimension_rows"]),
                "rows": u["dimension_rows"],
            }
        if h not in table_sets_map:
            table_sets_map[h] = {
                "table_set_id": f"ts_{h}",
                "dimension_ref": f"dims_{h}",
                "dimension_columns": u["dimension_columns"],
                "row_count": u["row_count"],
                "variants": [],
            }
        var = {
            "sheet_name": u["sheet_name"],
            "sheet_index": u["sheet_index"],
            "table_id": u["table_id"],
            "type": u["type"],
            "row_count": u["row_count"],
            "meta": u["meta"],
        }
        if u["metric_columns"]:
            var["metric_columns"] = u["metric_columns"]
            var["metric_names"] = u.get("metric_names")
            var["metric_rows"] = u["metric_rows"]
        table_sets_map[h]["variants"].append(var)
    final_sets = []
    for h, ts in table_sets_map.items():
        if len(ts["variants"]) >= 2:
            final_sets.append(ts)
        else:
            v = ts["variants"][0]
            rs = row_sets[h]
            sheet = {
                "sheet_name": v["sheet_name"],
                "sheet_index": v["sheet_index"],
                "table_id": v["table_id"],
                "type": v["type"],
                "row_count": v["row_count"],
                "dimension_columns": rs["columns"],
                "dimension_rows": rs["rows"],
                "meta": v["meta"],
            }
            if v.get("metric_columns"):
                sheet["metric_columns"] = v["metric_columns"]
                sheet["metric_names"] = v.get("metric_names")
                sheet["metric_rows"] = v.get("metric_rows")
            standalone.append(sheet)
    return {"row_sets": row_sets, "table_sets": final_sets, "sheets": standalone}


# ===========================================================================
# 主入口（bytes）
# ===========================================================================


def process_workbook_bytes(data: bytes, source_file: str = "") -> dict:
    """Excel bytes → 分类化结构（excel_classified_v2）。xls 需 xlrd、xlsx 需 openpyxl。"""
    suffix = os.path.splitext(source_file or "")[1].lower()
    raw_sheets = _read_xls_bytes(data) if suffix == ".xls" else _read_xlsx_bytes(data)

    all_units, sheet_overview, form_summary = [], [], {}
    for sheet_index, (sheet_name, matrix, merged) in enumerate(raw_sheets, start=1):
        if not matrix:
            sheet_overview.append(
                {
                    "sheet_name": sheet_name,
                    "sheet_index": sheet_index,
                    "type": "空表",
                    "row_count": 0,
                }
            )
            continue
        blocks = split_blocks(matrix, merged)
        sheet_type, total_rows = "未知", 0
        for bi, (sub, _span, local_merged) in enumerate(blocks, start=1):
            blk = process_block(sheet_index, bi, sub, local_merged)
            unit = (
                _build_doc_unit(blk, sheet_name, sheet_index)
                if blk.form_category == "说明表"
                else _build_stat_unit(blk, sheet_name, sheet_index)
            )
            all_units.append(unit)
            total_rows += unit.get("row_count", 0)
            if sheet_type == "未知":
                sheet_type = blk.form_category
            form_summary[blk.form_category] = form_summary.get(blk.form_category, 0) + 1
        sheet_overview.append(
            {
                "sheet_name": sheet_name,
                "sheet_index": sheet_index,
                "type": sheet_type,
                "row_count": total_rows,
            }
        )

    agg = aggregate_units(all_units)
    return {
        "kind": "excel_classified",
        "schema": SCHEMA_VERSION,
        "schema_version": "1.0",
        "source_file": source_file,
        "form_summary": form_summary,
        "sheet_overview": sheet_overview,
        "row_sets": agg["row_sets"],
        "table_sets": agg["table_sets"],
        "sheets": agg["sheets"],
    }
