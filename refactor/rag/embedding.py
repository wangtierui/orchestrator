# -*- coding: utf-8 -*-
"""BGE-M3 —— 本项目唯一的 embedding 模型契约（单一事实来源）。

固定使用 HuggingFace ``BAAI/bge-m3``，输出 1024 维向量。模型名、维度、
加载方式只在这里定义一次；入库、检索、向量库建表全部从这里取值，
配置里不再出现任何模型开关。

权重由 HuggingFace Hub 按需下载并缓存到 ``~/.cache/huggingface/hub``。
本模块**懒加载**：import 不会触发下载或加载，只有第一次调用
:func:`get_embedder` 才把权重读进内存（约 2.3GB）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

MODEL_ID = "BAAI/bge-m3"
EMBED_DIM = 1024


@lru_cache(maxsize=1)
def get_embedder() -> Any:
    """返回进程内共享的 BGE-M3 实例（入库与检索必须共用，保证向量空间一致）。"""
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    from .runtime import detect_device

    return HuggingFaceEmbedding(
        model_name=MODEL_ID,
        device=detect_device(),
        normalize=True,
    )
