# -*- coding: utf-8 -*-
"""retrieve + MCP 工具：只返回条款原文，不做生成。

全程 mock llama_index 与向量库，不加载模型、不联网、不连 PG。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from rag.config import RagConfig
from rag.query import retrieve


def _node() -> MagicMock:
    n = MagicMock()
    n.get_content.return_value = "第五十五条 商业银行应当建立健全风险管理体系。"
    n.score = 0.87321
    n.metadata = {
        "title": "商业银行风险管理指引",
        "article_no": "第五十五条",
        "source_url": "http://example.gov/1",
        "_embed_model": "BAAI/bge-m3",
        "_embed_dim": 1024,
    }
    return n


def _retrieve(top_k: int = 5):
    index = MagicMock()
    index.as_retriever.return_value.retrieve.return_value = [_node()]
    with (
        patch("rag.query.Settings", MagicMock()),
        patch("rag.query.get_embedder", return_value=MagicMock()) as get_emb,
        patch("rag.query.get_vector_store", return_value=MagicMock()) as get_store,
        patch("rag.query.VectorStoreIndex") as V,
    ):
        V.from_vector_store.return_value = index
        res = retrieve("风险控制", RagConfig(top_k=top_k))
    return res, index, get_emb, get_store


def test_retrieve_returns_chunks_with_original_text():
    res, *_ = _retrieve()
    assert set(res) == {"chunks"}
    c = res["chunks"][0]
    assert c["content"] == "第五十五条 商业银行应当建立健全风险管理体系。"
    assert c["title"] == "商业银行风险管理指引"
    assert c["article_no"] == "第五十五条"
    assert c["source_url"] == "http://example.gov/1"
    assert c["score"] == 0.8732


def test_retrieve_has_no_generated_answer():
    res, *_ = _retrieve()
    assert "answer" not in res
    assert "answer" not in res["chunks"][0]


def test_retrieve_hides_internal_markers():
    res, *_ = _retrieve()
    assert not any(k.startswith("_") for k in res["chunks"][0])


def test_retrieve_forwards_top_k():
    _, index, _, _ = _retrieve(top_k=7)
    index.as_retriever.assert_called_once_with(similarity_top_k=7)


def test_retrieve_reuses_cached_embedder():
    _, _, get_emb, _ = _retrieve()
    get_emb.assert_called_once_with()


def test_mcp_tool_returns_chunks_and_forwards_top_k():
    from mcp_server.tools.rag_query import rag_query

    expected = {"chunks": [{"content": "某条款正文"}]}
    with patch("rag.query.retrieve", return_value=expected) as fn:
        res = rag_query({"question": "风险控制要求", "top_k": 3})
    assert res == expected
    assert fn.call_args.args[0] == "风险控制要求"
    assert fn.call_args.args[1].top_k == 3
