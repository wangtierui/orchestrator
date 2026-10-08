# -*- coding: utf-8 -*-
"""tests.test_semantic_model_cache — 模型加载**进程级缓存**（N-183）机制测试。

背景：`semantic_enhance.split_sentences` / `embed_documents` **每次调用**都会
`load_segmenter` / `load_embedder`；原实现**无缓存** ⇒ 逐文档/逐批重复加载模型
（LTP/HanLP 20~40s、嵌入模型数百 MB）。按 gov 13178 条外推需**数天**，该路径实际不可用。

本测试用 **monkeypatch 替身**验证缓存**机制**（不加载真模型，保持 CI 快速稳定）：
  ① 同 `(name, path)` 第二次调用**不再加载**且返回**同一对象**；
  ② `_resolve()` **仍每次执行**（就绪度/权重缺失照旧 fail-closed，缓存不降低判据强度）；
  ③ 不同 name → 不同缓存条目（互不串扰）；
  ④ `_resolve` 抛错时**不得**命中缓存（权重被移除后必须立刻失败）。
"""
from __future__ import annotations

import pytest

from std_lib.common_lib import semantic_models as sm


class _FakeModel:
    def __init__(self, tag: str) -> None:
        self.tag = tag


def _install(monkeypatch, paths: dict[str, str], counters: dict[str, int]):
    """把 `_resolve` 与两个"真正加载"函数替换为可控替身。"""
    monkeypatch.setattr(sm, "_resolve", lambda name: (paths[name], {"name": name}), raising=True)

    def _seg(name: str):
        counters[f"seg:{name}"] = counters.get(f"seg:{name}", 0) + 1
        return _FakeModel(f"seg-{name}")

    def _emb(name: str):
        counters[f"emb:{name}"] = counters.get(f"emb:{name}", 0) + 1
        return _FakeModel(f"emb-{name}")

    monkeypatch.setattr(sm, "_load_segmenter_uncached", _seg, raising=True)
    monkeypatch.setattr(sm, "_load_embedder_uncached", _emb, raising=True)
    monkeypatch.setattr(sm, "_SEGMENTER_CACHE", {}, raising=True)
    monkeypatch.setattr(sm, "_EMBEDDER_CACHE", {}, raising=True)


def test_segmenter_and_embedder_are_cached(monkeypatch) -> None:
    """① 同 key 只加载一次且返回同一对象；② 每次仍调 `_resolve`（判据不降级）。"""
    paths = {"ltp": "/fake/ltp", "bge_base_zh": "/fake/bge"}
    counters: dict[str, int] = {}
    resolve_calls = {"n": 0}
    _install(monkeypatch, paths, counters)
    real_resolve = sm._resolve

    def _counting_resolve(name: str):
        resolve_calls["n"] += 1
        return real_resolve(name)

    monkeypatch.setattr(sm, "_resolve", _counting_resolve, raising=True)

    a1 = sm.load_segmenter("ltp")
    a2 = sm.load_segmenter("ltp")
    assert a1 is a2, "同 (name, path) 必须复用同一实例"
    assert counters["seg:ltp"] == 1, f"应只加载一次，实际 {counters['seg:ltp']} 次"

    e1 = sm.load_embedder("bge_base_zh")
    e2 = sm.load_embedder("bge_base_zh")
    assert e1 is e2
    assert counters["emb:bge_base_zh"] == 1

    assert resolve_calls["n"] >= 4, "_resolve 必须每次执行（就绪度判据不得被缓存绕过）"


def test_different_names_do_not_share_cache(monkeypatch) -> None:
    """③ 不同 name → 不同条目（互不串扰）。"""
    paths = {"ltp": "/fake/ltp", "hanlp": "/fake/hanlp", "text2vec": "/fake/t2v"}
    counters: dict[str, int] = {}
    _install(monkeypatch, paths, counters)

    ltp = sm.load_segmenter("ltp")
    hlp = sm.load_segmenter("hanlp")
    assert ltp is not hlp
    assert counters["seg:ltp"] == 1 and counters["seg:hanlp"] == 1
    # 再次取用仍各命中自己的缓存
    assert sm.load_segmenter("ltp") is ltp
    assert sm.load_segmenter("hanlp") is hlp
    assert counters == {"seg:ltp": 1, "seg:hanlp": 1}


def test_resolve_failure_is_not_cached(monkeypatch) -> None:
    """④ `_resolve` 抛错（权重缺失）→ **不得**返回缓存实例（fail-closed 优先于缓存）。"""
    paths = {"ltp": "/fake/ltp"}
    counters: dict[str, int] = {}
    _install(monkeypatch, paths, counters)
    first = sm.load_segmenter("ltp")

    def _boom(name: str):
        raise sm.ModelUnavailable(name, "权重被移除（测试）", "重新预置")

    monkeypatch.setattr(sm, "_resolve", _boom, raising=True)
    with pytest.raises(sm.ModelUnavailable):
        sm.load_segmenter("ltp")
    # 恢复后仍能取回原实例（缓存未被破坏）
    monkeypatch.setattr(sm, "_resolve", lambda name: (paths[name], {}), raising=True)
    assert sm.load_segmenter("ltp") is first
    assert counters["seg:ltp"] == 1, "缓存命中路径不得重复加载"
