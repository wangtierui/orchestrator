# -*- coding: utf-8 -*-
"""embedders/registry — 名字 → 工厂 的解析表。

『热插拔』的核心：config 里写 "bge_m3"，这里解析成对应实例。
新增一个模型通常只需在这里加一行（同后端甚至只换 model_name，零新代码）。
"""
from __future__ import annotations

from typing import Callable, Dict

from .base import Embedder
from .hf import build_hf
from .openai import build_openai

# name -> 工厂(可接收 **kwargs)。同名模型仅 model_name 不同，归入 hf 后端。
_FACTORIES: Dict[str, Callable[..., Embedder]] = {
    # 本地 HF 后端（按 model_name 区分）
    "bge_m3":       lambda **kw: build_hf("BAAI/bge-m3", **kw),
    "bge_base_zh":  lambda **kw: build_hf("BAAI/bge-base-zh", **kw),
    "bge_small_zh": lambda **kw: build_hf("BAAI/bge-small-zh", **kw),
    "bce":          lambda **kw: build_hf("maidalun/bce-embedding-base_v1", **kw),
    "gte_large_zh": lambda **kw: build_hf("thenlper/gte-large-zh", **kw),
    # API 后端
    "openai":       build_openai,
}


def register_embedder(name: str, factory: Callable[..., Embedder]) -> None:
    _FACTORIES[name] = factory


def get_embedder(name: str = "bge_m3", **kwargs) -> Embedder:
    if name not in _FACTORIES:
        raise KeyError(f"未知 embedder: {name!r}，可选: {sorted(_FACTORIES)}")
    return _FACTORIES[name](**kwargs)
