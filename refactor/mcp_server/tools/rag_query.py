# -*- coding: utf-8 -*-
"""rag_query — 把 RAG 的「法规条款语义检索」能力做成 MCP 工具。

只返回原始条款正文与出处（content / title / article_no / source_url / score）。
生成自然语言答案由调用方（外部商业 Agent 自带大模型）完成，本服务不加载
任何生成模型。
"""
from __future__ import annotations

from ..registry import tool


@tool(
    name="rag_query",
    description=(
        "按语义检索监管法规条款，返回最相关的原始条款正文与出处"
        "（标题+条号+来源链接+相关度）。仅返回库内已有条款，不做生成或改写。"
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
    from rag.query import retrieve

    question = args["question"]
    top_k = int(args.get("top_k", 5))
    cfg = RagConfig(top_k=top_k)
    return retrieve(question, cfg)
