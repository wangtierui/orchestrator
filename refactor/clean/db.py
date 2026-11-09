# -*- coding: utf-8 -*-
"""clean 与 PG 的唯一数据边界：读 raw_docs 的物理列 + 对齐 cleaned_items。

契约来自 ``init/ddl.py`` 的表字段，本模块不定义任何业务字段结构：

  - 读：``raw_docs`` 公共列 + ``raw_{ns}`` 副表列 + ``content_ref`` 指向的本地正文。
    组装结果直接以 DDL 列名为主，不再还原采集器的历史字段别名（那是采集侧
    增量 dedup 的需要，与 clean 无关）。
  - 对齐：删除 ``cleaned_items`` 中「该源已不在本批」的行，落实 clean ≡ 采集侧。

不经过 ``collectors.storage`` 的 RawStore.load()：那条路径的 ``_reconstruct()``
会把记录还原成采集器旧形状（full_text / content / extra 展开 / id 改名），
clean 因此需要一份「源 → 字段名」方言表才能读值。按 DDL 列名直接读，
字段契约就只剩 PG 表字段一处。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable

from sqlalchemy import bindparam, create_engine, text, update as sa_update
from sqlalchemy.engine import Engine

from config import settings
from collectors.storage.raw_store import CHILD_TABLES

_ENGINE: Engine | None = None


def engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(settings.database.url, future=True)
    return _ENGINE


def _read_content(content_ref: str | None) -> str | None:
    """按 content_ref 从本地文件读回正文（PG 只存指针）。"""
    if not content_ref:
        return None
    path = os.path.join(settings.database.raw_dir, content_ref)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("content_text")
    except Exception:  # noqa: BLE001 - 正文文件损坏按「无正文」处理，不阻断整批
        return None


# 副表 id 列 → 采集记录里的业务主键（adapter 的 index_spec 回退会读）
_ID_RENAME = {"mof": ("law_id", "id"), "nfra": ("src_doc_id", "doc_id")}


def load_raw(source: str) -> list[dict[str, Any]]:
    """按 DDL 列名读取该源全量记录（跨 run 全量，与 clean 的输入契约一致）。"""
    child = f"raw_{source}"
    stmt = text(
        f"SELECT d.*, c.* FROM raw_docs d "
        f"JOIN {child} c ON c.doc_id = d.id "
        f"WHERE d.namespace = :ns"
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt, {"ns": source}).mappings().all()

    out: list[dict[str, Any]] = []
    for row in rows:
        rec = {k: v for k, v in row.items() if v is not None}
        content = _read_content(rec.pop("content_ref", None))
        if content is not None:
            rec["content_text"] = content
        extra = rec.pop("extra", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                rec.setdefault(k, v)
        rename = _ID_RENAME.get(source)
        if rename:
            src, dst = rename
            if src in rec:
                rec[dst] = rec.pop(src)
        out.append(rec)
    return out


def keys_of(records: Iterable[dict]) -> list[str]:
    """本批记录的 key_md5：与 SqlStore._extract_key / build_dedup_key 同一派生。"""
    return [
        hashlib.md5(str(r.get("detail_url") or "").encode("utf-8")).hexdigest()
        for r in records
        if r.get("detail_url")
    ]


def prune_cleaned(engine_: Engine | None, source: str, keep_keys: Iterable[str]) -> int:
    """删除 cleaned_items 里「该源已不在本批」的行，落实 clean ≡ 采集侧。

    ``namespace`` 恒为 ``cleaned``，四源混在同一张表，因此必须按
    ``source`` 列限定源，避免删掉其它源尚未清洗的数据。
    """
    keys = sorted(set(keep_keys))
    base = (
        "DELETE FROM cleaned_items "
        "WHERE namespace = :ns AND source = :src"
    )
    if keys:
        stmt = text(base + " AND COALESCE(key_md5, '') NOT IN :keys").bindparams(
            bindparam("keys", expanding=True)
        )
    else:
        stmt = text(base)
    params: dict[str, Any] = {"ns": "cleaned", "src": source}
    if keys:
        params["keys"] = keys
    with (engine_ or engine()).begin() as conn:
        return conn.execute(stmt, params).rowcount


def write_attachments(source: str, doc_id: int, attachments: list) -> None:
    """回写 ``raw_{source}.attachments``（附件解析结果）。

    由 clean 的 AttachmentExtractStage 调用：文档理解归 clean，解析结果落回
    raw 副表，供下游（RAG/clauses/cleaned 导出）使用。
    """
    tbl = CHILD_TABLES[source]
    with engine().begin() as conn:
        conn.execute(
            sa_update(tbl).where(tbl.c.doc_id == doc_id).values(attachments=attachments)
        )
