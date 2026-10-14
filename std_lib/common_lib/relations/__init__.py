# -*- coding: utf-8 -*-
"""std_lib.common_lib.relations — 监管与制度依据·废止关系抽取（W4-c 三拆）。

原 1081 行单文件按职责拆分（方案 7.5.1，包 + 门面，import 路径不变）：
    patterns.py   模式集与文本工具（RelationConfig/中文数字/文号模式/目标分类/分句）
    extractor.py  抽取器（RelationExtractor + AliasResolver + 三产物 dataclass）
    pipeline.py   管道（RelationPipeline 编排入口 + 离线自检）

公共 API 经本门面 re-export（import 面契约测试 tests/test_import_surface.py 冻结；
模式常量全量转供——消费方 build_clause_graph/build_detail_tables/verify_regulatory_
citations/extract_relations 等按原路径 import 不变）。
"""

from __future__ import annotations

from .extractor import (
    AliasResolver,
    BasisRelation,
    ExtractionResult,
    RelationExtractor,
    RepealRelation,
)
from .patterns import (
    ARTICLE_CHAIN,
    ARTICLE_CHAIN_CAPTURE,
    ARTICLE_NUM,
    BARE_ARTICLE_RE,
    BASIS_TRIGGER_CORE,
    CN_NUM,
    CN_NUM_CHARS,
    DOCNO_CORE_PATTERN,
    EXTRACTOR_VERSION,
    GENERIC_ONLY,
    LAW_SUFFIX_ALT,
    ORGAN_WORDS,
    QUOTE_TITLE_MAX,
    QUOTE_TITLE_MIN,
    SCHEMA_VERSION,
    SELF_REF_RE,
    SELF_REF_WORDS,
    TARGET_CLASSES,
    TARGET_CORPUS,
    TARGET_ENTITY,
    TARGET_EXTERNAL,
    TARGET_GENERIC,
    TARGET_ORGAN,
    RelationConfig,
    basis_trigger_alt,
    classify_target,
    default_config,
    docno_core_re,
    docno_signature,
    extract_docnos,
    is_generic_target,
    is_organ_target,
    iter_docno_signatures,
    iter_quote_titles,
    normalize_doc_name,
    preprocess,
    quote_title_capture,
    quote_title_re,
    snippet_half_window,
    split_sentences,
)
from .pipeline import DocumentMeta, RelationPipeline

__all__ = [
    "SCHEMA_VERSION",
    "EXTRACTOR_VERSION",
    "CN_NUM",
    "CN_NUM_CHARS",
    "ARTICLE_NUM",
    "ARTICLE_CHAIN",
    "ARTICLE_CHAIN_CAPTURE",
    "LAW_SUFFIX_ALT",
    "DOCNO_CORE_PATTERN",
    "QUOTE_TITLE_MIN",
    "QUOTE_TITLE_MAX",
    "SELF_REF_WORDS",
    "SELF_REF_RE",
    "BARE_ARTICLE_RE",
    "ORGAN_WORDS",
    "TARGET_CLASSES",
    "TARGET_ENTITY",
    "TARGET_CORPUS",
    "TARGET_ORGAN",
    "TARGET_GENERIC",
    "TARGET_EXTERNAL",
    "GENERIC_ONLY",
    "BASIS_TRIGGER_CORE",
    "RelationConfig",
    "default_config",
    "snippet_half_window",
    "preprocess",
    "normalize_doc_name",
    "docno_signature",
    "iter_quote_titles",
    "iter_docno_signatures",
    "split_sentences",
    "extract_docnos",
    "quote_title_capture",
    "quote_title_re",
    "basis_trigger_alt",
    "docno_core_re",
    "is_generic_target",
    "is_organ_target",
    "classify_target",
    "BasisRelation",
    "RepealRelation",
    "ExtractionResult",
    "RelationExtractor",
    "AliasResolver",
    "DocumentMeta",
    "RelationPipeline",
]
