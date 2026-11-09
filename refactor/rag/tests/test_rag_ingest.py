# -*- coding: utf-8 -*-
"""ingest 单元测试：全程 mock，不加载模型、不连 PG。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rag.canonical import CanonicalChunk
from rag.config import RagConfig
from rag.embedding import EMBED_DIM, MODEL_ID
from rag.ingest import ingest


def _chunks(n: int = 2):
    return [
        CanonicalChunk(
            text=f"第{i}条 正文",
            meta={"title": "办法", "article_no": f"第{i}条"},
            chunk_id=f"clause:{i}",
        )
        for i in range(n)
    ]


def _run(chunks, dim: int = EMBED_DIM) -> SimpleNamespace:
    embedder = MagicMock()
    embedder.get_text_embedding_batch.return_value = [[0.1] * dim for _ in chunks]
    store = MagicMock()
    order: list[str] = []
    with (
        patch("rag.ingest.get_embedder", return_value=embedder) as get_emb,
        patch("rag.ingest.get_vector_store", return_value=store) as get_store,
        patch("rag.ingest.assert_compatible") as check,
        patch(
            "rag.ingest.delete_nodes_by_id",
            side_effect=lambda *a, **k: order.append("delete"),
        ) as delete,
    ):
        store.add.side_effect = lambda *a, **k: order.append("add")
        nodes = ingest(chunks, RagConfig())
    return SimpleNamespace(
        nodes=nodes, embedder=embedder, store=store, get_emb=get_emb,
        get_store=get_store, check=check, delete=delete, order=order,
    )


def test_ingest_builds_nodes_with_fixed_metadata():
    r = _run(_chunks())
    assert len(r.nodes) == 2
    assert all(len(n.embedding) == EMBED_DIM for n in r.nodes)
    assert all(n.metadata["_embed_model"] == MODEL_ID for n in r.nodes)
    assert all(n.metadata["_embed_dim"] == EMBED_DIM for n in r.nodes)
    assert r.nodes[0].text == "第0条 正文"
    assert r.nodes[0].id_ == "clause:0"
    assert r.nodes[0].metadata["title"] == "办法"


def test_ingest_deletes_old_nodes_before_add():
    r = _run(_chunks())
    assert r.order == ["delete", "add"]
    r.delete.assert_called_once()
    assert r.delete.call_args.args[1] == ["clause:0", "clause:1"]
    r.check.assert_called_once()


def test_ingest_wrong_dimension_does_not_add_nodes():
    embedder = MagicMock()
    embedder.get_text_embedding_batch.return_value = [[0.1] * 768 for _ in range(2)]
    store = MagicMock()
    with (
        patch("rag.ingest.get_embedder", return_value=embedder),
        patch("rag.ingest.get_vector_store", return_value=store),
        patch("rag.ingest.assert_compatible"),
        patch("rag.ingest.delete_nodes_by_id") as delete,
        pytest.raises(RuntimeError, match="输出维度"),
    ):
        ingest(_chunks(), RagConfig())
    store.add.assert_not_called()
    delete.assert_not_called()


def test_ingest_empty_chunks_does_not_load_model():
    r = _run([])
    assert r.nodes == []
    r.get_emb.assert_not_called()
    r.check.assert_not_called()
    r.store.add.assert_not_called()


def test_ingest_vector_store_spec_has_no_dim_override():
    r = _run(_chunks())
    spec = r.get_store.call_args.args[0]
    assert "embed_dim" not in spec
