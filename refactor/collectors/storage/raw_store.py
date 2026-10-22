# -*- coding: utf-8 -*-
"""RawStore：采集阶段范式化存储（主表 raw_docs + 各源副表 raw_{ns}）。

与 SqlStore（cleaned/clauses 的整行 JSON payload 表）并行存在；仅采集源走 RawStore，
落实"公共列显式（raw_docs）+ 各源私有列显式（raw_{ns}）+ 可选 JSON(extra) "的范式建模：

  raw_docs(id, run_id, namespace, key_md5, title, detail_url, publish_date,
           effective_date, issuing_authority, category, document_number, summary,
           fetch_status, captured_at)
  raw_{ns}(doc_id FK→raw_docs.id, 源特有列…, content_text, attachments JSONB, extra JSONB)

写入：canon_raw 把异构记录拆成 {common 归一字段} + {child 源私有字段}，事务内先写主表
      取 id 再写副表；on_conflict 按源（mof=update 覆盖，其余=nothing 写一次）。
读取：JOIN 主副表，重建为采集器期望的原始形状记录（公共字段 + 副表字段 + extra 兜底），
      保证增量 dedup / handle_known 读到与原记录一致的字段。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .base import StoredRecord
from ..lib.fetch_status import normalize as _norm_fetch_status

UTC = timezone.utc
RAW_METADATA = MetaData()

# ── 主表 / 运行账本（自带 meta 列承接 run 级治理 sidecar，如 nfra errors）──
RUNS = Table(
    "master_runs",
    RAW_METADATA,
    Column("id", Integer, primary_key=True),
    Column("namespace", String(64), nullable=False, index=True),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False),
    Column("meta", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)

RAW_DOCS = Table(
    "raw_docs",
    RAW_METADATA,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", Integer, ForeignKey("master_runs.id"), nullable=False, index=True),
    Column("namespace", String(64), nullable=False, index=True),
    Column("key_md5", String(32), nullable=False, index=True),
    Column("title", Text, nullable=True),
    Column("detail_url", Text, nullable=True),
    Column("publish_date", String(32), nullable=True),
    Column("effective_date", String(32), nullable=True),
    Column("issuing_authority", Text, nullable=True),
    Column("category", Text, nullable=True),
    Column("document_number", Text, nullable=True),
    Column("summary", Text, nullable=True),
    Column("fetch_status", String(32), nullable=False),
    Column("captured_at", DateTime(timezone=True), server_default=text("now()")),
    # 正文落本地文件后的指针（相对 content_root 的路径）；PG 只存元数据 + 指针。
    Column("content_ref", Text, nullable=True),
    UniqueConstraint("namespace", "key_md5", name="uq_raw_docs_ns_key"),
)

# ── 各源副表：doc_id 关联主表；content_text 统一正文（决策：正文放副表）──
RAW_MOF = Table(
    "raw_mof", RAW_METADATA,
    Column("doc_id", BigInteger, ForeignKey("raw_docs.id"), primary_key=True),
    Column("law_id", Text),
    Column("category_id", Text),
    Column("create_dept", Text),
    Column("file_count", Integer),
    Column("expire_date", String(32)),
    Column("abolish_date", String(32)),
    Column("status", Text),
    Column("change", Text),
    Column("active", Boolean),
    Column("api_link", Text),
    Column("attachments", JSON),
    Column("extra", JSON),
)

RAW_GOV = Table(
    "raw_gov", RAW_METADATA,
    Column("doc_id", BigInteger, ForeignKey("raw_docs.id"), primary_key=True),
    Column("page", Integer),
    Column("source_sub", Text),
    Column("attachment_text", Text),
    Column("attachments", JSON),
    Column("extra", JSON),
)

RAW_PBC = Table(
    "raw_pbc", RAW_METADATA,
    Column("doc_id", BigInteger, ForeignKey("raw_docs.id"), primary_key=True),
    Column("link_type", Text),
    Column("file_type", Text),
    Column("local_path", Text),
    Column("error", Text),
    Column("attachment_status", Text),
    Column("attachment_errors", JSON),
    Column("attachments", JSON),
    Column("extra", JSON),
)

RAW_NFRA = Table(
    "raw_nfra", RAW_METADATA,
    Column("doc_id", BigInteger, ForeignKey("raw_docs.id"), primary_key=True),
    Column("src_doc_id", Text),
    Column("build_date", Text),
    Column("index_no", Text),
    Column("doc_file_url", Text),
    Column("pdf_file_url", Text),
    Column("attachments", JSON),
    Column("extra", JSON),
)

RAW_INTERNAL = Table(
    "raw_internal", RAW_METADATA,
    Column("doc_id", BigInteger, ForeignKey("raw_docs.id"), primary_key=True),
    Column("attachments", JSON),
    Column("extra", JSON),
)

CHILD_TABLES = {
    "mof": RAW_MOF, "gov": RAW_GOV, "pbc": RAW_PBC,
    "nfra": RAW_NFRA, "internal": RAW_INTERNAL,
}

# 各副表"清晰"列（其余源原生字段回落 extra JSONB，保证 round-trip 保真）
CHILD_COLUMNS = {
    "mof": ["law_id", "category_id", "create_dept", "file_count", "expire_date",
            "abolish_date", "status", "change", "active", "api_link"],
    "gov": ["page", "source_sub", "attachment_text"],
    "pbc": ["link_type", "file_type", "local_path", "error",
            "attachment_status", "attachment_errors"],
    "nfra": ["src_doc_id", "build_date", "index_no", "doc_file_url", "pdf_file_url"],
    "internal": [],
}

# 副表列 → 源记录字段名 映射（默认同名；mof 的 law_id 来自记录的 id，
# nfra 的 src_doc_id 来自记录的 doc_id，使列名与源 key_of 期望键对应）。
CHILD_FIELD_MAP = {
    "mof": {"law_id": "id"},
    "nfra": {"src_doc_id": "doc_id"},
}

# 各源主正文在原始记录里的键名；统一列都是 content_text。正文只存一份于 content_text，
# 原别名（full_text/content）不进 extra；load 时据此从 content_text 还原别名，
# 保证重读记录与采集器原吐出形状一致（下游 map_gov 读 full_text、pbc/nfra 读 content 等）。
CONTENT_ALIAS = {
    "mof": None,
    "gov": "full_text",
    "pbc": "content",
    "nfra": "content",
    "internal": "content",
}
# 主正文键（统一列 + 各源别名）：拆/合时既不进 extra、也不重复存储。
_BODY_ALIASES = frozenset(
    {v for v in CONTENT_ALIAS.values() if v} | {"content_text", "attachments"}
)

# 副表 id 列 → 采集器 key_of 期望的记录键（load 时还原，保证增量判定一致）
CHILD_ID_RENAME = {"mof": ("law_id", "id"), "nfra": ("src_doc_id", "doc_id")}

RAW_COMMON_FIELDS = {
    "title", "detail_url", "publish_date", "effective_date",
    "issuing_authority", "category", "document_number", "summary", "fetch_status",
}


def _first(item: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


class RawStore:
    """采集源范式化存储：raw_docs（公共）+ raw_{ns}（源私有）双写。"""

    def __init__(self, namespace: str, db_url: str,
                 normalizer=None, on_conflict: str = "nothing",
                 content_root: str | None = None):
        self.namespace = namespace
        self.db_url = db_url
        self._engine = None
        self._normalizer = normalizer
        self._on_conflict = on_conflict
        self.content_root = content_root
        self._child = CHILD_TABLES[namespace]

    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_engine(self.db_url, future=True)
        return self._engine

    def init_schema(self) -> None:
        insp = inspect(self.engine)
        existing = set(insp.get_table_names())
        # 兼容迁移：旧库列名 doc_no → document_number（统一规范名）
        if "raw_docs" in existing:
            rd_cols = [c["name"] for c in insp.get_columns("raw_docs")]
            if "doc_no" in rd_cols and "document_number" not in rd_cols:
                with self.engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE raw_docs RENAME COLUMN doc_no TO document_number"))
        RAW_METADATA.create_all(self.engine)
        insp = inspect(self.engine)
        cols = [c["name"] for c in insp.get_columns("master_runs")]
        if "meta" not in cols:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE master_runs ADD COLUMN meta JSON"))
        # 保证 (namespace, key_md5) 唯一约束存在（覆盖模式 ON CONFLICT 依赖）；
        # 已存在的旧表 create_all 会跳过建约束，这里补 ALTER。
        uqs = [c["name"] for c in insp.get_unique_constraints("raw_docs")]
        if "uq_raw_docs_ns_key" not in uqs:
            with self.engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE raw_docs ADD CONSTRAINT uq_raw_docs_ns_key "
                    "UNIQUE (namespace, key_md5)"))

    def _split(self, item: dict, ns: str):
        if not isinstance(item, dict):
            return None
        content = _first(item, "content", "full_text", "content_text", "rich_text") or ""
        # 入参已被 canonicalize 规范化为统一字段名；此处仅按规范名取值，不再做源别名映射。
        common = {
            "title": item.get("title") or "",
            "detail_url": item.get("detail_url") or "",
            "publish_date": item.get("publish_date") or "",
            "effective_date": item.get("effective_date") or "",
            "issuing_authority": item.get("issuing_authority") or "",
            "category": item.get("category") or "",
            "document_number": item.get("document_number") or "",
            "summary": item.get("summary") or "",
            "fetch_status": _norm_fetch_status(
                item.get("fetch_status")
                or ("ok" if content else "no_content")),
        }
        explicit = {
            c: item.get(CHILD_FIELD_MAP.get(ns, {}).get(c, c))
            for c in CHILD_COLUMNS.get(ns, [])
        }
        extra = {
            k: v for k, v in item.items()
            if k not in RAW_COMMON_FIELDS
            and k not in explicit
            and k not in _BODY_ALIASES
        }
        child = {
            **explicit,
            "attachments": item.get("attachments"),
            "extra": extra or None,
        }
        return common, child, content

    def save(self, items: Iterable[dict], captured_at=None, extra: dict | None = None,
             *, normalizer=None, on_conflict: str | None = None) -> str:
        self.init_schema()
        captured_at = captured_at or datetime.now(UTC)
        oc = on_conflict or self._on_conflict
        ns = self.namespace
        child_t = self._child
        # 落库前统一套 canonicalize（单一映射源）；_split 之后只按规范字段名取值。
        norm = normalizer or self._normalizer
        if norm:
            items = [norm(ns, it) for it in items]
        parsed = [p for p in (self._split(it, ns) for it in items) if p is not None]

        with self.engine.begin() as conn:
            run_id = conn.execute(
                insert(RUNS).values(
                    namespace=ns, captured_at=captured_at,
                    count=len(parsed), meta=extra)
            ).inserted_primary_key[0]

            for common, child, content in parsed:
                if ns == "mof":
                    key = child.get("law_id") or common["detail_url"] or ns
                elif ns == "nfra":
                    key = child.get("src_doc_id") or common["detail_url"] or ns
                else:
                    key = common["detail_url"] or ns
                key_md5 = hashlib.md5(str(key).encode("utf-8")).hexdigest()

                # 写一次模式：已存在则整条（主+副）跳过，且正文文件也不写
                if oc == "nothing":
                    existing = conn.execute(
                        select(RAW_DOCS.c.id).where(
                            RAW_DOCS.c.namespace == ns,
                            RAW_DOCS.c.key_md5 == key_md5)
                    ).scalar_one_or_none()
                    if existing is not None:
                        continue
                # 正文落本地文件（锚定 content_root），PG 只存元数据 + 指针
                if self.content_root is None:
                    raise ValueError(
                        "RawStore.content_root 未设置：postgres 后端需锚定 out_dir "
                        "才能把正文落到本地文件")
                content_ref = f"content/{ns}/{key_md5}.json"
                content_path = os.path.join(self.content_root, content_ref)
                os.makedirs(os.path.dirname(content_path), exist_ok=True)
                with open(content_path, "w", encoding="utf-8") as fh:
                    json.dump({"content_text": content}, fh, ensure_ascii=False)
                common = {**common, "content_ref": content_ref}

                if oc == "nothing":
                    did = conn.execute(
                        insert(RAW_DOCS).values(
                            run_id=run_id, namespace=ns, key_md5=key_md5, **common)
                    ).inserted_primary_key[0]
                    conn.execute(insert(child_t).values(doc_id=did, **child))
                else:  # update：冲突覆盖（mof 变更追踪）
                    did = conn.execute(
                        pg_insert(RAW_DOCS).values(
                            run_id=run_id, namespace=ns, key_md5=key_md5, **common)
                        .on_conflict_do_update(
                            index_elements=["namespace", "key_md5"],
                            set_={**common, "run_id": run_id})
                        .returning(RAW_DOCS.c.id)
                    ).scalar_one()
                    conn.execute(
                        pg_insert(child_t).values(doc_id=did, **child)
                        .on_conflict_do_update(
                            index_elements=["doc_id"], set_=dict(child))
                    )
        return str(run_id)

    save_master = save

    def load(self, version: str = "latest") -> StoredRecord:
        # 先确保 schema 就绪：建表 + 给旧 master_runs 补 meta 列（幂等）。
        # 否则 build_context 阶段的 load_master 会在 save 之前就 SELECT meta 而报列不存在。
        self.init_schema()
        with self.engine.connect() as conn:
            run = (
                conn.execute(
                    select(RUNS)
                    .where(RUNS.c.namespace == self.namespace)
                    .order_by(RUNS.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            captured_at = (
                run["captured_at"].isoformat()
                if run and run["captured_at"] else ""
            )
            if version != "latest" and run and str(run["id"]) != str(version):
                run = (
                    conn.execute(
                        select(RUNS).where(RUNS.c.id == int(version))
                    )
                    .mappings()
                    .first()
                )
                captured_at = (
                    run["captured_at"].isoformat()
                    if run and run["captured_at"] else captured_at
                )
            stmt = (
                select(RAW_DOCS, self._child)
                .select_from(
                    RAW_DOCS.join(self._child,
                                  RAW_DOCS.c.id == self._child.c.doc_id)
                )
                .where(RAW_DOCS.c.namespace == self.namespace)
            )
            rows = conn.execute(stmt).mappings().all()
            items = [self._reconstruct(r) for r in rows]
            return StoredRecord(
                namespace=self.namespace,
                captured_at=captured_at,
                count=len(items),
                items=items,
            )

    load_master = load

    def _read_content(self, content_ref: str | None) -> str | None:
        """按 content_ref 从本地文件读回正文（PG 只存指针）。文件缺失则回退 None。"""
        if not content_ref or not self.content_root:
            return None
        p = os.path.join(self.content_root, content_ref)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh).get("content_text")
        except Exception:
            return None

    def _reconstruct(self, row: Dict[str, Any]) -> dict:
        rec: Dict[str, Any] = {}
        # 公共字段（含 content_ref 指针，仅透传，最终 pop）
        for k in ("title", "detail_url", "publish_date", "effective_date",
                  "issuing_authority", "category", "document_number", "summary",
                  "fetch_status", "content_ref"):
            if k in row and row[k] is not None:
                rec[k] = row[k]
        # 副表字段（attachments / extra；正文已移出副表）
        for k in ("attachments", "extra"):
            if k in row and row[k] is not None:
                rec[k] = row[k]
        # 源私有显式列
        for c in CHILD_COLUMNS.get(self.namespace, []):
            if c in row and row[c] is not None:
                rec[c] = row[c]
        # 正文：从本地文件读回（PG 只存元数据 + 指针）
        content_text = self._read_content(rec.get("content_ref"))
        if content_text is not None:
            rec["content_text"] = content_text
        # extra 兜底合并（还原 issue_organ / document_no / full_text 等源原生键）
        extra = rec.get("extra")
        if isinstance(extra, dict):
            for k, v in extra.items():
                rec.setdefault(k, v)
        # 还原 key_of 期望的 id 键
        rename = CHILD_ID_RENAME.get(self.namespace)
        if rename:
            src, dst = rename
            if rec.get(src) is not None:
                rec[dst] = rec.pop(src)
        # 还原主正文原始别名（gov=full_text / pbc=nfra=internal=content），保证重读记录
        # 形状与采集器原吐出一致；正文实体只存在于本地文件，PG 不重复存储。
        alias = CONTENT_ALIAS.get(self.namespace)
        if alias and alias not in rec and rec.get("content_text") is not None:
            rec[alias] = rec["content_text"]
        rec.pop("extra", None)
        rec.pop("content_ref", None)
        return rec
