# -*- coding: utf-8 -*-
"""query — 检索 + 重排 + 生成，返回答案与可溯源引用。"""
from __future__ import annotations

from llama_index.core import PromptTemplate, Settings, VectorStoreIndex
from llama_index.core.schema import NodeWithScore

from .adapters import get_embedder, get_llm, get_reranker, get_vector_store
from .config import RagConfig

_PROMPT = PromptTemplate(
    "你是监管条款问答助手。仅依据下方【条款】作答，并标注每条依据的出处（标题+条号）。\n"
    "若条款不足以回答，说明信息不足。\n\n"
    "【条款】\n{context}\n\n"
    "【问题】{question}\n\n【回答】"
)


def _fmt_citation(n: NodeWithScore) -> str:
    m = n.metadata or {}
    title = m.get("title", "未知文档")
    art = m.get("article_no", "")
    src = m.get("source_url", "")
    cite = f"《{title}》{art}".rstrip()
    return f"[{cite}] {n.get_content()}" + (f" ({src})" if src else "")


def query(question: str, config: RagConfig | None = None) -> dict:
    config = config or RagConfig()

    embedder = get_embedder(config.embedder_name, **config.embedder_kwargs)
    vector_store = get_vector_store(config.vector_store)
    llm = get_llm(config.llm_name, **config.llm_kwargs)
    reranker = get_reranker(config.reranker_name, **config.reranker_kwargs)
    Settings.embed_model = embedder  # 查询编码用同一模型（必须与入库一致）

    index = VectorStoreIndex.from_vector_store(vector_store)
    retriever = index.as_retriever(similarity_top_k=config.top_k)
    nodes = retriever.retrieve(question)
    if reranker:
        nodes = reranker(nodes)

    context = "\n\n".join(_fmt_citation(n) for n in nodes)
    answer = llm.predict(_PROMPT, context=context, question=question)
    citations = [
        {
            "title": (n.metadata or {}).get("title"),
            "article_no": (n.metadata or {}).get("article_no"),
            "source_url": (n.metadata or {}).get("source_url"),
            "score": round(getattr(n, "score", 0.0) or 0.0, 4),
        }
        for n in nodes
    ]
    return {"answer": answer, "citations": citations}
