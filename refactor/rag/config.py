# -*- coding: utf-8 -*-
"""RagConfig — 全部可替换组件的开关集中地。

切换任何组件都只改这里（或环境变量），不碰业务代码。
约定：每个组件 = (name, kwargs)；name 在对应 registry 中解析为工厂。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class RagConfig:
    # 向量化（Encoding）模型：bge_m3 / bge_base_zh / bge_small_zh / openai ...
    embedder_name: str = "bge_m3"
    embedder_kwargs: Dict[str, Any] = field(default_factory=dict)
    embed_dim: int = 1024                      # 必须与 embedder 输出维度一致

    # 向量库：当前指向已运行的 pglayers PG（含 pgvector）
    vector_store: Dict[str, Any] = field(default_factory=lambda: {
        "name": "pgvector",
        "host": "localhost", "port": 5432,
        "user": "root", "password": "5462",
        "database": "test", "table_name": "clause_chunks",
    })

    # 生成模型：ollama（本地）/ openai（API）
    llm_name: str = "ollama"
    llm_kwargs: Dict[str, Any] = field(default_factory=dict)

    # 重排（可选）：none / hf_cross_encoder
    reranker_name: str = "none"
    reranker_kwargs: Dict[str, Any] = field(default_factory=dict)

    top_k: int = 5


def load_default() -> RagConfig:
    """默认指向本机 pglayers PG + 本地 bge-m3 + 本地 ollama。"""
    return RagConfig()
