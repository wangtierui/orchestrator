# -*- coding: utf-8 -*-
"""CanonicalChunk — 内核与外界唯一契约（源无关）。

上游 connector（无论来自 clause 还是任意文件/库）只需产出此结构，
即可接入整套 RAG，无需关心内部用哪个 embedder / 向量库 / LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class CanonicalChunk:
    text: str                                   # 切块正文（如某一条款 body）
    meta: Dict[str, Any] = field(default_factory=dict)
    chunk_id: str | None = None

    # meta 建议字段（透传出处，供检索结果引用/溯源）：
    #   source           源标识 (pbc/gov/mof/nfra/...)
    #   title            文档标题
    #   document_number  文号
    #   publish_date     发布日期
    #   issuing_authority 发文机关
    #   chapter          章号/章名
    #   article_no       条号（真实条号，如 "第五十五条"）
    #   source_url       原文链接（可点击溯源）

    def to_document_meta(self, *, embed_model: str, embed_dim: int) -> Dict[str, Any]:
        """转成 LlamaIndex Document 的 metadata，并打上「哪个模型生成的」标记。"""
        m = dict(self.meta)
        m["_embed_model"] = embed_model      # 防跨模型向量混用
        m["_embed_dim"] = embed_dim
        return m
