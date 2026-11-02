# -*- coding: utf-8 -*-
"""vectorstores — 向量库适配器（当前实现 pgvector，指向 pglayers PG）。

换向量库 = 在 registry 加一个后端（如 milvus/chroma），其余不动。
embed_dim 必须和 embedder 输出维度一致，由 config 传入。
"""
from __future__ import annotations

import psycopg
from psycopg import errors as pg_errors
from typing import Any, Callable, Dict, List


def _node_table_name(spec: Dict[str, Any]) -> str:
    """LlamaIndex 的 PGVectorStore 实际节点表名带 data_ 前缀。"""
    return "data_" + spec.get("table_name", "clause_chunks")


def vector_dsn(spec: Dict[str, Any]) -> str:
    return "postgres://{user}:{password}@{host}:{port}/{database}".format(
        user=spec.get("user", "postgres"),
        password=spec.get("password", ""),
        host=spec.get("host", "localhost"),
        port=spec.get("port", 5432),
        database=spec.get("database", "postgres"),
    )


def ensure_compatible(spec: Dict[str, Any], embed_dim: int) -> None:
    """维度校验：若已存在节点表的向量维度与当前 embed_dim 不一致，则删表强制重索引。

    不同 embedding 模型的向量不可比，换模型必须重建索引，这里把该约束自动化。
    """
    tbl = _node_table_name(spec)
    try:
        conn = psycopg.connect(vector_dsn(spec))
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (a.attypmod - 4) AS dim FROM pg_attribute a "
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


def delete_nodes_by_id(spec: Dict[str, Any], node_ids: List[str]) -> None:
    """按 node_id 批量删除（幂等 upsert 的前置清理 / 清理孤儿节点）。"""
    if not node_ids:
        return
    tbl = _node_table_name(spec)
    try:
        conn = psycopg.connect(vector_dsn(spec))
    except Exception:
        return
    try:
        conn.execute(f'DELETE FROM "{tbl}" WHERE node_id = ANY(%s)', (list(node_ids),))
        conn.commit()
    except pg_errors.UndefinedTable:
        # 表刚被 reset_vector_table 删掉时，删除是空操作，忽略
        conn.rollback()
    finally:
        conn.close()


def reset_vector_table(spec: Dict[str, Any]) -> None:
    """清空节点表（--full 全量重建用；PGVectorStore 下次 add 会自动重建）。

    注意：删的是 data_{table_name} 节点表，不会动 clauses / cleaned_items。
    """
    tbl = _node_table_name(spec)
    try:
        conn = psycopg.connect(vector_dsn(spec))
    except Exception:
        return
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{tbl}"')
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
