# -*- coding: utf-8 -*-
"""
table_recovery.py —— 表格类文档提取后的结构修复专项（第七节 7.2.5）

技术红线（⑦）：提取插件输出格式优先级：
  1. 首选 list[list[str]]（二维字符串数组，保留行列结构）；
  2. 次选 list[dict]（字典列表，以表头为键）；
  3. 禁止：先拼接纯文本再正则反恢复。

专项修复流程：
  ① 行列边界识别与还原（优先利用工具原始单元格边界；纯文本时按行标记修复；
     列分隔默认 '|'，数据字典注明）；
  ② 多维表头（合并单元格）语义还原：合并信息向下/向右填充，扁平化列名
     （如 "2024年_一季度_营收"），保留原始表头层级结构；
  ③ 跨页表格拼接与去重：移除重复表头行，仅保留首份表头；"续前页"标记移除；
     合计行单独标记；
  ④ 特殊字符与空白单元格：空单元格填 ""，\n/\t 替换为空格，单位保留在单元格值；
  ⑤ 兜底：table_structured=[]，table_raw_text=原始提取文本，
     table_recovery_method="raw_only"，供人工复核。
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

LOG = logging.getLogger("scraper_std.table_recovery")

_CONTINUE_MARK = re.compile(r"^(续前页|接上页|续表|上接第.*页|第[一二三四五六七八九十\d]+页)")
_TOTAL_MARK = re.compile(r"^(合计|总计|小计|累计)")
_ILLEGAL_IN_CELL = re.compile(r"[\n\r\t]+")


def normalize_cell(value: Any) -> str:
    """单元格清洗：None→''；\n/\t→空格；去首尾空白。"""
    if value is None:
        return ""
    return _ILLEGAL_IN_CELL.sub(" ", str(value)).strip()


def normalize_table(rows: list[list[Any]]) -> list[list[str]]:
    """④ 空白单元格填充 + 特殊字符替换 + 行宽对齐。"""
    if not rows:
        return []
    width = max((len(r) for r in rows), default=0)
    out: list[list[str]] = []
    for r in rows:
        cells = [normalize_cell(c) for c in r]
        cells += [""] * (width - len(cells))
        out.append(cells)
    return out


# 表头关键词（首列强信号 + 全文弱信号；数据行通常首列为编号/数值）
_STRONG_HEADER_FIRST = ("序号", "项目", "指标", "科目", "类别", "名称", "年度",
                        "月份", "合计", "总计", "单位", "编号", "代码", "日期")
_HEADER_WEAK_KW = ("序号", "项目名称", "金额单位", "备注", "数量")


def is_header_row(row: list[str], prev_header: list[str] | None = None) -> bool:
    """
    启发式判断表头行：
      - 首列以数字开头 → 判定为数据行（非表头），防误删；
      - 首列命中强表头词（且非空比例≥0.5）或全文含弱表头关键词 → 表头；
      - 与前表头行相似度≥0.7 → 跨页重复表头。
    """
    cells = [str(c).strip() for c in row]
    if not cells or not cells[0]:
        return False
    if re.match(r"^\d", cells[0]):
        return False  # 数据行首列常为编号/数值
    joined = "|".join(cells)
    filled = sum(1 for c in cells if c)
    if cells[0] in _STRONG_HEADER_FIRST or any(k in joined for k in _HEADER_WEAK_KW):
        if filled / max(1, len(cells)) >= 0.5:
            return True
    if prev_header is not None and row and prev_header:
        same = sum(1 for a, b in zip(cells, prev_header, strict=False) if a == b)
        if same / max(1, min(len(cells), len(prev_header))) >= 0.7:
            return True
    return False


def fill_merged_headers(rows: list[list[str]]) -> list[list[str]]:
    """
    ② 多维表头合并单元格语义还原：向下填充（列方向延续值）。
    返回填充后的行列表（列方向上下文完整）。
    """
    if not rows:
        return []
    width = max((len(r) for r in rows), default=0)
    grid = [r + [""] * (width - len(r)) for r in rows]
    for col in range(width):
        carry = ""
        for r in grid:
            if r[col]:
                carry = r[col]
            else:
                r[col] = carry
    return grid


def flatten_header_chain(rows: list[list[str]], header_rows: int | None = None) -> list[str]:
    """
    将前 header_rows 行拼接为扁平化列名（"2024年_一季度_营收"），
    数据字典中保留原始表头层级结构。header_rows 缺省取全部行。
    """
    if not rows:
        return []
    if header_rows is None:
        header_rows = len(rows)
    header_rows = max(1, min(header_rows, len(rows)))
    cols = max((len(r) for r in rows[:header_rows]), default=0)
    names: list[str] = []
    for c in range(cols):
        parts = []
        for r in rows[:header_rows]:
            v = r[c] if c < len(r) else ""
            if v and v not in parts:
                parts.append(v)
        names.append("_".join(parts) if parts else f"col_{c + 1}")
    return names


def remove_dup_headers(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """
    ③ 跨页表格拼接去重：移除重复表头行（仅保留首份）。
    返回 (清理后行列表, 移除行数)。同时移除“续前页”等标记行，
    并将合计行标记为 "【合计】..." 前缀单独标识。
    """
    if not rows:
        return [], 0
    kept: list[list[str]] = []
    removed = 0
    header_template: list[str] | None = None
    for r in rows:
        joined = " ".join(c for c in r if c)
        if _CONTINUE_MARK.search(joined.strip()):
            removed += 1
            continue
        if _TOTAL_MARK.search(joined.strip()):
            # 合计行先于表头判定：单独标记，避免与明细混淆（7.2.5③）
            kept.append(["【合计】"] + [c for c in r if c][1:])
            continue
        if is_header_row(r, header_template):
            if header_template is None:
                header_template = r
                kept.append(r)
            else:
                removed += 1  # 跨页重复表头 → 移除
            continue
        kept.append(r)
    return kept, removed


def extract_tables_from_doc(
    data: bytes,
    name: str = "",
    *,
    kind: str | None = None,
) -> dict[str, Any]:
    """
    从文档字节中提取全部表格（结构化优先）。返回：
      {
        "tables": [list[list[str]], ...],        # 结构化二维数组（首选输出）
        "table_raw_text": str,                    # 兜底原始文本
        "recovery_method": "structured" | "raw_only",
        "table_count": int,
        "rows_total": int,
        "cols_max": int,
        "removed_header_rows": int,
        "notes": [str],
      }
    kind 可显式指定（pdf/docx/xlsx/ole2）；缺省自动嗅探。
    """
    from .crawler_common import sniff_kind
    kind = kind or sniff_kind(data, name)
    notes: list[str] = []
    tables: list[list[list[str]]] = []

    try:
        if kind == "pdf":
            tables, notes = _tables_from_pdf(data)
        elif kind == "docx":
            tables, notes = _tables_from_docx(data)
        elif kind == "xlsx":
            tables, notes = _tables_from_xlsx(data)
        elif kind == "ole2":
            tables, notes = _tables_from_ole2(data)
        else:
            return {
                "tables": [], "table_raw_text": "",
                "recovery_method": "raw_only", "table_count": 0,
                "rows_total": 0, "cols_max": 0, "removed_header_rows": 0,
                "notes": [f"unsupported kind {kind}"],
            }
    except Exception as e:
        LOG.warning("表格提取异常 %s: %s", name, e)
        return {
            "tables": [], "table_raw_text": "",
            "recovery_method": "raw_only", "table_count": 0,
            "rows_total": 0, "cols_max": 0, "removed_header_rows": 0,
            "notes": [f"extract_error: {e}"],
        }

    # 统一后处理：规范化 + 合并表头填充 + 跨页去重
    cleaned_tables: list[list[list[str]]] = []
    removed_total = 0
    for t in tables:
        t = normalize_table(t)
        if not t:
            continue
        t = fill_merged_headers(t)
        t, removed = remove_dup_headers(t)
        removed_total += removed
        cleaned_tables.append(t)

    method = "structured" if cleaned_tables else "raw_only"
    raw_text = _tables_to_raw_text(cleaned_tables)
    rows_total = sum(len(t) for t in cleaned_tables)
    cols_max = max((len(t[0]) for t in cleaned_tables if t), default=0)
    return {
        "tables": cleaned_tables,
        "table_raw_text": raw_text,
        "recovery_method": method,
        "table_count": len(cleaned_tables),
        "rows_total": rows_total,
        "cols_max": cols_max,
        "removed_header_rows": removed_total,
        "notes": notes,
    }


def _tables_to_raw_text(tables: list[list[list[str]]], sep: str = "|") -> str:
    """结构化表格 → 可读文本（| 分隔，数据字典注明分隔符）。"""
    chunks = []
    for t in tables:
        chunks.append("\n".join(sep.join(r) for r in t))
    return "\n\n".join(chunks)


def _tables_from_pdf(data: bytes) -> tuple[list[list[list[str]]], list[str]]:
    """PDF 表格：pdfplumber 按页抽取（保留行列结构，技术红线首选）。"""
    import pdfplumber
    tables: list[list[list[str]]] = []
    notes: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            try:
                found = page.extract_tables()
            except Exception as e:
                notes.append(f"p{pno}: extract_tables error {e}")
                continue
            for tb in found or []:
                rows = [[(c or {}).get("text", "") if isinstance(c, dict) else (c or "")
                         for c in row] for row in tb]
                if rows and any(any(c.strip() for c in r) for r in rows):
                    tables.append(rows)
    return tables, notes


def _tables_from_docx(data: bytes) -> tuple[list[list[list[str]]], list[str]]:
    """Word 表格：python-docx 按表格/行/单元格抽取。"""
    import docx
    d = docx.Document(io.BytesIO(data))
    tables: list[list[list[str]]] = []
    for tb in d.tables:
        rows = [[c.text for c in row.cells] for row in tb.rows]
        if rows:
            tables.append(rows)
    return tables, []


def _tables_from_xlsx(data: bytes) -> tuple[list[list[list[str]]], list[str]]:
    """Excel 表格：openpyxl 读取所有 Sheet，二维数组输出。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    tables: list[list[list[str]]] = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c) for c in r])
        if rows and any(any(str(c).strip() for c in r) for r in rows):
            tables.append(rows)
    wb.close()
    return tables, []


