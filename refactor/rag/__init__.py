# -*- coding: utf-8 -*-
"""rag — 通用 RAG 内核（数据无关、组件可热插拔）。

设计原则：
  - 采集/清洗/切块由上游 pipeline 负责，本包只消费 CanonicalChunk；
  - 向量化/向量库/LLM/重排 四大件均为「可替换适配器」，由 config 的 name 驱动；
  - 切换模型 = 改 config 一行 + 重新 ingest（向量不可跨模型比较）。
"""
from .config import RagConfig, load_default
from .canonical import CanonicalChunk

__all__ = ["RagConfig", "load_default", "CanonicalChunk"]
