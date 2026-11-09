# -*- coding: utf-8 -*-
"""rag — 通用 RAG 检索内核（数据无关）。

设计原则：
  - 采集/清洗/切块由上游 pipeline 负责，本包只消费 CanonicalChunk；
  - embedding 固定为 BGE-M3（见 rag/embedding.py），向量库为 pgvector；
  - 只做「向量化 + 检索」，返回原始条款；生成答案由外部 Agent 承担，
    本包不加载任何生成模型。
"""
from .config import RagConfig, load_default
from .canonical import CanonicalChunk

__all__ = ["RagConfig", "load_default", "CanonicalChunk"]
