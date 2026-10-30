# -*- coding: utf-8 -*-
"""refactor.rag.connector — DB(clauses) → RAG 增量同步 stage。

定位：与 clause/pipeline.build_clause_index 同属 storage-native 编排管线，是
clean → clause → docgraph 之后的「检索接入」阶段。不是独立 client 脚本，由统一
CLI 编排入口 `python -m refactor rag` 调用（见 refactor/__main__.py）。

职责：
  - 读取 clauses 表中 run_id 大于水位(last_run_id) 的新条款（覆盖全四源）；
  - 每条 clause 的 articles[] 逐条展开为一个 CanonicalChunk（条款级粒度）；
  - 经 rag.ingest 写入 pgvector(clause_chunks)，chunk_id 由 (key_md5, idx) 派生，
    LlamaIndex 按 doc_id upsert，幂等覆盖；
  - 增量水位记在 rag_ingest_state.last_run_id，仅 ingest 新增条款。

溯源：clause 产物刻意只保留 dedup_key/title/document_number/detail_url，
source/publish_date/issuing_authority/category 等溯源字段回表 join cleaned_items
（按 key_md5）取回，保证 RAG citation 字段完整。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List

import psycopg

from .canonical import CanonicalChunk
from .config import RagConfig
from .ingest import ingest

LOG = logging.getLogger(__name__)
_DEFAULT_BATCH = 200

# clause 产物已带的身份字段；其余溯源字段来自 cleaned_items 回表
_CLAUSE_META = ("title", "document_number", "detail_url")
_CLEANED_META = ("source", "publish_date", "issuing_authority", "category")


def _db_url() -> str:
    url = os.environ.get("REFACTOR_DB_URL")
    if not url:
        raise RuntimeError("REFACTOR_DB_URL 未设置，无法连接 clauses / cleaned_items")
    return url


def _ensure_state(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_ingest_state (
            id           INTEGER PRIMARY KEY DEFAULT 1,
            last_run_id  INTEGER NOT NULL DEFAULT 0,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        "INSERT INTO rag_ingest_state(id, last_run_id) VALUES (1, 0) "
        "ON CONFLICT (id) DO NOTHING"
    )


def _get_watermark(conn) -> int:
    _ensure_state(conn)
    row = conn.execute("SELECT last_run_id FROM rag_ingest_state WHERE id=1").fetchone()
    return int(row[0]) if row else 0


def _set_watermark(conn, run_id: int) -> None:
    conn.execute(
        "UPDATE rag_ingest_state SET last_run_id=%s, updated_at=now() WHERE id=1",
        (run_id,),
    )


def _fetch_new_clauses(conn, since_run_id: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT key_md5, run_id, payload FROM clauses "
        "WHERE run_id > %s ORDER BY run_id",
        (since_run_id,),
    )
    return cur.fetchall()


def _fetch_cleaned_meta(conn, key_md5s):
    if not key_md5s:
        return {}
    cur = conn.cursor()
    cur.execute(
        "SELECT key_md5, payload FROM cleaned_items WHERE key_md5 = ANY(%s)",
        (list(key_md5s),),
    )
    out: Dict[str, Dict[str, Any]] = {}
    for key_md5, payload in cur.fetchall():
        if isinstance(payload, dict):
            out[key_md5] = payload
    return out


def _build_chunks(clause_rows, cleaned_map):
    chunks: List[CanonicalChunk] = []
    new_clauses = 0
    max_run_id = 0
    for key_md5, run_id, payload in clause_rows:
        if not isinstance(payload, dict):
            continue
        articles = payload.get("articles") or []
        if not articles:
            continue
        new_clauses += 1
        if run_id > max_run_id:
            max_run_id = run_id
        cmeta = {k: (payload.get(k) or "") for k in _CLAUSE_META}
        cmeta["detail_url"] = payload.get("detail_url") or payload.get("source_url") or ""
        cln = cleaned_map.get(key_md5) or {}
        for k in _CLEANED_META:
            cmeta[k] = cln.get(k) or ""
        for idx, art in enumerate(articles):
            body = (art.get("body") or "").strip()
            if not body:
                continue
            meta = dict(cmeta)
            meta["article_no"] = art.get("number") or ""
            meta["chapter"] = art.get("chapter") or ""
            chunks.append(
                CanonicalChunk(
                    text=body,
                    meta=meta,
                    chunk_id=f"clause:{key_md5}:{idx}",
                )
            )
    return chunks, new_clauses, max_run_id


def build_rag_index(
    config: RagConfig | None = None,
    *,
    since_run_id: int | None = None,
    batch_size: int = _DEFAULT_BATCH,
) -> Dict[str, Any]:
    """clauses → RAG 增量同步（storage-native stage）。

    返回摘要 {new_clauses, chunks, batches, last_run_id, mode}。
    """
    config = config or RagConfig()
    conn = psycopg.connect(_db_url())
    try:
        watermark = since_run_id if since_run_id is not None else _get_watermark(conn)
        rows = _fetch_new_clauses(conn, watermark)
        key_md5s = {r[0] for r in rows if r[0]}
        cleaned_map = _fetch_cleaned_meta(conn, key_md5s)
        chunks, new_clauses, max_run_id = _build_chunks(rows, cleaned_map)
        if not chunks:
            LOG.info("[rag] 无新增条款（run_id > %d），跳过 ingest", watermark)
            return {
                "new_clauses": 0, "chunks": 0, "batches": 0,
                "last_run_id": watermark, "mode": "incremental",
            }
        total_batches = (len(chunks) + batch_size - 1) // batch_size
        LOG.info("[rag] 开始 ingest：chunks=%d batch_size=%d batches=%d", len(chunks), batch_size, total_batches)
        batches = 0
        done = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            ingest(batch, config)
            batches += 1
            done += len(batch)
            LOG.info("[rag] 进度 %d/%d chunks（batch %d/%d）", done, len(chunks), batches, total_batches)
        new_watermark = max_run_id if max_run_id else watermark
        _set_watermark(conn, new_watermark)
        LOG.info(
            "[rag] 增量同步完成：clauses=%d chunks=%d batches=%d last_run_id=%d",
            new_clauses, len(chunks), batches, new_watermark,
        )
        return {
            "new_clauses": new_clauses, "chunks": len(chunks),
            "batches": batches, "last_run_id": new_watermark, "mode": "incremental",
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="python -m refactor rag", description="clauses → RAG 增量同步 stage"
    )
    p.add_argument("--full", action="store_true",
                   help="从 run_id=0 全量重灌（仍按 chunk_id upsert，旧节点覆盖）")
    p.add_argument("--since-run-id", type=int, default=None, help="覆盖增量水位")
    p.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    p.add_argument("--db-url", default=None, help="覆盖 REFACTOR_DB_URL")
    args = p.parse_args(argv)
    if args.db_url:
        os.environ["REFACTOR_DB_URL"] = args.db_url
    since = 0 if args.full else args.since_run_id
    summary = build_rag_index(since_run_id=since, batch_size=args.batch_size)
    print("[rag] 同步摘要:", summary)
    return 0
