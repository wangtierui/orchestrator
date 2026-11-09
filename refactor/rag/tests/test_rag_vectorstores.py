# -*- coding: utf-8 -*-
"""向量库适配器：维度固定；维度不符只报错，绝不静默删表。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag.adapters import vectorstores
from rag.adapters.vectorstores import (
    _node_table_name,
    assert_compatible,
    build_pgvector,
    get_vector_store,
)
from rag.embedding import EMBED_DIM

SPEC = {"name": "pgvector", "table_name": "clause_chunks", "host": "h"}


def _conn_with_dim(dim):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = (dim,)
    return conn, cur


def _executed_sql(cur):
    return [c.args[0] for c in cur.execute.call_args_list]


def test_node_table_name_has_data_prefix():
    assert _node_table_name(SPEC) == "data_clause_chunks"


def test_assert_compatible_passes_on_fixed_dim():
    conn, cur = _conn_with_dim(EMBED_DIM)
    with patch("rag.adapters.vectorstores.psycopg.connect", return_value=conn):
        assert_compatible(SPEC)
    assert not any("DROP" in s.upper() for s in _executed_sql(cur))
    conn.close.assert_called_once()


def test_assert_compatible_raises_and_never_drops_on_mismatch():
    conn, cur = _conn_with_dim(768)
    with patch("rag.adapters.vectorstores.psycopg.connect", return_value=conn):
        with pytest.raises(RuntimeError, match="不一致"):
            assert_compatible(SPEC)
    assert not any("DROP" in s.upper() for s in _executed_sql(cur))
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_assert_compatible_ignores_connect_failure():
    with patch("rag.adapters.vectorstores.psycopg.connect", side_effect=OSError("down")):
        assert_compatible(SPEC)


def test_build_pgvector_uses_fixed_dim():
    with patch("llama_index.vector_stores.postgres.PGVectorStore.from_params") as factory:
        build_pgvector(host="h")
    assert factory.call_args.kwargs["embed_dim"] == EMBED_DIM


def test_get_vector_store_strips_name():
    # _FACTORIES 在模块定义时已绑定函数对象，必须替换字典本身而非模块属性
    build = MagicMock()
    with patch.object(vectorstores, "_FACTORIES", {"pgvector": build}):
        get_vector_store(SPEC)
    assert "name" not in build.call_args.kwargs
    assert build.call_args.kwargs["table_name"] == "clause_chunks"


def test_get_vector_store_rejects_unknown_backend():
    with pytest.raises(KeyError, match="未知 vector_store"):
        get_vector_store({"name": "milvus"})
