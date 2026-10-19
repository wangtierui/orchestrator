# -*- coding: utf-8 -*-
"""llms — 生成模型适配器（本地 ollama / API openai）。"""
from __future__ import annotations

from typing import Any, Callable, Dict


def build_ollama(
    model: str = "qwen2.5:7b",
    base_url: str = "http://localhost:11434",
    **kwargs,
) -> Any:
    from llama_index.llms.ollama import Ollama

    return Ollama(model=model, base_url=base_url, **kwargs)


def build_openai(model: str = "gpt-4o-mini", api_key: str | None = None, **kwargs) -> Any:
    from llama_index.llms.openai import OpenAI

    return OpenAI(model=model, api_key=api_key, **kwargs)


def build_mock(**kwargs) -> Any:
    """纯本地占位 LLM，不依赖 ollama/API，仅用于验证 query 接线。"""
    from llama_index.core.llms.mock import MockLLM

    return MockLLM()


_FACTORIES: Dict[str, Callable[..., Any]] = {
    "ollama": build_ollama,
    "openai": build_openai,
    "mock": build_mock,
}


def get_llm(name: str = "ollama", **kwargs) -> Any:
    if name not in _FACTORIES:
        raise KeyError(f"未知 llm: {name!r}，可选: {sorted(_FACTORIES)}")
    return _FACTORIES[name](**kwargs)
