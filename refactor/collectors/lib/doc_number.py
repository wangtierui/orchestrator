# -*- coding: utf-8 -*-
"""doc_number —— 再导出 shim（单一事实源已迁至 common.doc_number）。

本文件原为移植自 std_lib 的 near-identical 副本；现统一指向
refactor/common/doc_number.py（collectors 与 clean 共用），消除双份实现漂移，
使采集侧抽取/规范化与清洗侧完全一致。
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
