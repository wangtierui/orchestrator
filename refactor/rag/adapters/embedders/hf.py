# -*- coding: utf-8 -*-
"""本地 HuggingFace embedding —— 覆盖 bge 系列 / bce / gte 等。

关键：这些模型**只是 model_name 不同的同一类后端**，共用此工厂，
不必每人一个文件。device 默认 "auto"（Apple Silicon 走 mps，否则 cpu）。
"""
from __future__ import annotations

from ...runtime import detect_device
from .base import Embedder


def build_hf(
    model_name: str = "BAAI/bge-m3",
    device: str | None = None,
    normalize: bool = True,
    **kwargs,
) -> Embedder:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    # 不显式指定时，按运行平台自动选设备：Mac(mps) / 其它(cpu)
    if device is None:
        device = detect_device()

    return HuggingFaceEmbedding(
        model_name=model_name,
        device=device,
        normalize=normalize,
        **kwargs,
    )
