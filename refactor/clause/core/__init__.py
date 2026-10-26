# -*- coding: utf-8 -*-
"""refactor.clause.core — 条文抽取算法核心（源无关、storage-free）。

从 std_lib.scraper_std.document_structure 逐字抽取；仅依赖 re 与 common.enums（refactor 内受控词表），
不触碰任何存储/文件系统，便于独立单测与下游复用。
"""
from .document_structure import (
    extract_structure,
    parse_document,
    validate_clauses,
    render_markdown,
    normalize_parse_text,
    strip_document_tail,
    segment_body,
    segment_outline,
    repair_articles,
    dedup_articles,
    fix_chapter_index,
    parse_article_structure,
    score_parse,
    structure_semantics,
    CN_NUM_CHARS,
    CN_NUM_CLASS,
)

__all__ = [
    "extract_structure",
    "parse_document",
    "validate_clauses",
    "render_markdown",
    "normalize_parse_text",
    "strip_document_tail",
    "segment_body",
    "segment_outline",
    "repair_articles",
    "dedup_articles",
    "fix_chapter_index",
    "parse_article_structure",
    "score_parse",
    "structure_semantics",
    "CN_NUM_CHARS",
    "CN_NUM_CLASS",
]
