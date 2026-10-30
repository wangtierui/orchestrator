# -*- coding: utf-8 -*-
"""ingest — 把 CanonicalChunk 列表写入向量库（一次性离线任务）。"""
from __future__ import annotations

from typing import List

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex

from .adapters import get_embedder, get_vector_store
from .adapters.vectorstores import ensure_compatible
from .canonical import CanonicalChunk
from .config import RagConfig

# embedder 加载慢（bge_m3 约 8s），connector 按批多次调用 ingest 时复用，避免重复加载。
_EMBEDDER_CACHE: dict = {}


def _get_cached_embedder(config: RagConfig):
    key = (
        config.embedder_name,
        config.embed_dim,
        tuple(sorted(config.embedder_kwargs.items())),
    )
    if key not in _EMBEDDER_CACHE:
        _EMBEDDER_CACHE[key] = get_embedder(config.embedder_name, **config.embedder_kwargs)
    return _EMBEDDER_CACHE[key]


def ingest(chunks: List[CanonicalChunk], config: RagConfig | None = None) -> VectorStoreIndex:
    config = config or RagConfig()

    embedder = _get_cached_embedder(config)
    # 把 embed_dim 并入向量库参数（否则 PGVectorStore 用默认 1024 建表，与向量维度冲突）
    vs_spec = {**config.vector_store, "embed_dim": config.embed_dim}
    vector_store = get_vector_store(vs_spec)
    # 换模型/维度时自动重索引，避免新旧向量混用
    ensure_compatible(vs_spec, config.embed_dim)
    Settings.embed_model = embedder  # 入库编码用此模型

    docs = []
    for c in chunks:
        # chunk_id 作为 LlamaIndex 的 doc_id：向量库按 doc_id upsert，
        # 使增量重跑（同 key_md5 同条款位置）幂等覆盖，而非写重复节点。
        kw = {
            "text": c.text,
            "metadata": c.to_document_meta(
                embed_model=config.embedder_name, embed_dim=config.embed_dim
            ),
        }
        if c.chunk_id:
            kw["id_"] = c.chunk_id
        docs.append(Document(**kw))
    # 必须用 StorageContext 才能把向量真正写入 PG（直接传 vector_store= 会被忽略）
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_documents(docs, storage_context=storage_context)
    return index
