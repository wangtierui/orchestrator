# -*- coding: utf-8 -*-
"""API embedding（OpenAI 等）—— 需 key、出网。作为本地模型的替换项。"""
from __future__ import annotations

from .base import Embedder


def build_openai(
    model: str = "text-embedding-3-small",
    api_key: str | None = None,
    **kwargs,
) -> Embedder:
    from llama_index.embeddings.openai import OpenAIEmbedding

    return OpenAIEmbedding(model=model, api_key=api_key, **kwargs)
