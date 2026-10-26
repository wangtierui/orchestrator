# -*- coding: utf-8 -*-
"""tests/test_rel_rules_alignment.py — 依据/废止抽取对齐《规则》(2026-10-26)。

规则文档：`reports/refactors/依据抽取规则与废止失效判定规则_20261026.md`
本批落地 R-DEP-01/03/04/05/06 与 R-REP-01 的**可判定部分**，守卫逐条钉住：
"
  A. R-DEP-06：会议/讲话/实际情况 等**非文件对象**不建依据边
  B. R-DEP-06（延伸）：**机关名不建边** —— 产物里 `dst_class=organ` 必须为 0
  C. R-DEP-01："制定本×" ⇒ legislative_basis；否则 application_basis
  D. R-DEP-03：同一引导词下并列书名号共享 group_id
  E. R-DEP-04/05：条号与文号（括号内 `〔YYYY〕N号`）
  F. R-REP-01：名词化排除（废止后/废止程序）+ `自……起废止` 取终止日
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def ex():
    from std_lib.common_lib.relations.extractor import RelationExtractor

    return RelationExtractor()


class TestNonFileTargets:
    """A. 非文件对象（R-DEP-06）。"""

    def test_blacklist_words_are_dropped(self, ex) -> None:
        # 句式必须落在**动作词表**内（制定/起草/发布…），否则连 pattern 都不命中，
        # 测到的是"句子不合法"而不是"黑名单生效"。
        r = ex.extract("根据《会议精神》和《统计资料》，制定本办法。")
        assert not r.basis, [b.target_name for b in r.basis]
        assert r.filtered_non_file >= 1

    def test_but_real_reply_document_is_kept(self, ex) -> None:
        """`《…批复》` 是**文件**，不能被"批复"这个黑名单词误伤（规则原文的边界）。"""
        r = ex.extract("根据《国务院关于同意设立某公司的批复》，制定本办法。")
        names = [b.target_name for b in r.basis]
        assert any("批复" in n for n in names), names

    def test_organ_kept_at_extraction_but_dropped_in_products(self, ex) -> None:
        """**分层责任**：抽取层保留机关名（程序性依据是合法形态），
        产物层（`build_rows`）按 R-DEP-06 剔除。否则会把"抽取"与"发布"两件事混谈。
        """
        r = ex.extract("根据《国务院》有关规定，制定本办法。")
        assert any(b.target_name == "国务院" for b in r.basis), "抽取层应保留原始命中"
        # 产物层剔除由 TestProductsHaveNoOrganEdges 守卫（那里断言 organ 行数为 0）


class TestSubtype:
    """C. R-DEP-01 立法/适用依据判别。"""

    def test_legislative_when_article_says_zhiding_ben(self, ex) -> None:
        r = ex.extract("第一条 为规范管理，根据《中华人民共和国保险法》，制定本办法。")
        assert r.basis and r.basis[0].relation_subtype == "legislative_basis"

    def test_application_when_no_zhiding(self, ex) -> None:
        # 用"印发"（在动作词表内）而不用"执行"（不在），确保命中的是句式而非巧合
        r = ex.extract("第三条 依据《中华人民共和国公司法》印发本通知。")
        assert r.basis and r.basis[0].relation_subtype == "application_basis"

    def test_procedural_is_application(self, ex) -> None:
        r = ex.extract("经国务院银行业监督管理机构批准，开展业务。")
        proc = [b for b in r.basis if b.basis_type == "procedural"]
        assert proc and proc[0].relation_subtype == "application_basis"


class TestGroupAndClause:
    """D/E. 并列分组、条号、文号。"""

    def test_parallel_citations_share_group(self, ex) -> None:
        r = ex.extract("根据《A管理办法》《B管理办法》，制定本办法。")
        gids = {b.group_id for b in r.basis if b.target_name.startswith(("A", "B"))}
        assert len(gids) == 1, gids

    def test_docno_in_parentheses(self, ex) -> None:
        r = ex.extract("根据《保险公司管理规定》（银保监发〔2021〕12号）制定本办法。")
        got = [b.target_docno for b in r.basis if b.target_name == "保险公司管理规定"]
        assert got and "〔2021〕12号" in got[0], got

    def test_target_clause_after_title(self, ex) -> None:
        r = ex.extract("依据《中华人民共和国公司法》第12条办理。")
        b = [x for x in r.basis if x.target_name == "中华人民共和国公司法"][0]
        assert b.target_clause == "第12条" and b.article == "第12条"


class TestRepealNominal:
    """F. R-REP-01 名词化排除与终止日。"""

    @pytest.mark.parametrize("text", [
        "《XX办法》废止后另行制定。",
        "《XX办法》废止程序另行规定。",
        "《XX办法》废止时应当公告。",
    ])
    def test_nominal_forms_not_repeal(self, ex, text: str) -> None:
        assert not [r for r in ex.extract(text).repeal if r.target_name == "XX办法"]

    def test_since_clause_sets_end_date(self, ex) -> None:
        r = ex.extract("《YY办法》自2024年1月1日起废止。")
        assert r.repeal and r.repeal[0].target_end_date.startswith("2024年1月1日")

    def test_explicit_repeal_still_works(self, ex) -> None:
        r = ex.extract("现决定废止《ZZ办法》。")
        assert any("ZZ办法" in x.target_name for x in r.repeal)


class TestProductsHaveNoOrganEdges:
    """B. 产物层：机关名不得成为关系目标（用户明确要求）。"""

    @pytest.fixture(scope="class")
    def rows(self):
        from interfaces import relations_api as R

        return R.load("all")

    def test_no_organ_rows(self, rows) -> None:
        bad = [r for r in rows if str(r.get("dst_class")) == "organ"]
        assert not bad, f"仍有 {len(bad)} 条机关名边，例：{bad[:2]}"

    def test_no_generic_rows(self, rows) -> None:
        assert not [r for r in rows if str(r.get("dst_class")) == "generic"]

    def test_stat_discloses_excluded(self) -> None:
        """被排除的条数必须**留痕**（否则读者以为"从来没有这些关系"）。"""
        import json
        import os

        import paths

        p = os.path.join(os.path.dirname(paths.relations_index()), "relations_stat.json")
        st = json.load(open(p, encoding="utf-8"))
        assert int(st.get("excluded_organ") or 0) > 0
        assert "by_subtype" in st and "groups" in st

    def test_subtype_matches_extractor_vocabulary(self, rows) -> None:
        vals = {str(r.get("relation_subtype")) for r in rows if r.get("relation") == "basis"}
        assert vals <= {"legislative_basis", "application_basis"}, vals
