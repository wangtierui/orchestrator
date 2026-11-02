# -*- coding: utf-8 -*-
"""ingest — 把 CanonicalChunk 列表写入向量库（storage-native stage）。

关键修正：
  - 不走 VectorStoreIndex.from_documents —— 它把 Document 转 Node 时会重生成
    随机 node_id，导致无法按我们的 chunk_id 幂等 upsert（PGVectorStore.add 又是
    纯 INSERT、node_id 无唯一约束，重跑只会堆叠重复行）。
  - 改为直接构造 TextNode(id_=chunk_id)，node_id 即幂等键；注入前先按 node_id
    删旧行，实现 upsert 语义。
  - embedding 批量计算（比逐节点 get_embedding 快）。
"""
from __future__ import annotations

from typing import List

from llama_index.core import Settings
from llama_index.core.schema import TextNode

from .adapters import get_embedder, get_vector_store
from .adapters.vectorstores import ensure_compatible, delete_nodes_by_id
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


def ingest(chunks: List[CanonicalChunk], config: RagConfig | None = None) -> List[TextNode]:
    """把一批 chunk 幂等写入向量库，返回写入的 nodes。

    幂等：先按 chunk_id(node_id) 删旧行，再 add。重跑同一批不产生重复节点。
    """
    config = config or RagConfig()
    if not chunks:
        return []
    embedder = _get_cached_embedder(config)
    vs_spec = {**config.vector_store, "embed_dim": config.embed_dim}
    vector_store = get_vector_store(vs_spec)
    ensure_compatible(vs_spec, config.embed_dim)
    Settings.embed_model = embedder

    # 批量算 embedding（比逐节点 get_embedding 高效）
    texts = [c.text for c in chunks]
    embeddings = embedder.get_text_embedding_batch(texts)

    nodes = []
    for c, emb in zip(chunks, embeddings):
        meta = c.to_document_meta(
            embed_model=config.embedder_name, embed_dim=config.embed_dim
        )
        nodes.append(
            TextNode(text=c.text, id_=c.chunk_id or None, embedding=emb, metadata=meta)
        )

    # PGVectorStore.add 是纯 INSERT（node_id 无唯一约束），先删同 id 旧节点实现 upsert
    ids = [c.chunk_id for c in chunks if c.chunk_id]
    if ids:
        delete_nodes_by_id(vs_spec, ids)
    vector_store.add(nodes)
    return nodes
