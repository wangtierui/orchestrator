# -*- coding: utf-8 -*-
"""P1 语义增强**执行门面**测试（N-162）：选型 / 参数传递 / 异常处理路径。

设计：**不加载真实大模型**（秒级完成、可在 CI 跑）——
以 monkeypatch 替换 `semantic_models.load_embedder/load_segmenter`，
从而确定性地覆盖"成功 / 链式降级 / 全链耗尽（strict 两态）"四条路径。
真实加载的正确性由 `tools/smoke_semantic_models.py` 负责（重、按需）。
"""

from __future__ import annotations

import pytest

from std_lib.common_lib import semantic_enhance as se
from std_lib.common_lib import semantic_models as sm
from std_lib.common_lib import semantic_tools as st


class _FakeEmb:
    def __init__(self, name: str) -> None:
        self.name = name

    def encode(self, texts, normalize: bool = True):
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture(autouse=True)
def _clear_cache():
    st.load_manifest.cache_clear()
    yield
    st.load_manifest.cache_clear()


def test_selection_policy_is_ssot_codes():
    """选型来自清单 selection_policy（本模块不得自带模型名）。"""
    pol = st.load_manifest().get("selection_policy") or {}
    modes = pol.get("modes") or {}
    assert modes["full"]["primary"] == "bge_base_zh"
    assert "text2vec" in modes["full"]["fallback"]
    assert modes["incremental"]["primary"] == "youtu_embedding"


def test_choose_embedder_prefers_primary_and_honours_override():
    """全量 → 首选 bge；增量 → youtu；**显式指定优先级最高**。"""
    assert se.choose_embedder("full") == "bge_base_zh"
    assert se.choose_embedder("incremental") == "youtu_embedding"
    assert se.choose_embedder("full", requested="text2vec") == "text2vec"


def test_embed_success_reports_model_and_no_fallback(monkeypatch):
    monkeypatch.setattr(sm, "load_embedder", lambda name: _FakeEmb(name))
    r = se.embed(["a", "b"], mode="full")
    assert r.ok and not r.fallback
    assert r.model == "bge_base_zh"
    assert len(r.vectors) == 2


def test_embed_degrades_to_fallback_candidate(monkeypatch):
    """首选加载失败 → **自动降级**到 fallback 候选（text2vec），并如实报告实际模型。"""
    def _boom(name):
        if name == "bge_base_zh":
            raise sm.ModelUnavailable(name, "注入的失败")
        return _FakeEmb(name)

    monkeypatch.setattr(sm, "load_embedder", _boom)
    r = se.embed(["x"], mode="full")
    assert r.ok and not r.fallback
    assert r.model == "text2vec"
    assert r.chain_tried == ["bge_base_zh", "text2vec"]


def test_embed_exhausted_strict_false_returns_fallback_notice(monkeypatch):
    """全链耗尽 + strict=False（默认）→ **不抛**，返回 ok=False 且带回退留痕。"""
    def _boom(name):
        raise sm.ModelUnavailable(name, "注入的失败")

    monkeypatch.setattr(sm, "load_embedder", _boom)
    r = se.embed(["x"], mode="full")
    assert not r.ok and r.fallback and r.vectors is None
    assert r.notice  # 回退**可观测**（不得静默）


def test_embed_exhausted_strict_true_raises(monkeypatch):
    """strict=True → 抛 `EnhanceUnavailable`（"必须用增强"的场景）。"""
    def _boom(name):
        raise sm.ModelUnavailable(name, "注入的失败")

    monkeypatch.setattr(sm, "load_embedder", _boom)
    with pytest.raises(se.EnhanceUnavailable) as ei:
        se.embed(["x"], mode="full", strict=True)
    assert ei.value.reasons  # 每个候选的失败原因都在


def test_split_default_is_byte_equivalent_to_ssot():
    """**默认（ML 关）必须与受控 SSOT 逐项一致** —— 这是"接线零回归"的硬约束。"""
    from std_lib.common_lib.sentence_boundary import split_by

    t = "第一条 为了规范，制定本办法。第二条 违反的，责令改正；情节严重的，罚款。"
    for lvl in ("strict", "loose"):
        r = se.split_sentences(t, level=lvl, use_ml=False)
        assert r.sentences == split_by(t, lvl)
        assert r.basis == f"deterministic:{lvl}"
        assert not r.fallback


def test_split_ml_failure_falls_back_with_notice(monkeypatch):
    """ML 增强失败 → 回退确定性切分，且 `fallback=True` + notice（**不静默**）。"""
    def _boom(name="ltp"):
        raise sm.ModelUnavailable(name, "注入的失败")

    monkeypatch.setattr(sm, "load_segmenter", _boom)
    t = "甲。乙。"
    r = se.split_sentences(t, level="strict", use_ml=True)
    assert r.fallback and r.notice
    assert r.sentences == ["甲。", "乙。"]


def test_split_strict_raises_on_ml_failure(monkeypatch):
    def _boom(name="ltp"):
        raise sm.ModelUnavailable(name, "注入的失败")

    monkeypatch.setattr(sm, "load_segmenter", _boom)
    with pytest.raises(se.EnhanceUnavailable):
        se.split_sentences("甲。", use_ml=True, strict=True)


def test_relations_split_is_wired_through_facade():
    """**接线断言**：`relations.split_sentences` 必须经门面（默认路径与 SSOT 对等）。"""
    from std_lib.common_lib.relations import split_sentences as rel_split
    from std_lib.common_lib.sentence_boundary import split_by

    t = "甲。乙；丙。"
    assert rel_split(t) == split_by(t, "loose")


def test_describe_discloses_chain_and_readiness():
    d = se.describe()
    assert d["modes"]["full"]["chain"][0] == "bge_base_zh"
    assert d["modes"]["incremental"]["chain"][0] == "youtu_embedding"
    assert "split_ml_enabled" in d
