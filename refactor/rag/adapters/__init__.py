# -*- coding: utf-8 -*-
"""adapters — 四大可替换组件的工厂入口。"""
from .embedders.registry import get_embedder
from .vectorstores import get_vector_store
from .llms import get_llm
from .rerankers import get_reranker

__all__ = ["get_embedder", "get_vector_store", "get_llm", "get_reranker"]
