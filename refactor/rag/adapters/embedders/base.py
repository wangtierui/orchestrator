# -*- coding: utf-8 -*-
"""embedders/base — 本包对「向量化模型」的抽象。

我们不直接重实现 embedding 数学，而是**包裹** LlamaIndex 的
BaseEmbedding 实现（它已对接 sentence-transformers / OpenAI 等）。
这样『可热插拔』由 LlamaIndex 成熟实现承托，我们只在上面做选择/标记。

Embedder 即 LlamaIndex BaseEmbedding 的别名：入库与查询共用同一实例，
保证两边用同一个模型、同一向量空间。
"""
from typing import Protocol, runtime_checkable

try:
    from llama_index.core.embeddings import BaseEmbedding as _LE
    Embedder = _LE
except Exception:  # pragma: no cover - 依赖未装时仅作类型占位
    @runtime_checkable
    class Embedder(Protocol):
        def get_text_embedding(self, text: str) -> list[float]: ...
        def get_query_embedding(self, query: str) -> list[float]: ...
