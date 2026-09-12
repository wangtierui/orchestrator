# -*- coding: utf-8 -*-
"""test_scrapers_pure.py —— 采集/清洗纯函数单测（审查 P3 覆盖续升，2026-09-12）。

覆盖：unified_schema 归一函数、doc_type_cleaner（文种提取，返回 dict）、
category_classifier（位阶分类，返回 dict）。均为无 IO 纯函数，属清洗链核心判定。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scraper_std.category_classifier import analyze_agency, classify_category  # noqa: E402
from scraper_std.doc_type_cleaner import extract_doc_type, normalize_doc_type  # noqa: E402
from scraper_std.unified_schema import (  # noqa: E402
    build_dedup_key,
    canonical_body_source,
    canonical_source,
    canonical_timeliness_status,
    empty_str,
)


class TestUnifiedSchema:
    def test_empty_str(self):
        assert empty_str(None) == ""
        assert empty_str("x") == "x"

    def test_canonical_source_norm(self):
        # 源标识规范名直通（历史别名收敛见 SOURCE_ALIASES；未识别值→空串）
        assert canonical_source("gov") == "gov"
        assert canonical_source("nfra") == "nfra"
        assert canonical_source("未知源X") == ""

    def test_canonical_body_source_default(self):
        # 空值默认 'webpage'（正文来源契约默认档）
        assert canonical_body_source("") == "webpage"
        assert canonical_body_source(None) == "webpage"
        assert isinstance(canonical_body_source("附件"), str)

    def test_canonical_timeliness(self):
        # 7 值受控；中文存量映射可识别（有效→valid）
        assert canonical_timeliness_status("valid") == "valid"
        assert canonical_timeliness_status("有效") == "valid"

    def test_build_dedup_key_deterministic(self):
        # 同输入稳定（sha256 语义）；不做隐式 strip
        a = build_dedup_key("保监发〔2020〕1号", "某通知")
        b = build_dedup_key("保监发〔2020〕1号", "某通知")
        assert a == b and isinstance(a, str) and len(a) >= 32


class TestDocTypeCleaner:
    def test_extract_notice_returns_dict(self):
        r = extract_doc_type("中国银保监会办公厅关于印发意外伤害保险业务监管办法的通知")
        assert isinstance(r, dict)
        assert r.get("doc_type")          # 文种抽取成功（通知结尾→通知）
        assert r.get("status") == "success"

    def test_normalize_alias(self):
        # 别名收敛：令→命令（DOC_TYPE_ALIAS）；normalize 返回规范名 str
        v = normalize_doc_type("令", "中国银行保险监督管理委员会令（2020年第13号）")
        assert isinstance(v, str) and v

    def test_empty_title_safe(self):
        r = normalize_doc_type("", "")
        assert isinstance(r, str)         # 空输入不抛异常（返回 '' 或兜底名）


class TestCategoryClassifier:
    def test_law_level_rank(self):
        r = classify_category("法律", "中华人民共和国保险法")
        assert isinstance(r, dict)
        assert r.get("category") == "law"
        assert r.get("authority_rank") == 2

    def test_dept_rule(self):
        r = classify_category("部门规章", "保险公司管理规定", agency="银保监会")
        assert isinstance(r, dict)
        assert r.get("category") == "dept_rule"

    def test_agency_analysis(self):
        info = analyze_agency("中国银行保险监督管理委员会")
        assert isinstance(info, dict)
