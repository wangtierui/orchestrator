# -*- coding: utf-8 -*-
"""
parser —— 解析/清洗模块再导出（单一事实源，对应四源 scripts/parser.py）。

职责：正文抽取、基础清洗、断句修复、OCR 校正、表格结构还原的按职责封装。
底层复用 crawler_common 与 scraper_std 的既有实现。相对导入（from ..X）确保
本模块随 scraper_std 包一起被解析。

用法示例：
    from scraper_std.scripts.parser import parse_document
    result = parse_document(data_bytes, "scan.pdf")
"""

from __future__ import annotations

from ..cleaner import clean_text, is_table_block, normalize_ws
from ..crawler_common import (
    extract_document_text,
    garble_ratio,
    normalize_date,
    normalize_digits,
    sniff_kind,
)
from ..sentence_split import repair_text
from ..table_recovery import extract_tables_from_doc


def parse_document(
    data: bytes, name: str = "", *, enable_ocr: bool = True, extract=None
) -> dict:
    """文档字节 → 结构化解析结果（正文 + 表格 + 基础清洗）。

    N-170（2026-09-30）：**抽取器可注入**（`extract=`）。本模块的默认抽取器基于
    `crawler_common`；而 `collectors/supp_parser.py` 的 supp 链路用 **pypdf 文本层 +
    Tesseract OCR 回退**（同一函数名、**不同实现**）。原两处 `parse_document` 逐字相同
    却各自绑定不同抽取器 → **不能直接合并**（合并会改变 supp 的抽取路径）。
    故 canonical 只拥有「正文 → 清洗 → 断句修复 → 表格」这段**真正相同**的流水线，
    抽取器由调用方注入（默认=`crawler_common` 版），差异被**显式参数化**而非复制。
    """
    ex = (extract or extract_document_text)(data, name, enable_ocr=enable_ocr)
    text = (ex.get("text") or "").strip()
    out = dict(ex)
    if text:
        out["text_cleaned"] = clean_text(text)
        out["repaired"] = repair_text(out["text_cleaned"], source="document")
    out["tables"] = extract_tables_from_doc(data, name)
    return out


__all__ = [
    "extract_document_text",
    "sniff_kind",
    "garble_ratio",
    "normalize_date",
    "normalize_digits",
    "clean_text",
    "normalize_ws",
    "is_table_block",
    "repair_text",
    "extract_tables_from_doc",
    "parse_document",
]
