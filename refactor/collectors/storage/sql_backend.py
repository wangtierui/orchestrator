# -*- coding: utf-8 -*-
"""SqlStore：PostgreSQL 实现（SQLAlchemy Core + psycopg）。

仅在 backend=postgres 时由 factory 惰性导入，因此未安装 SQLAlchemy/psycopg 不影响 file 路径。

Schema（幂等建表 + 兼容迁移）：
  master_runs(id, namespace, captured_at, count)
  master_items(id, run_id, namespace, key_md5, url, payload JSONB)
  —— (namespace, key_md5) 唯一（key_md5 非空时，部分唯一索引允许多个 NULL）；
     key_md5 由 detail_url/url/detail_link/api_link 归一计算，缺省按源自然主键去重。
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    inspect,
    JSON,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .base import StoredRecord

UTC = timezone.utc

_metadata = MetaData()
RUNS = Table(
    "master_runs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("namespace", String(64), nullable=False, index=True),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)
ITEMS = Table(
    "master_items",
    _metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", Integer, ForeignKey("master_runs.id"), nullable=False, index=True),
    Column("namespace", String(64), nullable=False, index=True),
    Column("key_md5", String(32), nullable=True, index=True),
    Column("url", Text, nullable=True),
    Column("payload", JSON, nullable=False),
)


def _extract_key(item, keys=("detail_url", "url", "detail_link", "api_link")):
    if isinstance(item, dict):
        for k in keys:
            v = item.get(k)
            if v:
                return str(v)
    return None


def _key_md5(key: str | None) -> str | None:
    return hashlib.md5(key.encode("utf-8")).hexdigest() if key else None


class SqlStore:
    def __init__(
        self,
        namespace: str,
        db_url: str,
        normalizer=None,
        on_conflict: str = "nothing",
    ):
        self.namespace = namespace
        self.db_url = db_url
        self._engine = None
        self._normalizer = normalizer
        self._on_conflict = on_conflict

    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_engine(self.db_url, future=True)
        return self._engine

    def init_schema(self) -> None:
        """幂等建表 + 兼容已有库（旧 schema 列名 source→namespace、补 key_md5/created_at）。"""
        _metadata.create_all(self.engine)
        insp = inspect(self.engine)
        item_cols = [c["name"] for c in insp.get_columns("master_items")]
        run_cols = [c["name"] for c in insp.get_columns("master_runs")]
        with self.engine.begin() as conn:
            # 兼容旧 schema：列名 source -> namespace
            if "source" in item_cols and "namespace" not in item_cols:
                conn.execute(
                    text("ALTER TABLE master_items RENAME COLUMN source TO namespace")
                )
                conn.execute(
                    text("ALTER TABLE master_runs RENAME COLUMN source TO namespace")
                )
                item_cols = [c["name"] for c in insp.get_columns("master_items")]
                run_cols = [c["name"] for c in insp.get_columns("master_runs")]
            if "key_md5" not in item_cols:
                conn.execute(
                    text("ALTER TABLE master_items ADD COLUMN key_md5 TEXT")
                )
            if "created_at" not in run_cols:
                conn.execute(
                    text(
                        "ALTER TABLE master_runs ADD COLUMN created_at TIMESTAMPTZ DEFAULT now()"
                    )
                )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_master_ns_key "
                    "ON master_items (namespace, key_md5)"
                )
            )

    def save(
        self,
        items,
        captured_at=None,
        extra=None,
        *,
        normalizer=None,
        on_conflict=None,
    ) -> str:
        norm = normalizer or self._normalizer
        if norm:
            items = [norm(self.namespace, it) for it in items]
        captured_at = captured_at or datetime.now(UTC)
        oc = on_conflict or self._on_conflict

        rows = []
        for it in items:
            key = _extract_key(it)
            rows.append(
                {
                    "namespace": self.namespace,
                    "key_md5": _key_md5(key),
                    "url": key,
                    "payload": it,
                }
            )

        with self.engine.begin() as conn:
            run_id = conn.execute(
                insert(RUNS).values(
                    namespace=self.namespace,
                    captured_at=captured_at,
                    count=len(items),
                )
            ).inserted_primary_key[0]
            if rows:
                for r in rows:
                    r["run_id"] = run_id
                stmt = pg_insert(ITEMS).values(rows)
                if oc == "update":
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["namespace", "key_md5"],
                        set_={
                            "payload": stmt.excluded.payload,
                            "url": stmt.excluded.url,
                            "run_id": stmt.excluded.run_id,
                        },
                    )
                else:  # nothing（写一次）
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["namespace", "key_md5"]
                    )
                conn.execute(stmt)
        return str(run_id)

    save_master = save

    def load(self, version: str = "latest") -> StoredRecord:
        with self.engine.connect() as conn:
            if version == "latest":
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
            else:
                run = (
                    conn.execute(
                        select(RUNS).where(RUNS.c.id == int(version))
                    )
                    .mappings()
                    .first()
                )
            captured_at = (
                run["captured_at"].isoformat()
                if run and run["captured_at"]
                else ""
            )
            # 返回该 namespace 当前全量（跨 run 并集）：写一次下各 run 仅含新增，
            # 必须并集才能给出完整语料；下游 clean 依赖完整集。
            payloads = (
                conn.execute(
                    select(ITEMS.c.payload).where(
                        ITEMS.c.namespace == self.namespace
                    )
                )
                .scalars()
                .all()
            )
            items = [p for p in payloads if isinstance(p, dict)]
            return StoredRecord(
                namespace=self.namespace,
                captured_at=captured_at,
                count=len(items),
                items=items,
            )

    load_master = load
