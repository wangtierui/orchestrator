# -*- coding: utf-8 -*-
"""RagConfig — RAG 检索的少量运行参数。

模型已定死在 ``rag/embedding.py``（BGE-M3 / 1024 维），不在配置里出现。
本模块只保留与运行环境相关的项（向量库连接、返回条数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from config import settings


@dataclass
class RagConfig:
    # 向量库连接（pgvector）
    vector_store: Dict[str, Any] = field(
        default_factory=lambda: dict(settings.rag.vector_store)
    )
    top_k: int = field(default_factory=lambda: settings.rag.top_k)


def load_default() -> RagConfig:
    """默认指向本机 PG 向量库。"""
    return RagConfig()
