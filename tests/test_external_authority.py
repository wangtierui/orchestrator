# -*- coding: utf-8 -*-
"""tests/test_external_authority.py — 外部法规权威表（独立键空间 `RFN-EXT-*`）。

为什么不是"再补一批 RFN"
------------------------
上一批（112）把定位率从 20.9% 提到 50.3%，剩下 2632 条的目标**根本不在语料里**
（民法典/水法/国务院令/本公司制度的历史版本）。给它们登记普通 RFN 等于宣称
"这份文件已收录"——而库里连原文都没有，那是**伪造事实**。

所以建独立键空间：编号 `RFN-EXT-<12hex>`，由 (文号, 标题) 归一后派生，**可重算**。
本守卫钉住五条不变量：

  A. 编号体系：格式合法、与语料内 `RFN-<16hex>` **不相交**、同键同号（可重算）
  B. 纳入判据：只收 `dst_class == external`；**机关名（`organ`）绝不登记**
     （第一版用"有标题就算"，把 1026 条"国务院/财政部"当成法规登记了）
  C. 诚实标注：`in_corpus=False` / `kind=external` / 不与实体同色（前端契约）
  D. 集成：图谱四视图之外单列一屏、ego 能画出外部节点
  E. 幂等：`--check` 通过（重跑不产生新编号、统计与明细一致）
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html() -> str:
    return open(os.path.join(ROOT, "web", "assets", "index.html"),
                   encoding="utf-8").read()


class TestIdScheme:
    """A. 编号体系（编码层面的"独立键空间"）。"""

    def test_table_exists(self) -> None:
        from interfaces import external_api as E

        assert E.exists(), "外部权威表未生成（运行 tools/build_external_authority.py）"
        rows = E.load()
        assert len(rows) > 100, f"表过小（{len(rows)}），疑似生成失败"

    def test_id_format(self) -> None:
        from interfaces import external_api as E

        for r in E.load()[:50]:
            assert E.is_ext_id(r["ext_id"]), r["ext_id"]

    def test_no_collision_with_corpus_rfn(self) -> None:
        """`RFN-EXT-*` 与语料内 `RFN-<16hex>` **正则不相交**。

        这是本设计的核心安全点：标签会被复制粘贴丢掉，编号不会。读者看到一个
        `RFN-EXT-xxxx` 就应当知道"这份文件不在库里"，不需要记住任何约定。
        """
        from interfaces import external_api as E

        corpus = re.compile(r"^RFN-[0-9a-f]{16}$")
        for r in E.load()[:50]:
            assert not corpus.fullmatch(r["ext_id"]), r["ext_id"]
            assert r["ext_id"].startswith(E.PREFIX)

    def test_id_is_deterministic(self) -> None:
        """同 (文号, 标题) 永远同号 —— 重跑不会让历史引用漂移。"""
        from interfaces import external_api as E

        a = E.resolve("中华人民共和国网络安全法", "")
        b = E.resolve("中华人民共和国网络安全法 ", "").strip()
        assert a == b and E.is_ext_id(a)

    def test_docno_wins_over_title(self) -> None:
        """有文号时按文号定键：同一文号配不同标题写法仍然是同一条。"""
        from interfaces import external_api as E

        k1 = E.key_of("X办法（2021版）", "阳光人寿发〔2021〕255号")
        k2 = E.key_of("X办法", "阳光人寿发〔２０２１〕２５５ 号")   # 全角
        assert k1 == k2, "全角文号未归一 ⇒ 会产生重复外部条目"

    def test_empty_target_gets_no_id(self) -> None:
        from interfaces import external_api as E

        assert E.resolve("", "") == "", "无证据目标不得拿到编号（否则等于伪造记录）"


class TestInclusionCriteria:
    """B. 纳入判据：机关名不是文件。"""

    def test_organs_excluded(self) -> None:
        from interfaces import external_api as E
        from std_lib.common_lib.relations.patterns import is_organ_target

        for r in E.load():
            title = str(r.get("title") or "")
            assert not is_organ_target(title), (
                f"机关名被登记成外部法规：{title!r}（{r['ext_id']}）")

    def test_organ_samples_never_registered(self) -> None:
        """把几个典型机关名直接喂给 extract 侧判据，确保它们不会出现在表里。"""
        from interfaces import external_api as E

        titles = {str(r.get("title") or "") for r in E.load()}
        for organ in ("国务院", "财政部", "同级人民政府", "海事管理机构",
                      "自然资源标准化工作管理委员会"):
            assert organ not in titles, f"{organ} 被登记为外部法规"

    def test_stat_matches_detail(self) -> None:
        from interfaces import external_api as E

        rows = E.load()
        st = E.load_stat()
        assert st["available"] is True
        assert st["total"] == len(rows)
        assert st["evidence_total"] == sum(int(r["evidence_count"]) for r in rows)

    def test_stat_discloses_exclusions(self) -> None:
        """被排除的机关名/语料弱线索必须披露数量（否则读者以为"只剩这么点"）。"""
        from interfaces import external_api as E

        st = E.load_stat()
        assert int(st.get("organ_excluded") or 0) > 0
        assert int(st.get("corpus_weak_excluded") or 0) > 0


class TestHonesty:
    """C. 不得冒充语料内实体。"""

    def test_never_in_corpus(self) -> None:
        from interfaces import external_api as E

        for r in E.load()[:80]:
            assert r["in_corpus"] is False
            assert r["kind"] == "external"
            assert r["guise"] in E.GUISES

    def test_guise_labels_complete(self) -> None:
        from interfaces import external_api as E

        assert set(E.GUISE_LABELS) == set(E.GUISES)


class TestIntegration:
    """D. 图谱集成。"""

    def test_global_graph_has_external_view(self) -> None:
        from interfaces import relations_api as R

        g = R.global_graph()
        keys = [v["key"] for v in g["views"]]
        assert keys == ["internal_to_regulatory", "regulatory_to_regulatory",
                        "internal_to_internal", "theme", "external"], keys
        ext = g["external"]
        assert ext["nodes"] and ext["total"] > 0
        assert ext["evidence"] > 0
        # 视图节点必须自带"语料外"标记（前端据此画虚线）
        for n in ext["nodes"][:20]:
            assert n["external"] is True and n["in_corpus"] is False
            assert n["id"].startswith("RFN-EXT-")

    def test_bipartite_draws_external_nodes(self) -> None:
        """制度→监管 视图里必须能看到语料外目标（虚线），而不是只报个数字。"""
        from interfaces import relations_api as R

        g = R.global_graph()
        right = g["bipartite"]["right"]
        ext_nodes = [n for n in right if n.get("external")]
        assert ext_nodes, "制度→监管 视图没有外部节点（外部关系被丢弃了）"
        ext_edges = [e for e in g["bipartite"]["edges"] if e.get("external")]
        assert ext_edges, "制度→监管 视图没有外部边"
        ids = {n["id"] for n in right}
        assert all(e["to"] in ids for e in ext_edges)

    def test_internal_view_fully_resolved_today(self) -> None:
        """制度↔制度 目前 **24/24 全部可定位**（`dst_class` 全是 entity）。

        这条守卫记录**当前事实**，不是"要求永远没有外部"：将来若出现"新版废止旧版
        而旧版未收录"，`_gInternal` 会把它画成虚线节点（代码已支持），那时应把这条
        断言改成"外部节点必须 `external=True` 且带 `guise`"。
        """
        from interfaces import relations_api as R
        from std_lib.common_lib.relations.patterns import TARGET_ENTITY

        rows = R.load("internal")
        classes = {str(r.get("dst_class") or "") for r in rows}
        if classes == {TARGET_ENTITY}:
            g = R.global_graph()
            assert not any(n.get("external") for n in g["internal"]["nodes"])
        else:  # pragma: no cover  数据演化后的分支
            g = R.global_graph()
            for n in g["internal"]["nodes"]:
                if n.get("external"):
                    assert n["id"].startswith("RFN-EXT-") and n.get("guise")

    def test_ego_includes_external_edge(self) -> None:
        """以某份制度为中心时，指向语料外法规的边必须画出来并标 `external`。"""
        from interfaces import relations_api as R

        rows = R.load("cross")
        ref = next(r["src_ref"] for r in rows
                   if not r.get("dst_ref") and not r.get("dst_key")
                   and str(r.get("dst_class")) == "external")
        e = R.ego(ref)
        ext_edges = [x for x in e["edges"] if x.get("external")]
        assert ext_edges, "ego 未画出外部法规边"
        assert e["counts"]["external"] == len(ext_edges)
        node = next(n for n in e["nodes"] if n["id"] == ext_edges[0]["to"])
        assert node["kind"] == "external" and node.get("external") is True

    def test_attributed_ratio_is_separate_metric(self) -> None:
        """归属率（含外部）必须与语料内定位率**分开报**，不许合成一个更好看的数。"""
        from interfaces import relations_api as R

        c = R.global_graph()["counts"]
        assert "file_resolved_ratio" in c and "attributed_ratio" in c
        assert c["attributed_ratio"] >= c["file_resolved_ratio"], (
            "归属率（含外部）不应低于语料内定位率——它包含后者")


class TestIdempotent:
    """E. 幂等（重跑不漂移）。"""

    def test_check_passes(self) -> None:
        from tools.build_external_authority import _check, _collect

        rows, stat = _collect()
        bad = _check(rows, stat)
        assert not bad, bad[:5]

    def test_ids_stable_across_runs(self) -> None:
        from interfaces import external_api as E
        from tools.build_external_authority import _collect

        on_disk = {r["ext_id"] for r in E.load()}
        fresh, _st = _collect()
        assert {r["ext_id"] for r in fresh} == on_disk, (
            "重算得到的编号集合与落盘表不一致 ⇒ 编号不稳定（历史引用会断）")


class TestFrontendContract:
    """F. 前端契约。"""

    def test_view_registered(self) -> None:
        h = _html()
        assert 'key: "external"' in h, "关系图谱缺「外部法规」视图"
        assert "function _gExternal(" in h
        assert 'cur === "external" ? _gExternal(d)' in h

    def test_external_visually_distinct(self) -> None:
        """外部节点必须与语料内节点**视觉可分**（点线/空心），且图例可查。"""
        h = _html()
        assert "语料外（点线）" in h or "语料外法规（点线" in h
        assert 'stroke-dasharray="2 3"' in h or 'stroke-dasharray="2 2"' in h

    def test_states_files_are_outside_corpus(self) -> None:
        """页面必须明说"不代表库里能打开这份文件"——否则会被当成已收录。"""
        h = _html()
        assert "不代表库里能打开" in h

    def test_zero_external_links(self) -> None:
        h = _html()
        assert "http://" not in h and "https://" not in h
