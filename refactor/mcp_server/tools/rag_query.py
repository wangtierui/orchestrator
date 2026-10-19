# -*- coding: utf-8 -*-
"""rag_query — 把现成 RAG 的「语义问答」能力做成插件。

验证用户判断的关键点：
  - 它只调用 rag.query.query(question, config)，返回 {answer, citations}；
  - 它「不关心」底层是哪家监管数据、怎么清洗——那些耦合全在「入库 connector」一侧；
  - 将来把新的业务数据灌进 RAG（只要产出 CanonicalChunk 并 ingest），本插件零改动。

运行前提：RAG 的 LLM 已配置（ollama 本地 或 openai API），否则生成那步会失败。
"""
from __future__ import annotations

import os

from ..registry import tool


@tool(
    name="rag_query",
    description=(
        "对监管法规语料做语义问答，返回自然语言答案与条款级引用"
        "（标题+条号+来源链接+相关度）。仅依据已入库的条款作答，不编造。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "用户自然语言问题，如「商业银行在风险控制方面有什么要求？」",
            },
            "top_k": {
                "type": "integer",
                "default": 5,
                "description": "返回的最相关条款数",
            },
        },
        "required": ["question"],
    },
)
def rag_query(args: dict) -> dict:
    from rag.config import RagConfig
    from rag.query import query

    question = args["question"]
    top_k = int(args.get("top_k", 5))

    # 业务无关的通用查询：具体用哪个 embedding / LLM / 维度，由「启动 MCP server 时的
    # 环境变量」决定，不在代码里写死，便于切换（如 RAG_EMBEDDER=bge_small_zh）。
    cfg = RagConfig(
        embedder_name=os.environ.get("RAG_EMBEDDER", "bge_m3"),
        embed_dim=int(os.environ.get("RAG_EMBED_DIM", "1024")),
        llm_name=os.environ.get("RAG_LLM", "ollama"),
        top_k=top_k,
    )
    res = query(question, cfg)
    return {"answer": res.get("answer"), "citations": res.get("citations", [])}
