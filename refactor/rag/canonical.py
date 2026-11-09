# -*- coding: utf-8 -*-
"""CanonicalChunk — 内核与外界唯一契约（源无关）。

上游 connector（无论来自 clause 还是任意文件/库）只需产出此结构即可接入 RAG；
内部固定使用 BGE-M3 + pgvector，上游无需关心实现细节。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .embedding import EMBED_DIM, MODEL_ID


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

    def to_document_meta(self) -> Dict[str, Any]:
        """转成 LlamaIndex Document 的 metadata，并标记生成向量的模型与维度。"""
        m = dict(self.meta)
        m["_embed_model"] = MODEL_ID          # 防跨模型向量混用
        m["_embed_dim"] = EMBED_DIM
        return m
