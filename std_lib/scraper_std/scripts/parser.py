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

from ..cleaner import clean_text, is_table_block, normalize_ws  # noqa: F401
from ..crawler_common import (  # noqa: F401
    extract_document_text,
    garble_ratio,
    normalize_date,
    normalize_digits,
    sniff_kind,
)
from ..sentence_split import repair_text  # noqa: F401
from ..table_recovery import extract_tables_from_doc  # noqa: F401


def parse_document(data: bytes, name: str = "", *, enable_ocr: bool = True) -> dict:
    """文档字节 → 结构化解析结果（正文 + 表格 + 基础清洗）。"""
    ex = extract_document_text(data, name, enable_ocr=enable_ocr)
    text = (ex.get("text") or "").strip()
    out = dict(ex)
    if text:
        out["text_cleaned"] = clean_text(text)
        out["repaired"] = repair_text(out["text_cleaned"], source="document")
    out["tables"] = extract_tables_from_doc(data, name)
    return out


__all__ = [
    "extract_document_text", "sniff_kind", "garble_ratio", "normalize_date",
    "normalize_digits", "clean_text", "normalize_ws", "is_table_block",
    "repair_text", "extract_tables_from_doc", "parse_document",
]
