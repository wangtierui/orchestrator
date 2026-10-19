# -*- coding: utf-8 -*-
"""rerankers — 检索后重排（可选，提升精度）。none = 不重排。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from llama_index.core.schema import NodeWithScore

from ..runtime import detect_device


def _noop(nodes: Sequence[NodeWithScore]) -> List[NodeWithScore]:
    return list(nodes)


def build_hf_cross_encoder(model_name: str = "BAAI/bge-reranker-v2-m3", **kwargs) -> Callable:
    from llama_index.core.postprocessor import SentenceTransformerRerank

    # Mac 走 mps，其它走 cpu（与 embedding 同一套探测）
    device = kwargs.get("device") or detect_device()
    pp = SentenceTransformerRerank(model=model_name, top_n=kwargs.get("top_n", 5), device=device)

    def rerank(nodes: Sequence[NodeWithScore]) -> List[NodeWithScore]:
        return pp.postprocess_nodes(list(nodes))

    return rerank


_FACTORIES: Dict[str, Optional[Callable]] = {
    "none": None,
    "hf_cross_encoder": build_hf_cross_encoder,
}


def get_reranker(name: str = "none", **kwargs) -> Optional[Callable]:
    if name not in _FACTORIES:
        raise KeyError(f"未知 reranker: {name!r}，可选: {sorted(_FACTORIES)}")
    factory = _FACTORIES[name]
    return factory(**kwargs) if factory else None
