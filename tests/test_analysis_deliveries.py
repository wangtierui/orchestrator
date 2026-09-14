# -*- coding: utf-8 -*-
"""test_analysis_deliveries.py —— 分析交付库生成器单测（审查 P1-2，2026-09-12）。

覆盖：主题标识归一（_n，防 '1' vs 'T1' 前缀陷阱）、年份归一、渲染辅助（_table/_mermaid）、
dry-run 冒烟（不写盘返回 0）、**关系类纳管**（F-L01 追加，2026-09-14：2.1.2.4/2.1.2.5
单源渲染 + 事实源缺失即跳过）。
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_analysis_deliveries as g  # noqa: E402

_TS_RE = re.compile(r"生成于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _strip_ts(s: str) -> str:
    return _TS_RE.sub("生成于 <TS>", s)


def _norm(s: str) -> str:
    """行尾归一：Windows 落盘会把 \\n 写成 \\r\\n（渲染串为 LF）。"""
    return s.replace("\r\n", "\n")


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


# --------------------------------------------------------------------------- #
# F-L01 纳管（2026-09-14）：2.1.2.4 / 2.1.2.5 关系类交付
# 两条硬纪律：① 单源渲染（生成器与工具 CLI 共用同一函数，禁止分叉）
#           ② 关系事实源缺失 → 跳过并告警（不写占位、不登记），防覆盖既有好报告
# --------------------------------------------------------------------------- #
class TestRelationDeliveries:
    @staticmethod
    def _min_rows_stat():
        """最小可用 (rows, stat)：覆盖 render_report 消费的全部字段。"""
        rows = [{"src_kind": "internal", "src_ref": "IPN-1", "src_key": "k1",
                 "src_source": "internal", "relation": "basis", "dst_kind": "regulatory",
                 "dst_ref": "RFN-1", "dst_name": "甲办法", "dst_docno": "",
                 "matched_by": "docno_exact", "dst_class": "entity"}]
        stat = {
            "schema_version": "1.0", "extractor_version": "relations-1.0",
            "generated_by": "tools/extract_relations.py", "generated_at": "2026-01-01 00:00:00",
            "documents": {"regulatory": {"nfra": 1}, "internal": 1},
            "relations": {"total": 1, "by_kind": {"basis": 1, "repeal": 0},
                          "by_cross": {"regulatory->regulatory": 0, "internal->internal": 0,
                                       "internal->regulatory": 1, "regulatory->internal": 0}},
            "resolution": {"docno_exact": 1},
            "file_resolved_ratio": 1.0, "file_located_ratio": 1.0,
            "target_class": {"entity": 1, "corpus": 0, "organ": 0, "generic": 0, "external": 0},
            "file_level_denominator": 1, "filtered_generic_total": 0, "cross_basis_rows": 1,
            "files": {"relations_index": "a.jsonl", "cross_basis": "b.jsonl",
                      "relations_stat": "c.json"},
            "unresolved_sample": [], "warnings_total": 0,
        }
        return rows, stat

    def test_load_relations_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(g, "REL_INDEX", str(tmp_path / "missing.jsonl"))
        monkeypatch.setattr(g, "REL_STAT", str(tmp_path / "missing.json"))
        assert g._load_relations() == (None, None)

    def test_d212_4_skips_without_source(self, tmp_path):
        manifest: list = []
        g.d_212_4(None, None, False, manifest, str(tmp_path))
        assert manifest == []                       # 不登记
        assert os.listdir(str(tmp_path)) == []      # 不写占位（防覆盖既有好报告）

    def test_d212_5_skips_without_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(g, "REL_INDEX", str(tmp_path / "missing.jsonl"))
        manifest: list = []
        g.d_212_5(False, manifest, str(tmp_path))
        assert manifest == []
        assert os.listdir(str(tmp_path)) == []

    def test_renderers_are_single_source(self):
        """生成器必须取用两工具**同一**渲染函数（而非自带副本）。"""
        assert callable(g._relation_tool("extract_relations").render_report)
        assert callable(g._relation_tool("rfn_backlog").render_md)

    def test_write_report_is_thin_writer(self, tmp_path, monkeypatch):
        """`write_report` 只做落盘：内容必须等于 `render_report`（防两处渲染分叉）。"""
        import extract_relations as er
        rows, stat = self._min_rows_stat()
        monkeypatch.setattr(er, "REPORT_PATH", str(tmp_path / "report.md"))
        p = er.write_report(rows, stat)
        assert _norm(open(p, encoding="utf-8").read()) == er.render_report(rows, stat)

    def test_write_outputs_is_thin_writer(self, tmp_path, monkeypatch):
        """`write_outputs` 的 MD 必须等于 `render_md`（仅生成时间戳不同）。"""
        import rfn_backlog as rb
        bl = {"items": [], "relations_scanned": 0}
        monkeypatch.setattr(rb, "REL_DIR", str(tmp_path))
        monkeypatch.setattr(rb, "OUT_CSV", str(tmp_path / "c.csv"))
        monkeypatch.setattr(rb, "OUT_MD", str(tmp_path / "m.md"))
        rb.write_outputs(bl)
        got = _strip_ts(_norm(open(str(tmp_path / "m.md"), encoding="utf-8").read()))
        assert got == _strip_ts(rb.render_md(bl))


@pytest.mark.data
class TestRelationDeliveriesWithData:
    def test_delivery_lib_has_17_items(self):
        rows, _stat = g._load_relations()
        if rows is None:
            pytest.skip("关系事实源缺失（无数据环境）")
        mpath = os.path.join(ROOT, "docs", "reports", "_manifest.json")
        if not os.path.exists(mpath):
            pytest.skip("交付库未生成")
        m = json.load(open(mpath, encoding="utf-8"))
        assert m["count"] == len(m["items"]) == 17
        assert {"2.1.2.4", "2.1.2.5"} <= {it["item"] for it in m["items"]}

    def test_graph_report_matches_single_source_render(self):
        """交付库中的关系图谱 == 单源渲染器输出（逐字节；行尾归一）。"""
        rows, stat = g._load_relations()
        if rows is None:
            pytest.skip("关系事实源缺失（无数据环境）")
        er = g._relation_tool("extract_relations")
        p = os.path.join(ROOT, "docs", "reports", "监管与制度依据废止关系图谱.md")
        assert _norm(open(p, encoding="utf-8").read()) == er.render_report(rows, stat)
