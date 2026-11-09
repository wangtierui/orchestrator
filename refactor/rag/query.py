# -*- coding: utf-8 -*-
"""retrieve — 纯语义检索：返回原始条款与出处，不生成自然语言答案。

本项目只负责「向量化 + 检索」。生成答案与 Agent 编排由外部商业产品承担，
因此这里把条款正文（content）原样交出去，供调用方自行组织答案、标注引用。
本项目不加载任何生成模型。
"""
from __future__ import annotations

from llama_index.core import Settings, VectorStoreIndex

from .adapters import get_vector_store
from .config import RagConfig
from .embedding import get_embedder


def retrieve(question: str, config: RagConfig | None = None) -> dict:
    """检索最相关条款，返回 ``{"chunks": [...]}``（含正文、溯源字段、相关度）。"""
    config = config or RagConfig()
    Settings.embed_model = get_embedder()
    vector_store = get_vector_store(config.vector_store)

    index = VectorStoreIndex.from_vector_store(vector_store)
    retriever = index.as_retriever(similarity_top_k=config.top_k)
    nodes = retriever.retrieve(question)

    chunks = []
    for n in nodes:
        item = {
            "content": n.get_content(),
            "score": round(getattr(n, "score", 0.0) or 0.0, 4),
        }
        # 溯源字段原样透传；下划线开头的内部标记（_embed_model 等）不外泄
        item.update(
            {k: v for k, v in (n.metadata or {}).items() if not k.startswith("_")}
        )
        chunks.append(item)
    return {"chunks": chunks}
