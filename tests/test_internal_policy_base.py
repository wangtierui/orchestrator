# -*- coding: utf-8 -*-
"""test_internal_policy_base.py —— IPB 纯函数层单测（审查 P1-2，2026-09-12）。

覆盖：scan.parse_filename / ipn_of / clean_title_noise（正文权威）与
extract.normalize_text（文本归一）。均为无 IO 纯函数，回归网高价值区。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "modules", "internal_policy_base")):
    if p not in sys.path:
        sys.path.insert(0, p)

from extract import normalize_text  # noqa: E402
from scan import clean_title_noise, ipn_of, parse_filename  # noqa: E402


class TestParseFilename:
    def test_docno_with_title_after(self):
        r = parse_filename("阳光人寿发〔2025〕293号_关于印发销售管理办法的通知.pdf")
        assert r["docno"] == "阳光人寿发〔2025〕293号"
        assert "销售管理办法" in r["title"]

    def test_tail_paren_docno_takes_before(self):
        # 尾括号形态："标题（文号）"——文号后无实质内容 → 取文号前段
        r = parse_filename("关于印发XX管理规定的通知（阳光人寿发〔2025〕88号）.pdf")
        assert r["docno"] == "阳光人寿发〔2025〕88号"
        assert "通知" in r["title"] or "管理规定" in r["title"]

    def test_no_docno_strips_leading_marks(self):
        r = parse_filename("_23.关于修订XX制度的通知.docx")
        assert r["docno"] == ""
        assert not r["title"].startswith("_")

    def test_extension_removed(self):
        r = parse_filename("制度评分表.xlsx")
        assert r["title"] == "制度评分表"


class TestIpnOf:
    def test_deterministic(self):
        a = ipn_of("阳光人寿发〔2025〕293号", "销售管理办法", ".pdf")
        b = ipn_of("阳光人寿发〔2025〕293号", "销售管理办法", ".pdf")
        assert a == b and a.startswith("IPN-") and len(a) == 20

    def test_extension_disambiguates(self):
        # 同文号同标题双介质（pdf/xlsx）→ 不同 IPN
        assert ipn_of("文〔2025〕1号", "表单", ".pdf") != ipn_of("文〔2025〕1号", "表单", ".xlsx")

    def test_title_only_fallback(self):
        r = ipn_of("", "无文号制度", ".docx")
        assert r.startswith("IPN-")


class TestCleanTitleNoise:
    def test_strip_banben_suffix(self):
        assert clean_title_noise("销售管理办法-清洁版V3") == "销售管理办法"

    def test_strip_gai_zhang(self):
        assert clean_title_noise("客服管理规定 盖章") == "客服管理规定"

    def test_keep_plain_title(self):
        assert clean_title_noise("信息披露管理制度") == "信息披露管理制度"


class TestNormalizeText:
    def test_collapses_blank_lines(self):
        out = normalize_text("第一行\r\n\n\n\n第二行")
        assert "第一行" in out and "第二行" in out
        assert "\n\n\n" not in out

    def test_nfkc_and_strip(self):
        out = normalize_text("　全角空格开头　")
        assert not out.startswith(" ") and "全角空格开头" in out
