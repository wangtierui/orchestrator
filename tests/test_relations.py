# -*- coding: utf-8 -*-
"""tests/test_relations.py — 依据/废止关系统一抽取（R-F01）回归

分两层：
  · **代码级**（无 `data` 标记）：抽取器语义、归一与签名、共享原语、契约/枚举一致性；
  · **数据级**（`@pytest.mark.data`）：产物存在性、门禁通过、读取 API 与统计一致
    （无数据环境用 `pytest tests -m "not data"` 排除）。

覆盖的用户要求：三类关系（监管依据/废止、内部依据/废止、内部→监管依据）可提取、
可解析到实体、可被下游消费，且**未解析者保留原文不臆造**。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "std_lib"), os.path.join(_ROOT, "modules"),
           os.path.join(_ROOT, "modules", "regulatory_classifier"),
           os.path.join(_ROOT, "modules", "regulatory_classifier", "scripts"),
           os.path.join(_ROOT, "modules", "internal_policy_drafter", "scripts"),
           os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ===========================================================================
# 一、抽取器语义（代码级）
# ===========================================================================
class TestExtractorSemantics:
    def _ex(self, text: str):
        from std_lib.common_lib.relations import RelationPipeline
        return RelationPipeline().extractor.extract(text)

    def test_basis_with_adjacent_quote_list_and_hierarchy(self):
        """相邻书名号（`《A》《B》`）与位阶词连写（`等法律法规`）须能抽取。"""
        res = self._ex("为规范管理，根据《中华人民共和国保险法》《中华人民共和国银行业监督管理法》"
                       "等法律法规，制定本办法。")
        names = [b.target_name for b in res.basis]
        assert names == ["中华人民共和国保险法", "中华人民共和国银行业监督管理法"], names
        assert all(b.basis_type == "substantive" for b in res.basis)

    def test_basis_article_level_captures_article(self):
        res = self._ex("根据《中华人民共和国立法法》第三条，制定本细则。")
        assert res.basis and res.basis[0].article == "第三条"

    def test_repeal_single_with_docno_and_action(self):
        res = self._ex("《商业银行市场风险管理指引》（银监发〔2004〕10号）同时废止。")
        assert len(res.repeal) == 1
        r = res.repeal[0]
        assert r.target_name == "商业银行市场风险管理指引"
        assert r.target_docno == "银监发〔2004〕10号"
        assert r.action == "repeal" and r.scope == "whole"

    def test_repeal_action_longest_first_not_truncated(self):
        """长动作词优先：`同时废止` 不得被切成 `废止` 而丢失上下文。"""
        res = self._ex("《旧办法》同时废止。")
        assert res.repeal and res.repeal[0].action == "repeal"

    def test_repeal_list_header_and_numbered_items(self):
        res = self._ex("经研究，现决定废止以下三部规章："
                       "一、《A管理办法》（甲发〔2010〕1号）。二、《B管理办法》。三、《C规定》。")
        names = sorted(r.target_name for r in res.repeal)
        assert names == ["A管理办法", "B管理办法", "C规定"], names

    def test_partial_repeal_scope_and_article(self):
        res = self._ex("《某办法》第十五条废止，其余条款继续有效。")
        assert res.repeal and res.repeal[0].scope == "partial"
        assert res.repeal[0].article == "第十五条"

    def test_negated_context_not_treated_as_repeal(self):
        """否定/未生效语境（拟废止、征求意见、草案）不得计入废止关系。"""
        assert not self._ex("现拟废止《旧办法》，并征求各分公司意见。").repeal
        assert not self._ex("《新办法》（草案）宣布失效。").repeal

    def test_excluded_context_skips_interpretation_clause(self):
        """`负责解释` 等上下文中的书名号不视为依据关系。"""
        assert not self._ex("本办法由国务院负责解释。").basis

    def test_procedural_basis_requires_organ_suffix(self):
        """程序性依据须为**机关名**（法定后缀结尾），不得跨词捕获（实测 `批准或者未按照`）。"""
        res = self._ex("本办法经国务院同意后施行。")
        assert [b.target_name for b in res.basis] == ["国务院"]
        assert res.basis[0].basis_type == "procedural"
        assert not self._ex("本办法经批准或者未按照规定的其他情形处理。").basis

    def test_attachment_hint_emits_warning(self):
        res = self._ex("废止清单详见附件。")
        assert any("附件" in w for w in res.warnings)

    def test_result_carries_offset_and_snippet(self):
        text = "前言。根据《中华人民共和国保险法》，制定本办法。"
        res = self._ex(text)
        assert res.basis[0].offset > 0
        assert "保险法" in res.basis[0].source_snippet
        assert res.basis[0].offset == text.index("根据")


# ===========================================================================
# 二、归一与共享原语（代码级；merged/relations 收敛点）
# ===========================================================================
class TestSharedPrimitives:
    def test_normalize_doc_name_strips_quotes_and_bracket_tail(self):
        from std_lib.common_lib.relations import normalize_doc_name
        assert normalize_doc_name("《中华人民共和国保险法》") == "中华人民共和国保险法"
        assert normalize_doc_name("《某办法》（试行）") == "某办法"
        assert normalize_doc_name("") == ""

    def test_docno_signature_matches_merged_semantics(self):
        from std_lib.common_lib.relations import docno_signature
        assert docno_signature("银保监发〔2019〕19号") == "201919"
        assert docno_signature("中华人民共和国国务院令第262号") == ""   # 无四位年 → 不走签名

    def test_iter_docno_signatures_handles_bare_bracket_form(self):
        """merged 的 docno_sig 语义：**裸括号式**（无机关代字）也要能抽；<6 位签名按原语义丢弃。"""
        from std_lib.common_lib.relations import iter_docno_signatures
        sigs = iter_docno_signatures("依据〔2019〕19号及（2020）77号文件办理，另见（2020）7号。")
        assert "201919" in sigs and "202077" in sigs
        assert "20207" not in sigs, "签名长度 <6 应丢弃（与 merged 收敛前语义一致）"

    def test_iter_quote_titles_respects_length_window(self):
        """收敛前 merged 用 `《…》` 长度 4-40；参数须保持该语义。"""
        from std_lib.common_lib.relations import iter_quote_titles
        assert iter_quote_titles("《办法》与《中华人民共和国保险法实施条例》", min_len=4, max_len=40) == \
            ["中华人民共和国保险法实施条例"]
        assert iter_quote_titles("《A》与《B》", min_len=1, max_len=40) == ["A", "B"]

    def test_alias_resolver_exact_then_contains(self):
        from std_lib.common_lib.relations import AliasResolver
        r = AliasResolver({"银行业监督管理法": "中华人民共和国银行业监督管理法"})
        assert r.resolve("《银行业监督管理法》") == "中华人民共和国银行业监督管理法"
        assert r.resolve("完全无关的名称") is None


# ===========================================================================
# 三、契约与受控枚举一致性（代码级）
# ===========================================================================
class TestContractAndEnums:
    def test_relation_fields_registered_and_stable(self):
        from interfaces.contract import CROSS_BASIS_FIELDS, RELATION_FIELDS
        assert "relation_id" in RELATION_FIELDS and "matched_by" in RELATION_FIELDS
        assert "source_snippet" in RELATION_FIELDS and "generated_at" in RELATION_FIELDS
        assert CROSS_BASIS_FIELDS == RELATION_FIELDS     # 派生视图同构（便于统一消费）

    def test_enums_closure_and_ref_match_subset(self):
        from config.enums import (
            BASIS_TYPE,
            REF_MATCH_METHOD,
            RELATION_DOC_KIND,
            RELATION_KIND,
            RELATION_MATCH_METHOD,
            REPEAL_ACTION,
            REPEAL_SCOPE,
        )
        assert RELATION_KIND == {"basis", "repeal"}
        assert RELATION_DOC_KIND == {"regulatory", "internal"}
        assert BASIS_TYPE == {"substantive", "procedural"}
        assert REPEAL_SCOPE == {"whole", "partial", "attachment"}
        assert len(REPEAL_ACTION) == 5
        assert REF_MATCH_METHOD.issubset(RELATION_MATCH_METHOD)

    def test_enum_selfcheck_passes(self):
        from config.enums import assert_enum_bindings
        assert_enum_bindings()


# ===========================================================================
# 四、数据级：产物 / 门禁 / API（需本机数据）
# ===========================================================================
class TestGateFreshness:
    """判据 8（2026-09-14）：关系产物**不得早于**其数据面输入。

    动因：消费面（merged 引用原语 / drafter 关系素材 / 交付库 2.1.2.4·2.1.2.5 报告）会随
    关系产物**静默反映旧数据**；此前门禁只查结构一致性，陈旧产物可全额通过。
    """

    @staticmethod
    def _gate():
        sys.path.insert(0, _ROOT)
        from gates import gate_relations
        return gate_relations

    def test_stale_detected_when_input_newer(self, monkeypatch):
        g = self._gate()
        monkeypatch.setattr(g, "_data_inputs", lambda: [(1000.0, "新输入.jsonl")])
        assert g._stale_inputs(900.0) == ["新输入.jsonl"]

    def test_fresh_when_product_newer(self, monkeypatch):
        g = self._gate()
        monkeypatch.setattr(g, "_data_inputs", lambda: [(1000.0, "输入.jsonl")])
        assert g._stale_inputs(1100.0) == []

    def test_tolerance_absorbs_write_skew(self, monkeypatch):
        g = self._gate()
        monkeypatch.setattr(g, "_data_inputs", lambda: [(1000.0, "输入.jsonl")])
        assert g._stale_inputs(999.0) == []          # 1s 差 < 容忍 2s
        assert g._stale_inputs(997.0) == ["输入.jsonl"]   # 3s 差 > 容忍

    def test_no_inputs_never_stale(self, monkeypatch):
        g = self._gate()
        monkeypatch.setattr(g, "_data_inputs", lambda: [])
        assert g._stale_inputs(0.0) == []


@pytest.mark.data
class TestRelationsProducts:
    def test_products_exist_and_index_has_rows(self):
        from interfaces.relations_api import INDEX_PATH, load
        assert os.path.exists(INDEX_PATH), "关系事实源缺失：先运行 `python cli.py relations gen`"
        rows = load("all")
        assert rows, "关系事实源为空"
        # 三类关系须全部非空（用户要求明确列明三类）
        assert load("regulatory"), "类别 1（监管依据/废止）为空"
        assert load("cross"), "类别 3（内部→监管依据）为空"

    def test_three_kinds_partition_index(self):
        from interfaces.relations_api import load
        all_rows = load("all")
        parts = load("regulatory") + load("internal") + load("cross")
        # 三类口径互斥（internal 与 cross 互斥；regulatory 为源侧全集）
        assert len(load("internal")) + len(load("cross")) == \
            len([r for r in all_rows if r["src_kind"] == "internal"])
        assert all(r["src_kind"] == "regulatory" for r in load("regulatory"))
        assert parts, "三类关系不应同时为空"

    def test_cross_basis_view_is_pure_basis_subset(self):
        from interfaces.relations_api import load, load_cross_basis
        cross_rows = load_cross_basis()
        assert cross_rows, "cross_basis 派生视图为空"
        assert all(r["relation"] == "basis" and r["src_kind"] == "internal"
                   and r["dst_kind"] == "regulatory" for r in cross_rows)
        ids = {r["relation_id"] for r in load("all")}
        assert all(r["relation_id"] in ids for r in cross_rows), "视图须为事实源子集"

    def test_api_by_src_and_by_dst_are_consistent(self):
        from interfaces.relations_api import by_dst, by_src, load
        rows = load("all")
        sample = next((r for r in rows if r["dst_ref"]), None)
        assert sample is not None
        assert any(x["relation_id"] == sample["relation_id"] for x in by_src(sample["src_ref"]))
        assert any(x["relation_id"] == sample["relation_id"] for x in by_dst(sample["dst_ref"]))

    def test_stat_matches_index(self):
        from interfaces.relations_api import load, stat
        s = stat()
        assert s, "relations_stat.json 缺失"
        rows = load("all")
        assert s["relations"]["total"] == len(rows)
        by_kind: dict[str, int] = {}
        for r in rows:
            by_kind[r["relation"]] = by_kind.get(r["relation"], 0) + 1
        assert s["relations"]["by_kind"] == by_kind

    def test_relation_id_is_unique(self):
        """关系 id 唯一（2026-09-20 修复）：id 派生须纳入 article/action/scope 等判别字段。"""
        from interfaces.relations_api import load, stat
        rows = load("all")
        ids = [r["relation_id"] for r in rows]
        assert len(ids) == len(set(ids)), \
            f"relation_id 不唯一：{len(ids)} 行 / {len(set(ids))} 个 id"
        info = (stat() or {}).get("relation_id") or {}
        if info:
            assert info.get("rows") == len(rows)
            assert info.get("distinct") == len(set(ids))

    def test_unresolved_rows_keep_original_text_never_fabricate(self):
        """未解析者必须保留原文名称（**禁止臆造 RFN**）——drafter 引用核验同纪律。"""
        from interfaces.relations_api import load
        unresolved = [r for r in load("all") if r["matched_by"] == "unresolved"]
        assert unresolved, "预期存在未解析目标（多为未入库法律法规）"
        for r in unresolved[:200]:
            assert not r["dst_ref"], "unresolved 不得带强实体 ref"
            assert r["dst_name"].strip(), "unresolved 须保留原文名称"
            assert r["confidence"] == 0.0

    def test_gate_relations_passes(self):
        sys.path.insert(0, _ROOT)
        from gates import gate_relations
        ok, detail = gate_relations.run()
        assert ok, detail.get("problems") or detail


# ===========================================================================
# 五、R-F01 收敛：三处旧实现**不得再各自定义口径**（防再分叉）
# ===========================================================================
class TestConvergence:
    """回归：`clause_graph` / `detail_tables` / `verify_citations` 的抽取口径必须与
    `std_lib.common_lib.relations` **同源**（2026-09-14 收敛；此前三处各写一份字面量，
    口径靠人眼对齐，任一处改动会静默造成全链不一致）。
    """

    def test_detail_tables_uses_shared_patterns(self):
        import build_detail_tables as dt

        from std_lib.common_lib.relations import (
            ARTICLE_CHAIN_CAPTURE,
            LAW_SUFFIX_ALT,
            QUOTE_TITLE_MAX,
            QUOTE_TITLE_MIN,
            quote_title_capture,
        )
        assert dt.ART_RE.pattern == (quote_title_capture() + r"[^。；\n]{0,12}?"
                                     + ARTICLE_CHAIN_CAPTURE)
        assert dt.LAW_RE.pattern == (
            rf"《([^《》]{{{QUOTE_TITLE_MIN},{QUOTE_TITLE_MAX}}}(?:{LAW_SUFFIX_ALT}))》")

    def test_clause_graph_uses_shared_patterns(self):
        import build_clause_graph as cg

        from std_lib.common_lib.relations import (
            ARTICLE_CHAIN_CAPTURE,
            ARTICLE_NUM,
            BARE_ARTICLE_RE,
            SELF_REF_RE,
            basis_trigger_alt,
            quote_title_capture,
        )
        assert cg.NUM == ARTICLE_NUM
        assert cg.ART_IN.pattern == ARTICLE_CHAIN_CAPTURE
        assert cg.P2.pattern == SELF_REF_RE.pattern
        assert cg.P3.pattern == BARE_ARTICLE_RE.pattern
        assert cg.P4.pattern == rf"(?:{basis_trigger_alt()})\s*{quote_title_capture()}"
        assert cg.P5.pattern == quote_title_capture()

    def test_verify_citations_uses_shared_patterns(self):
        import verify_regulatory_citations as vrf

        from std_lib.common_lib.relations import (
            DOCNO_CORE_PATTERN,
            ORGAN_WORDS,
            quote_title_capture,
        )
        assert vrf.DOCNO_CORE_PAT.pattern == DOCNO_CORE_PATTERN
        assert vrf.TITLE_PAT.pattern == quote_title_capture()
        assert tuple(vrf.ORGAN_PREFIXES) == tuple(ORGAN_WORDS)


# ===========================================================================
# 六、目标分层与 RFN 补登（2026-09-14 口径修正 + 提升路径①）
# ===========================================================================
class TestTargetClass:
    def test_classify_target_layers(self):
        from std_lib.common_lib.relations import classify_target

        assert classify_target(dst_ref="RFN-x") == "entity"
        assert classify_target(dst_key="abc123") == "corpus"
        # 机关名（程序性依据目标）→ organ，不算"文件未定位"
        assert classify_target(name="国务院") == "organ"
        assert classify_target(name="国务院银行业监督管理机构") == "organ"
        assert classify_target(name="本级人民政府") == "organ"
        assert classify_target(name="其总公司") == "organ"
        # 纯类型泛指词 → generic
        assert classify_target(name="条例") == "generic"
        assert classify_target(name="办法") == "generic"
        # 语料外文件（法律/法规/其他机关文件）→ external
        assert classify_target(name="中华人民共和国银行业监督管理法") == "external"
        assert classify_target(name="中华人民共和国民法典") == "external"
        assert classify_target(name="中央对地方专项转移支付管理办法") == "external"

    def test_generic_targets_filtered_at_extraction(self):
        """`《条例》`/`《办法》` 属泛指（抽取噪声）→ 不产出关系，并计数（2026-09-14 净化）。"""
        from std_lib.common_lib.relations import RelationPipeline

        ex = RelationPipeline().extractor
        res = ex.extract("根据《条例》和《办法》制定本办法。《条例》同时废止。")
        assert not res.basis, res.basis
        assert not res.repeal, res.repeal
        assert res.filtered_generic >= 3, res.filtered_generic

    def test_organ_not_counted_as_unsolved_file(self):
        """程序性依据目标为机关 → `organ`，不计入文件级分母（口径修正的可验证断言）。"""
        from std_lib.common_lib.relations import RelationPipeline, classify_target

        res = RelationPipeline().extractor.extract("本办法经国务院同意后施行。")
        assert len(res.basis) == 1
        b = res.basis[0]
        assert classify_target(dst_ref="", dst_key="", name=b.target_name,
                              basis_type=b.basis_type) == "organ"


@pytest.mark.data
class TestRfnBacklog:
    """RFN 补登（提升路径①）：清单产物 + 主题建议规则。"""

    def test_backlog_products_exist_with_decision_column(self):
        import csv
        import os

        p = os.path.join(_ROOT, "modules", "regulatory_classifier", "data",
                         "relations", "rfn_backlog.csv")
        assert os.path.exists(p), "补登清单缺失：先运行 `python tools/rfn_backlog.py`"
        with open(p, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows, "补登清单为空"
        assert {"dedup_key", "title", "suggested_theme", "decision", "cited_count"} <= set(rows[0])
        # decision 受控（主题建议的**依据**必须可解释，禁止无据建议）
        assert {r["decision"] for r in rows} <= {"votes", "law_to_t0", "uncertain"}

    def test_law_shape_maps_to_t0_anchor(self):
        """法律/行政法规 → T0 上位法锚点（体系语义；此类**不**用引用者投票）。"""
        import rfn_backlog as rb

        assert rb._law_shape("中华人民共和国银行业监督管理法")
        assert rb._law_shape("中华人民共和国商业银行法")
        assert rb._law_shape("中华人民共和国预算法实施条例")
        assert not rb._law_shape("中央对地方专项转移支付管理办法")

    def test_theme_map_from_rfn_index_is_single_source(self):
        """主题映射须经 rfn 索引（`rows()` 已合并主题列），不自行拼 CSV 路径。"""
        import rfn_backlog as rb

        tm = rb.load_theme_map()
        assert tm, "主题映射为空（rfn 索引或主题表缺失）"
        assert any(k.startswith("RFN-") for k in tm)
        assert set(v for v in tm.values() if v) <= set(
            v for v in __import__("rfn").THEME_MAP.values())
