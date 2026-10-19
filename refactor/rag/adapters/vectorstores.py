# -*- coding: utf-8 -*-
"""vectorstores — 向量库适配器（当前实现 pgvector，指向 pglayers PG）。

换向量库 = 在 registry 加一个后端（如 milvus/chroma），其余不动。
embed_dim 必须和 embedder 输出维度一致，由 config 传入。
"""
from __future__ import annotations

import psycopg
from typing import Any, Callable, Dict


def _node_table_name(spec: Dict[str, Any]) -> str:
    """LlamaIndex 的 PGVectorStore 实际节点表名带 data_ 前缀。"""
    return "data_" + spec.get("table_name", "clause_chunks")


def ensure_compatible(spec: Dict[str, Any], embed_dim: int) -> None:
    """维度校验：若已存在节点表的向量维度与当前 embed_dim 不一致，则删表强制重索引。

    不同 embedding 模型的向量不可比，换模型必须重建索引，这里把该约束自动化。
    """
    tbl = _node_table_name(spec)
    dsn = "postgres://{user}:{password}@{host}:{port}/{database}".format(
        user=spec.get("user", "postgres"),
        password=spec.get("password", ""),
        host=spec.get("host", "localhost"),
        port=spec.get("port", 5432),
        database=spec.get("database", "postgres"),
    )
    try:
        conn = psycopg.connect(dsn)
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (a.atttypmod - 4) AS dim FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = %s AND a.attname = 'embedding'",
                (tbl,),
            )
            row = cur.fetchone()
            if row and row[0] != embed_dim:
                cur.execute(f'DROP TABLE IF EXISTS "{tbl}"')
                conn.commit()
    finally:
        conn.close()


def build_pgvector(
    host: str = "localhost",
    port: int = 5432,
    user: str = "root",
    password: str = "5462",
    database: str = "test",
    table_name: str = "clause_chunks",
    embed_dim: int = 1024,
    **kwargs,
) -> Any:
    from llama_index.vector_stores.postgres import PGVectorStore

    return PGVectorStore.from_params(
        host=host, port=port, user=user, password=password,
        database=database, table_name=table_name, embed_dim=embed_dim,
        **kwargs,
    )


_FACTORIES: Dict[str, Callable[..., Any]] = {
    "pgvector": build_pgvector,
}


def get_vector_store(spec: Dict[str, Any]) -> Any:
    name = spec.get("name", "pgvector")
    if name not in _FACTORIES:
        raise KeyError(f"未知 vector_store: {name!r}，可选: {sorted(_FACTORIES)}")
    kwargs = {k: v for k, v in spec.items() if k != "name"}
    return _FACTORIES[name](**kwargs)
