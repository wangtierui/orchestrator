# -*- coding: utf-8 -*-
"""refactor.rag.connector — DB(clauses) → RAG 增量同步 stage。

定位：与 clause/pipeline.build_clause_index 同属 storage-native 编排管线，是
clean → clause → docgraph 之后的「检索接入」阶段。不是独立 client 脚本，由统一
CLI 编排入口 `python -m refactor rag` 调用（见 refactor/__main__.py）。

条款级粒度：每条 clause 的 articles[] 逐条展开为一个 CanonicalChunk。
幂等键 chunk_id = clause:{key_md5}:{idx}:{hash(body)}：
  - 正文不变 → id 不变（增量跳过 / 覆盖，不重嵌）；
  - 正文变化 → id 变化（旧节点变孤儿被清、新节点注入）；
  - 新文档   → 新 key_md5 → 新 id（注入）。

增量策略（解耦 clause.run_id 的抖动）：
  clause 管线每次全量重抽并刷新所有行的 run_id，若按 run_id 判断会每次重嵌全量。
  因此这里以「当前 clauses 全量」为期望集，与「向量库已有 node_id 集」做差集：
    - 期望有、库里没有 → 注入（只嵌真正新增/变更，轻量）；
    - 库里有、期望没有（被改/被删正文）→ 删孤儿；
    - 两者都有（正文未变） → 跳过（不重嵌）。
  无论 clause 怎么重建，RAG 端都只处理 diff，且幂等无重复。
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from typing import Any, Dict, List

import psycopg
from psycopg import errors as pg_errors

from .adapters.vectorstores import (
    ensure_compatible,
    delete_nodes_by_id,
    reset_vector_table,
    _node_table_name,
    vector_dsn,
)
from .canonical import CanonicalChunk
from .config import RagConfig
from .ingest import ingest
try:
    from refactor.config import settings, psycopg_dsn
except ImportError:  # 兼容从 refactor/ 目录直接运行（collectors 上下文）
    from config import settings, psycopg_dsn

LOG = logging.getLogger(__name__)
_DEFAULT_BATCH = 200

# clause 产物已带的身份字段；其余溯源字段来自 cleaned_items 回表
_CLAUSE_META = ("title", "document_number", "detail_url")
_CLEANED_META = ("source", "publish_date", "issuing_authority", "category")


def _db_url() -> str:
    # CLI --db-url 会在运行时写入 os.environ；否则取统一配置
    url = os.environ.get("REFACTOR_DB_URL") or settings.database.url
    if not url:
        raise RuntimeError("database.url 未配置（检查 refactor/config.yaml 或 REFACTOR_DB_URL）")
    # SQLAlchemy 风格串（postgresql+psycopg://）转 psycopg 直连串
    return psycopg_dsn(url)


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
    conn.commit()


def _get_watermark(conn) -> int:
    _ensure_state(conn)
    row = conn.execute("SELECT last_run_id FROM rag_ingest_state WHERE id=1").fetchone()
    return int(row[0]) if row else 0


def _set_watermark(conn, run_id: int) -> None:
    _ensure_state(conn)
    conn.execute(
        "UPDATE rag_ingest_state SET last_run_id=%s, updated_at=now() WHERE id=1",
        (run_id,),
    )
    conn.commit()


def _fetch_all_clauses(conn):
    cur = conn.cursor()
    cur.execute("SELECT key_md5, run_id, payload FROM clauses ORDER BY run_id")
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
            # 正文哈希纳入幂等键：正文变 → id 变 → 重嵌；不变 → id 同 → 跳过/覆盖
            h = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
            chunks.append(
                CanonicalChunk(
                    text=body,
                    meta=meta,
                    chunk_id=f"clause:{key_md5}:{idx}:{h}",
                )
            )
    return chunks, new_clauses, max_run_id


def _existing_node_ids(vs_spec) -> set:
    """向量库里已有的 node_id 集合（增量差集用）。表不存在时返回空。"""
    tbl = _node_table_name(vs_spec)
    try:
        with psycopg.connect(vector_dsn(vs_spec)) as conn:
            rows = conn.execute(f'SELECT node_id FROM "{tbl}"').fetchall()
        return {r[0] for r in rows}
    except pg_errors.UndefinedTable:
        return set()


def _ingest_chunks(chunks, config, batch_size):
    total = len(chunks)
    if total == 0:
        return
    done = 0
    batches = 0
    total_batches = (total + batch_size - 1) // batch_size
    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        ingest(batch, config)
        batches += 1
        done += len(batch)
        LOG.info("[rag] 进度 %d/%d chunks（batch %d/%d）", done, total, batches, total_batches)


def build_rag_index(
    config: RagConfig | None = None,
    *,
    full: bool = False,
    batch_size: int = _DEFAULT_BATCH,
) -> Dict[str, Any]:
    """clauses → RAG 增量同步（reconcile 差集，幂等）。

    返回摘要 {ingested, deleted, clauses, last_run_id, mode}。
    """
    config = config or RagConfig()
    vs_spec = {**config.vector_store, "embed_dim": config.embed_dim}
    conn = psycopg.connect(_db_url())
    try:
        _ensure_state(conn)  # 始终建表，保证 --full 也能记录水位
        rows = _fetch_all_clauses(conn)
        key_md5s = {r[0] for r in rows if r[0]}
        cleaned_map = _fetch_cleaned_meta(conn, key_md5s)
        chunks, new_clauses, max_run_id = _build_chunks(rows, cleaned_map)
        if not chunks:
            LOG.info("[rag] 无条款，跳过")
            return {"ingested": 0, "deleted": 0, "mode": "empty"}

        if full:
            LOG.info("[rag] --full：清空向量表后全量重建（chunks=%d）", len(chunks))
            reset_vector_table(vs_spec)
            to_ingest = chunks
            to_delete: List[str] = []
        else:
            # 确保向量表存在再查已有 id
            ensure_compatible(vs_spec, config.embed_dim)
            existing = _existing_node_ids(vs_spec)
            expected = {c.chunk_id for c in chunks}
            to_ingest = [c for c in chunks if c.chunk_id not in existing]
            to_delete = [nid for nid in existing if nid not in expected]
            if to_delete:
                delete_nodes_by_id(vs_spec, to_delete)

        _ingest_chunks(to_ingest, config, batch_size)
        new_watermark = max_run_id if max_run_id else _get_watermark(conn)
        _set_watermark(conn, new_watermark)
        LOG.info(
            "[rag] 完成：ingest=%d delete=%d clauses=%d watermark=%d mode=%s",
            len(to_ingest), len(to_delete), new_clauses, new_watermark,
            "full" if full else "incremental",
        )
        return {
            "ingested": len(to_ingest), "deleted": len(to_delete),
            "clauses": new_clauses, "last_run_id": new_watermark,
            "mode": "full" if full else "incremental",
        }
    finally:
        conn.close()


def _smoke(config: RagConfig | None = None, limit_clauses: int = 2) -> Dict[str, Any]:
    """小冒烟：取前 N 条 clause，验证 TextNode 写入 pgvector + 重跑幂等。

    约 20-30s（含 bge_m3 离线加载 ~8s）。交给用户在自己终端执行。
    """
    config = config or RagConfig()
    conn = psycopg.connect(_db_url())
    try:
        rows = _fetch_all_clauses(conn)
        seen: set = set()
        small = []
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            small.append(r)
            if len(small) >= limit_clauses:
                break
        kms = {r[0] for r in small}
        cm = _fetch_cleaned_meta(conn, kms)
        chunks, _, _ = _build_chunks(small, cm)
    finally:
        conn.close()

    def cclause() -> int:
        tbl = _node_table_name({**config.vector_store, "embed_dim": config.embed_dim})
        c = psycopg.connect(_db_url())
        try:
            return c.execute(
                f"SELECT count(*) FROM \"{tbl}\" WHERE node_id LIKE 'clause:%'"
            ).fetchone()[0]
        finally:
            c.close()

    b = cclause()
    ingest(chunks, config)
    a = cclause()
    ingest(chunks, config)
    a2 = cclause()
    print(
        f"[smoke] chunks={len(chunks)} 示例chunk_id={chunks[0].chunk_id}\n"
        f"[smoke] 注入第1次 delta={a - b}（应≈{len(chunks)}）；"
        f"重跑后 delta={a2 - a}（应=0 幂等）",
        flush=True,
    )
    return {"chunks": len(chunks), "after_first": a - b, "after_second": a2 - a}


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
                   help="清空向量表后全量重建（换 chunk_id 格式/换模型时用）")
    p.add_argument("--smoke", action="store_true",
                   help="小冒烟：取前2条 clause 验证写入+幂等（约20-30s）")
    p.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    p.add_argument("--db-url", default=None, help="覆盖 REFACTOR_DB_URL")
    args = p.parse_args(argv)
    if args.db_url:
        os.environ["REFACTOR_DB_URL"] = args.db_url
    if args.smoke:
        summary = _smoke(limit_clauses=2)
        print("[rag] 冒烟摘要:", summary)
        return 0
    summary = build_rag_index(full=args.full, batch_size=args.batch_size)
    print("[rag] 同步摘要:", summary)
    return 0
