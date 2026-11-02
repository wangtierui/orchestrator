# -*- coding: utf-8 -*-
"""RagConfig — 全部可替换组件的开关集中地。

切换任何组件都只改这里（或环境变量），不碰业务代码。
约定：每个组件 = (name, kwargs)；name 在对应 registry 中解析为工厂。

实现说明（2026-10-30）：默认值统一从 refactor.config.settings 派生，
不再散落硬编码 host/port/user/password/embedder 等；改配置只改 config.yaml。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

try:
    from refactor.config import settings
except ImportError:  # 兼容从 refactor/ 目录直接运行（collectors 上下文）
    from config import settings


@dataclass
class RagConfig:
    # 向量化（Encoding）模型：bge_m3 / bge_base_zh / bge_small_zh / openai ...
    embedder_name: str = field(default_factory=lambda: settings.rag.embedder_name)
    embedder_kwargs: Dict[str, Any] = field(default_factory=dict)
    embed_dim: int = field(default_factory=lambda: settings.rag.embed_dim)  # 须与 embedder 输出维度一致

    # 向量库：当前指向已运行的 pglayers PG（含 pgvector）
    vector_store: Dict[str, Any] = field(
        default_factory=lambda: dict(settings.rag.vector_store)
    )

    # 生成模型：ollama（本地）/ openai（API）
    llm_name: str = field(default_factory=lambda: settings.rag.llm_name)
    llm_kwargs: Dict[str, Any] = field(default_factory=dict)

    # 重排（可选）：none / hf_cross_encoder
    reranker_name: str = field(default_factory=lambda: settings.rag.reranker_name)
    reranker_kwargs: Dict[str, Any] = field(default_factory=dict)

    top_k: int = field(default_factory=lambda: settings.rag.top_k)


def load_default() -> RagConfig:
    """默认指向本机 pglayers PG + 本地 bge-m3 + 本地 ollama（全部来自统一配置）。"""
    return RagConfig()
