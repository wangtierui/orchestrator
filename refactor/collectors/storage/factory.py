# -*- coding: utf-8 -*-
"""存储后端工厂：按配置选路（file / postgres），并支持规范化器与冲突策略。"""
from .base import RecordStore
from .config import StorageSettings
from .file_backend import FileStore


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
    """返回绑定到 namespace 的存储后端。

    settings 缺省时从环境变量加载（默认 FileStore，零风险保留现状）。
    backend=postgres 时惰性导入 SqlStore / RawStore（避免未装 SQLAlchemy 拖累 file 路径）。
    仅采集源（RAW_SOURCES）在 postgres 下使用 RawStore + canon_raw 归一器；cleaned/clauses
    继续使用 SqlStore + canonicalize 信封（JSON payload），互不干扰。
    """
    settings = settings or StorageSettings.load()
    if settings.is_postgres():
        if not settings.db_url:
            raise ValueError("backend=postgres 但 REFACTOR_DB_URL 未设置")
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
    return FileStore(
        namespace,
        settings.raw_dir,
        filename=filename,
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
    """构造存储后端（file/postgres 统一选路）。

    - file 模式：raw_dir 收口到 out_dir（保持 --out 与 test_smoke 兼容）；
    - postgres 模式：忽略 out_dir，走 REFACTOR_DB_URL；
    - out_dir 为 None 时退回 StorageSettings.load() 默认 raw_dir。
    """
    s = StorageSettings.load()
    if s.backend == "file" and out_dir:
        s = StorageSettings(backend="file", raw_dir=out_dir, db_url=s.db_url)
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
