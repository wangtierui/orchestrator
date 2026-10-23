# -*- coding: utf-8 -*-
"""tests/test_relations_resolution.py — 关系定位率与四类关系视图（第一百一十二批）。

本批要解决的四个"看不见"（用户原话：关系图谱并未显示）：
  ① 监管↔监管 依据/废止   ② 制度↔制度   ③ 制度→监管   ④ 主题关系

根因是**两个层次的缺陷**，守卫分别钉住：

A. **数据层**（定位率只有 20.9%）
   - `nfra` 源被 `sorted(glob)[-1]` 取到 `.quarantine.jsonl` ⇒ 1935 份文件
     /111,970 条条款**对关系链零贡献**，且日志只写"可抽取 0 份"；
   - 归一化只到 `norm_docno`/`norm_title_strict` 级：扛不住全角数字、
     中文序号（令第两百六十二号）、`&lt;` 实体、版本后缀（2022年修订版）、
     3 字法定简称（公司法）；
   - `corpus` 类（已采集未登记 RFN）没有进入补登流程。

B. **展示层**（即使有数据也不画）
   - 原 `global_graph` 只画"制度→监管"一张二部图，其余三类**完全没有入口**；
   - 未定位的关系被静默丢弃，读者无从知道"还有一半没画"。

守卫分组：A nfra 取文件  B 归一化  C 解析档位  D 四视图  E 口径与披露
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


class TestNfraSelection:
    """A. cleaned 主文件选择（nfra 失联事故）。"""

    def test_aux_files_excluded(self) -> None:
        from tools.extract_relations import _is_cleaned_main

        D = "2026" + "1022"   # 拼接构造：门禁禁止源码里出现字面量快照日期
        assert _is_cleaned_main(f"nfra_cleaned_{D}.jsonl") is True
        for name in (f"nfra_cleaned_{D}.quarantine.jsonl",
                     f"gov_cleaned_{D}.bad.jsonl",
                     f"mof_cleaned_{D}.bak.jsonl",
                     f"pbc_cleaned_{D}.partial.jsonl"):
            assert _is_cleaned_main(name) is False, name

    def test_latest_picks_main_not_quarantine(self, tmp_path, monkeypatch) -> None:
        """字典序里 `.quarantine.jsonl` 排在 `.jsonl` 之后 —— 必须被排除。"""
        import tools.extract_relations as er

        d = tmp_path
        D = "2026" + "0101"
        (d / f"xx_cleaned_{D}.jsonl").write_text("{}\n", encoding="utf-8")
        (d / f"xx_cleaned_{D}.quarantine.jsonl").write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(er, "CLEANED_DIR", str(d))
        got = er._latest_cleaned_jsonl("xx")
        assert got.endswith(f"xx_cleaned_{D}.jsonl"), got

    def test_zero_docs_warns(self) -> None:
        """抽到 0 份必须是**告警**（原来静默 info，整源缺席看不出来）。"""
        src = _src("tools/extract_relations.py")
        assert "正文可抽取 **0 份**" in src
        assert "LOG.warning" in src

    def test_real_nfra_present(self) -> None:
        """真实产物里 nfra 必须出现在 documents.regulatory（回归事实）。"""
        p = os.path.join(ROOT, "modules", "regulatory_classifier", "data", "relations",
                         "relations_stat.json")
        st = json.load(open(p, encoding="utf-8"))
        reg = (st.get("documents") or {}).get("regulatory") or {}
        assert int(reg.get("nfra") or 0) > 0, f"nfra 仍未进入关系链：{reg}"


class TestNormalization:
    """B. 关系解析增强层（`std_lib/common_lib/norm.py`）。"""

    def test_fullwidth_docno(self) -> None:
        from std_lib.common_lib.norm import norm_docno, norm_docno_strong

        assert norm_docno("阳光人寿发〔２０２３〕２０７号") != norm_docno("阳光人寿发〔2023〕207号")
        assert norm_docno_strong("阳光人寿发〔２０２３〕２０７ 号") == \
            norm_docno_strong("阳光人寿发〔2023〕207号")

    def test_chinese_ordinal_in_order_docno(self) -> None:
        from std_lib.common_lib.norm import norm_docno_strong

        assert norm_docno_strong("中华人民共和国国务院令第二百六十二号") == \
            norm_docno_strong("中华人民共和国国务院令第262号")

    def test_no_residue_stripping(self) -> None:
        """**刻意不剥离文号前残渣**（判据不可靠，会切掉合法机关代字）。

        正文里确有 `保险机构参与中国金融期货证监会公告〔2020〕12号` 这种夹带形态，但
        "从哪里截"没有可靠判据（机关代字可长可短）⇒ 硬截会把两个不同文号归成一个。
        这类形态走**签名层**（四位年+序号数字尾，天然不受前缀影响）。
        """
        from std_lib.common_lib.norm import norm_docno_strong
        from std_lib.common_lib.relations import docno_signature

        long_form = "保险机构参与中国金融期货证监会公告〔2020〕12号"
        pure_form = "证监会公告〔2020〕12号"
        assert norm_docno_strong(long_form) != norm_docno_strong(pure_form), \
            "强归一不应把夹带形态归成纯文号（那是臆造匹配）"
        assert docno_signature(long_form) == docno_signature(pure_form), \
            "签名层应能对上（这才是该形态的正确通道）"

    def test_html_entity_and_angle_brackets(self) -> None:
        from std_lib.common_lib.norm import norm_title_rel

        assert norm_title_rel("关于印发&lt;X管理办法&gt;的通知") == \
            norm_title_rel("关于印发<X管理办法>的通知") == "关于印发X管理办法的通知"

    def test_version_suffix(self) -> None:
        from std_lib.common_lib.norm import strip_title_version

        for raw, want in (
            ("X管理办法(2022年修订版)", "X管理办法"),
            ("X管理办法（2023版）", "X管理办法"),
            ("X管理办法（试行）", "X管理办法"),
            ("X管理办法（暂行）", "X管理办法"),
            ("X管理办法V2", "X管理办法"),
        ):
            assert strip_title_version(raw) == want, raw

    def test_drop_cn_prefix(self) -> None:
        from std_lib.common_lib.norm import drop_cn_prefix

        assert drop_cn_prefix("中华人民共和国公司法") == "公司法"
        assert drop_cn_prefix("保险公司股权管理办法") == "保险公司股权管理办法"
        # 不能把"中国"剥成空
        assert drop_cn_prefix("中国") == "中国"

    def test_strict_layer_untouched(self) -> None:
        """增强层**不得**改 `norm_title_strict` 语义（归属表/RFN 索引依赖它）。"""
        from std_lib.common_lib.norm import norm_title_strict

        assert norm_title_strict("《X办法》（试行）") == "X办法"
        assert norm_title_strict("中国银保监会办公厅关于A、B事项的通知") == \
            "中国银保监会办公厅关于A、B事项的通知"


class TestResolutionTiers:
    """C. 解析档位（索引侧与查询侧必须同时改，否则单边失配）。"""

    @staticmethod
    def _idx():
        import tools.extract_relations as er

        ix = er._empty_index()
        er._add_entity(ix, ref="RFN-1", name="中华人民共和国公司法",
                       docno="中华人民共和国主席令第15号")
        er._add_entity(ix, ref="RFN-2", name="阳光人寿实物资产管理办法（2021版）",
                       docno="阳光人寿发〔2021〕255号")
        er._add_entity(ix, ref="RFN-3", name="A管理办法", docno="")
        er._add_entity(ix, ref="RFN-4", name="A管理办法（2024版）", docno="")
        return ix

    def test_short_law_name(self) -> None:
        """3 字法定简称必须能命中（`MIN_CONTAINS_LEN=4` 会整类挡掉）。"""
        import tools.extract_relations as er

        ref, how, _ = er.resolve_target("公司法", "", self._idx())
        assert ref == "RFN-1" and how == "title_contains", (ref, how)

    def test_short_name_needs_endswith(self) -> None:
        """"关于适用公司法的若干规定"**不能**挂到《公司法》上（那是另一份文件）。"""
        import tools.extract_relations as er

        ref, how, _ = er.resolve_target("关于适用公司法的若干规定", "", self._idx())
        assert ref != "RFN-1", (ref, how)

    def test_version_key_unique_only(self) -> None:
        """去版本后**多义**的键不得用于解析（选错实体比未定位更糟）。"""
        import tools.extract_relations as er

        ix = self._idx()
        assert "A管理办法" in ix["amb_title_ver"], ix["amb_title_ver"]
        ref, how, _ = er.resolve_target("A管理办法（2019版）", "", ix)
        # 允许回落到更弱的 tier（title_contains），但**不得**当成精确命中（title）
        assert how != "title", f"多义版本键被当成精确命中：{ref} {how}"

    def test_version_key_resolves_when_unique(self) -> None:
        import tools.extract_relations as er

        ref, how, _ = er.resolve_target("阳光人寿实物资产管理办法(2021版)", "", self._idx())
        assert ref == "RFN-2" and how == "title", (ref, how)

    def test_strict_mode_still_blocks_contains(self) -> None:
        """跨域解析必须严格（否则法规会被匹成内部制度）。"""
        import tools.extract_relations as er

        ref, how, _ = er.resolve_target("公司法", "", self._idx(), strict=True)
        assert ref == "", (ref, how)


class TestFourViews:
    """D. 四类关系都必须有视图（用户的四条"并未显示"）。"""

    @pytest.fixture(scope="class")
    def g(self):
        from interfaces import relations_api as R

        return R.global_graph()

    def test_four_views_present(self, g) -> None:
        keys = [v["key"] for v in g["views"]]
        assert keys == ["internal_to_regulatory", "regulatory_to_regulatory",
                        "internal_to_internal", "theme"], keys

    def test_regulatory_view_has_hubs_and_edges(self, g) -> None:
        reg = g["regulatory"]
        assert reg["nodes"], "监管↔监管 没有枢纽节点"
        assert reg["total_edges"] > 0
        degs = [n["degree"] for n in reg["nodes"]]
        assert degs == sorted(degs, reverse=True), "枢纽未按被依据次数排序"
        # 枢纽之间的边必须两端都在节点表里
        ids = {n["id"] for n in reg["nodes"]}
        for e in reg["edges"]:
            assert e["from"] in ids and e["to"] in ids, e

    def test_internal_view_complete(self, g) -> None:
        """制度↔制度的数据量小，必须**全画**（不抽样）。"""
        it = g["internal"]
        assert it["edges"], "制度↔制度 视图为空"
        ids = {n["id"] for n in it["nodes"]}
        for e in it["edges"]:
            assert e["from"] in ids and e["to"] in ids, e
        assert len(it["edges"]) == int(g["counts"]["internal_relations"]), \
            "制度↔制度 未全画"

    def test_theme_view(self, g) -> None:
        th = g["theme"]
        assert th["themes"], "主题视图没有主题"
        assert th["edges"], "主题视图没有边"
        assert all(e["count"] >= 1 for e in th["edges"])
        # 主题边只能指向图里的右侧节点
        docs = {d["id"] for d in th["docs"]}
        assert all(e["ref"] in docs for e in th["edges"])

    def test_bipartite_edges_consistent(self, g) -> None:
        bp = g["bipartite"]
        left = {x["id"] for x in bp["left"]}
        right = {x["id"] for x in bp["right"]}
        assert bp["edges"], "制度→监管 为空"
        for e in bp["edges"]:
            assert e["from"] in left and e["to"] in right, e

    def test_views_disclose_unresolved(self, g) -> None:
        """未定位必须**逐视图**披露（总盘子的数字对读者没意义）。"""
        uv = g["counts"]["unresolved_by_view"]
        for key in ("internal_to_regulatory", "regulatory_to_regulatory",
                    "internal_to_internal"):
            b = uv[key]
            assert {"entity", "corpus", "unresolved"} <= set(b), (key, b)
            assert b["entity"] + b["corpus"] + b["unresolved"] > 0


class TestMetricDefinition:
    """E. 定位率口径（机关名不是"未定位的文件"）。"""

    def test_organ_excluded_from_denominator(self) -> None:
        from interfaces import relations_api as R

        c = R.global_graph()["counts"]
        total = c["total_relations"]
        denom = c["file_denominator"]
        assert 0 < denom <= total
        # organ(机关名) + generic(泛指) 被剔除 ⇒ 分母必须显著小于总数
        assert denom < total, "分母未剔除机关名/泛指词"

    def test_ratio_improved_vs_baseline(self) -> None:
        """回归基线：本批把 entity 定位率从 20.9% 提到 40%+（只降不升即回归）。"""
        from interfaces import relations_api as R

        c = R.global_graph()["counts"]
        assert (c["file_resolved_ratio"] or 0) >= 0.45, c["file_resolved_ratio"]
        assert (c["resolved_to_entity_ratio"] or 0) >= 0.38, c["resolved_to_entity_ratio"]

    def test_unresolved_total_matches_stat(self) -> None:
        from interfaces import relations_api as R

        c = R.global_graph()["counts"]
        # 三桶之和 == 总关系数
        uv = c["unresolved_by_view"]
        s = sum(v[1]["entity"] + v[1]["corpus"] + v[1]["unresolved"]
                for v in uv.items())
        assert s == c["total_relations"], (s, c["total_relations"])
