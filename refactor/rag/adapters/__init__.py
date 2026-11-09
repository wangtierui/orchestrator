# -*- coding: utf-8 -*-
"""adapters — 向量库适配层（唯一与运行环境相关的可替换组件）。

embedding 模型已定死在 rag/embedding.py；本层只管向量库，
生成答案由外部 Agent 承担。
"""
from .vectorstores import get_vector_store

__all__ = ["get_vector_store"]
