# -*- coding: utf-8 -*-
"""test_analysis_deliveries.py —— 分析交付库生成器单测（审查 P1-2，2026-09-12）。

覆盖：主题标识归一（_n，防 '1' vs 'T1' 前缀陷阱）、年份归一、渲染辅助（_table/_mermaid）、
dry-run 冒烟（不写盘返回 0）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_analysis_deliveries as g  # noqa: E402


class TestNorm:
    def test_strip_T_prefix(self):
        assert g._n("T1") == "1"
        assert g._n("1") == "1"
        assert g._n("t10") == "10"
        assert g._n("") == "" and g._n(None) == ""

    def test_equivalence_is_the_key_fix(self):
        # 2026-09-12 实测陷阱：clause_graph.dst_theme='1' vs theme='T1' 前缀不一致
        assert g._n("1") == g._n("T1")

    def test_norm_year(self):
        assert g._norm_year("2023") == 2023
        assert g._norm_year("2023-05-01") == 2023
        assert g._norm_year("") == 0
        assert g._norm_year("n/a") == 0
        assert g._norm_year("1200") == 0     # 越界保护


class TestRenderHelpers:
    def test_table_empty(self):
        assert g._table(["A"], []) == "（无数据）"

    def test_table_escapes_pipe(self):
        out = g._table(["A"], [["x|y"]])
        assert "x／y" in out and "| x／y |" in out

    def test_mermaid_empty(self):
        assert g._mermaid_edges([]) == "（无关系边）"

    def test_mermaid_renders_edges(self):
        out = g._mermaid_edges([{"src_rfn": "RFN-aaa111", "dst_rfn": "RFN-bbb222",
                                 "kind": "book_title", "count": 2}])
        assert out.startswith("```mermaid") and "aaa111" in out and "bbb222" in out


class TestDryRunSmoke:
    def test_dry_main_returns_zero(self, tmp_path):
        rc = g.main(["--dry", "--out", str(tmp_path)])
        assert rc == 0
        # dry 模式不应写盘（目录保持空或不存在）
        assert not os.path.exists(os.path.join(str(tmp_path), "_manifest.json"))