def _tables_from_ole2(data: bytes) -> tuple[list[list[list[str]]], list[str]]:
    """旧版 .xls：xlrd 读取所有 Sheet（OLE2）。"""
    import xlrd
    bk = xlrd.open_workbook(file_contents=data)
    tables: list[list[list[str]]] = []
    for sh in bk.sheets():
        rows = [[str(c.value) for c in sh.row(r)] for r in range(sh.nrows)]
        if rows and any(any(str(c).strip() for c in r) for r in rows):
            tables.append(rows)
    return tables, []


if __name__ == "__main__":  # 离线自检（纯函数）
    # 规范化与对齐
    assert normalize_table([["a", None, "b\nc"], ["d"]]) == [["a", "", "b c"], ["d", "", ""]]
    # 合并单元格向下填充（纵向合并“2024年”跨三行）
    filled = fill_merged_headers([["2024年", "一季度", ""], ["", "营收", ""], ["", "100", ""]])
    assert filled[0][0] == "2024年" and filled[1][0] == "2024年" and filled[2][0] == "2024年"
    assert filled[2][1] == "100"  # 数据行未被表头污染
    # 跨页表头去重
    hdr = ["序号", "项目", "金额"]
    rows = [hdr, ["1", "收入", "100"], hdr, ["2", "支出", "50"], ["合计", "", "150"]]
    kept, removed = remove_dup_headers(rows)
    assert removed == 1
    assert any("合计" in r[0] for r in kept)
    # 扁平化列名
    names = flatten_header_chain([["2024年", "2024年"], ["一季度", "二季度"], ["营收", "营收"]])
    assert names[0] == "2024年_一季度_营收"
    print("[scraper_std.table_recovery] 离线自检通过")
