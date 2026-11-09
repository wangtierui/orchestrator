# -*- coding: utf-8 -*-
"""BGE-M3 契约回归：模型定死、配置里不许再出现模型开关。

全部为纯单元用例，不加载模型、不联网、不连 PG。
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from config import settings
from rag.adapters import __all__ as ADAPTER_EXPORTS
from rag.canonical import CanonicalChunk
from rag.config import RagConfig
from rag.embedding import EMBED_DIM, MODEL_ID, get_embedder


def test_model_id_and_dim_are_fixed():
    assert MODEL_ID == "BAAI/bge-m3"
    assert EMBED_DIM == 1024


def test_rag_config_exposes_only_runtime_params():
    assert set(RagConfig.__dataclass_fields__) == {"vector_store", "top_k"}


def test_rag_settings_exposes_only_runtime_params():
    assert {f.name for f in fields(settings.rag)} == {"vector_store", "top_k"}


def test_config_yaml_has_no_model_switches():
    path = Path(__file__).resolve().parents[2] / "config.yaml"
    keys = {
        line.split(":", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if ":" in line and not line.lstrip().startswith("#")
    }
    for key in ("embedder_name", "embed_dim", "llm_name", "reranker_name"):
        assert key not in keys


def test_canonical_meta_uses_fixed_constants():
    meta = CanonicalChunk(text="正文", meta={"title": "标题"}).to_document_meta()
    assert meta["title"] == "标题"
    assert meta["_embed_model"] == MODEL_ID
    assert meta["_embed_dim"] == EMBED_DIM


def test_adapters_only_expose_vector_store():
    assert ADAPTER_EXPORTS == ["get_vector_store"]


def test_get_embedder_is_cached_and_uses_fixed_model():
    """同一进程内只构造一次，且始终用固定模型名（不真正加载权重）。"""
    get_embedder.cache_clear()
    try:
        with patch("llama_index.embeddings.huggingface.HuggingFaceEmbedding") as ctor:
            first = get_embedder()
            second = get_embedder()
            assert first is second
            assert ctor.call_count == 1
            assert ctor.call_args.kwargs["model_name"] == MODEL_ID
            assert ctor.call_args.kwargs["normalize"] is True
    finally:
        get_embedder.cache_clear()
