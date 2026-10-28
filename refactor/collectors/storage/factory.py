# -*- coding: utf-8 -*-
"""存储后端工厂：仅 Postgres 后端（file 后端已移除）。

后端形态：Postgres 存元数据，正文等大对象落本地文件、由 content_ref 指针指向
（"PG + 文件指向"）。cleaned/clauses 走 SqlStore（JSON payload 表）；
采集源走 RawStore（raw_docs 主表 + raw_{ns} 副表，范式化）。
"""
from .base import RecordStore
from .config import StorageSettings


# 采集源走 RawStore（范式化主表+副表）；其余（cleaned/clauses）走 SqlStore（JSON payload）。
RAW_SOURCES = {"gov", "mof", "nfra", "pbc", "internal"}


def get_store(
    namespace: str,
    settings: StorageSettings | None = None,
    *,
    filename: str | None = None,
    normalizer=None,
    on_conflict: str = "nothing",
    content_root: str | None = None,
) -> RecordStore:
    """返回绑定到 namespace 的 Postgres 存储后端。

    仅支持 backend=postgres；未配置 REFACTOR_DB_URL 时显式报错。
    仅采集源（RAW_SOURCES）使用 RawStore + canon_raw 归一器；cleaned/clauses
    使用 SqlStore + canonicalize 规范记录（JSON payload），互不干扰。
    """
    settings = settings or StorageSettings.load()
    if not settings.is_postgres():
        raise ValueError(
            "仅支持 postgres 后端；请设置 REFACTOR_STORAGE=postgres 与 REFACTOR_DB_URL"
        )
    if not settings.db_url:
        raise ValueError("backend=postgres 但 REFACTOR_DB_URL 未设置")
    # content_root 缺省收敛到 raw_dir（即 data/raw），保证采集器写出正文、
    # 下游 clean/clause 读回时路径一致（PG 只存 content_ref 指针）。
    content_root = content_root or settings.raw_dir
    if namespace in RAW_SOURCES:
        from .raw_store import RawStore
        return RawStore(
            namespace,
            settings.db_url,
            on_conflict=on_conflict,
            content_root=content_root,
        )
    from .sql_backend import SqlStore

    return SqlStore(
        namespace,
        settings.db_url,
        normalizer=normalizer,
        on_conflict=on_conflict,
    )


def open_store(
    namespace: str,
    out_dir: str | None = None,
    *,
    filename: str | None = None,
    normalizer=None,
    on_conflict: str = "nothing",
) -> RecordStore:
    """构造 Postgres 存储后端；out_dir 用作正文落盘的 content_root 锚点。

    - out_dir 提供时：content_root 收口到 out_dir（保持 --out 与采集器一致）；
    - 否则退回 StorageSettings.load() 默认 raw_dir（作 content_root）。
    """
    s = StorageSettings.load()
    if not s.is_postgres():
        raise ValueError(
            "仅支持 postgres 后端；请设置 REFACTOR_STORAGE=postgres 与 REFACTOR_DB_URL"
        )
    # 正文落本地文件的锚定目录：优先用本次运行目录 out_dir，否则退回默认 raw_dir。
    content_root = out_dir or s.raw_dir
    return get_store(
        namespace,
        s,
        filename=filename,
        normalizer=normalizer,
        on_conflict=on_conflict,
        content_root=content_root,
    )
