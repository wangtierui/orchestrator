# -*- coding: utf-8 -*-
"""ingest — 把 CanonicalChunk 列表写入向量库（一次性离线任务）。"""
from __future__ import annotations

from typing import List

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex

from .adapters import get_embedder, get_vector_store
from .adapters.vectorstores import ensure_compatible
from .canonical import CanonicalChunk
from .config import RagConfig


def ingest(chunks: List[CanonicalChunk], config: RagConfig | None = None) -> VectorStoreIndex:
    config = config or RagConfig()

    embedder = get_embedder(config.embedder_name, **config.embedder_kwargs)
    # 把 embed_dim 并入向量库参数（否则 PGVectorStore 用默认 1024 建表，与向量维度冲突）
    vs_spec = {**config.vector_store, "embed_dim": config.embed_dim}
    vector_store = get_vector_store(vs_spec)
    # 换模型/维度时自动重索引，避免新旧向量混用
    ensure_compatible(vs_spec, config.embed_dim)
    Settings.embed_model = embedder  # 入库编码用此模型

    docs = [
        Document(
            text=c.text,
            metadata=c.to_document_meta(
                embed_model=config.embedder_name, embed_dim=config.embed_dim
            ),
        )
        for c in chunks
    ]
    # 必须用 StorageContext 才能把向量真正写入 PG（直接传 vector_store= 会被忽略）
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_documents(docs, storage_context=storage_context)
    return index
