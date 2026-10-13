# -*- coding: utf-8 -*-
"""doc_number —— 再导出 shim（单一事实源已迁至 common.doc_number）。

clean 内部历史 `from .core.doc_number import ...` 继续可用；真实实现见
refactor/common/doc_number.py，collectors 与 clean 共用，消除 collectors/lib 与
clean/core 两份 near-identical 实现漂移，以及 envelope 落库未规范化 document_number 的 P0。
"""
from common.doc_number import (
    cn_to_arabic,
    clean_doc_number,
    extract_doc_number,
    extract_from_body,
    extract_from_title,
    extract_orgs,
    in_abolish_context,
    looks_like_doc_number,
    normalize_doc_number,
    rebuild_multi_org_docno,
)
extract = extract_doc_number
normalize = normalize_doc_number
