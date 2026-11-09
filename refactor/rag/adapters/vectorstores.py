# -*- coding: utf-8 -*-
"""vectorstores — 向量库适配器（pgvector，指向本机 PG）。

向量维度由 ``rag/embedding.py`` 的 ``EMBED_DIM`` 固定（BGE-M3 = 1024），
不再经 config 传递；维度不符时报错并要求显式 ``--full`` 重建，绝不静默删表。
"""
from __future__ import annotations

import psycopg
from psycopg import errors as pg_errors
from typing import Any, Callable, Dict, List

from ..embedding import EMBED_DIM


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


def assert_compatible(spec: Dict[str, Any]) -> None:
    """校验已有节点表的向量维度；不一致则报错，要求显式重建索引。

    不同模型的向量不可比，换模型必须重建。这里只报错、不删表 —— 重建是
    ``python -m refactorctl rag --full`` 的显式动作，不能由一次查询悄悄触发。
    """
    tbl = _node_table_name(spec)
    try:
        conn = psycopg.connect(vector_dsn(spec))
    except Exception:
        return  # 连不上时交给后续写入报错，此处不掩盖真实故障
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (a.attypmod - 4) AS dim FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = %s AND a.attname = 'embedding'",
                (tbl,),
            )
            row = cur.fetchone()
            if row and row[0] != EMBED_DIM:
                raise RuntimeError(
                    f"向量表 {tbl} 的维度是 {row[0]}，与 BGE-M3 的 {EMBED_DIM} 不一致；"
                    "请执行 python -m refactorctl rag --full 显式重建索引。"
                )
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
    **kwargs,
) -> Any:
    from llama_index.vector_stores.postgres import PGVectorStore

    return PGVectorStore.from_params(
        host=host, port=port, user=user, password=password,
        database=database, table_name=table_name, embed_dim=EMBED_DIM,
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
