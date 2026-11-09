# -*- coding: utf-8 -*-
"""ingest — 把 CanonicalChunk 列表写入向量库（storage-native stage）。

  - 不走 VectorStoreIndex.from_documents：它转 Node 时会重生成随机 node_id，
    无法按 chunk_id 幂等 upsert（PGVectorStore.add 是纯 INSERT）。
  - 直接构造 TextNode(id_=chunk_id)，注入前先按 node_id 删旧行，实现 upsert。
  - 模型固定为 rag/embedding.py 的 BGE-M3（1024 维），批量计算 embedding。
"""
from __future__ import annotations

from typing import List

from llama_index.core.schema import TextNode

from .adapters import get_vector_store
from .adapters.vectorstores import assert_compatible, delete_nodes_by_id
from .canonical import CanonicalChunk
from .config import RagConfig
from .embedding import EMBED_DIM, get_embedder


def ingest(chunks: List[CanonicalChunk], config: RagConfig | None = None) -> List[TextNode]:
    """把一批 chunk 幂等写入向量库，返回写入的 nodes。"""
    config = config or RagConfig()
    if not chunks:
        return []
    vs_spec = dict(config.vector_store)
    embedder = get_embedder()
    vector_store = get_vector_store(vs_spec)
    assert_compatible(vs_spec)

    # embedding 由本函数批量算好并直接塞进 TextNode，无需再动全局 Settings
    texts = [c.text for c in chunks]
    embeddings = embedder.get_text_embedding_batch(texts)

    # 真实向量长度校验：不让维度不符的向量静默写进索引
    for emb in embeddings:
        if len(emb) != EMBED_DIM:
            raise RuntimeError(
                f"BGE-M3 输出维度 {len(emb)}，期望 {EMBED_DIM}；索引未写入。"
            )

    nodes = [
        TextNode(
            text=c.text, id_=c.chunk_id or None, embedding=emb,
            metadata=c.to_document_meta(),
        )
        for c, emb in zip(chunks, embeddings)
    ]

    ids = [c.chunk_id for c in chunks if c.chunk_id]
    if ids:
        delete_nodes_by_id(vs_spec, ids)
    vector_store.add(nodes)
    return nodes
