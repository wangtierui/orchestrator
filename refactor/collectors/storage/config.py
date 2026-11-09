# -*- coding: utf-8 -*-
"""存储配置（单一配置源 refactor/config.yaml + 环境变量覆盖，12-factor）。仅支持 Postgres 后端。

后端形态：Postgres 存元数据（raw_docs / raw_{ns} / cleaned_items / docgraph_edges 等），
正文等大对象落本地文件、由 content_ref 指针指向（即"PG + 文件指向"）。
file 后端已移除。

配置项（来自 refactor/config.yaml 的 database 段，环境变量可覆盖）：
  REFACTOR_STORAGE  postgres   （默认 postgres；仅支持 PG 后端）
  REFACTOR_RAW_DIR   正文落盘根目录 content_root（默认 data/raw）
  REFACTOR_DB_URL    postgres 连接串（SQLAlchemy 风格，psycopg 直连自动剥离 +psycopg）
"""
from dataclasses import dataclass

from config import settings


@dataclass
class StorageSettings:
    backend: str = "postgres"
    raw_dir: str = "data/raw"  # 作为 content_root：正文本地落盘根目录
    db_url: str | None = None

    @classmethod
    def load(cls) -> "StorageSettings":
        # 统一配置已合并环境变量覆盖（settings.database.* 已含 env 覆盖）
        return cls(
            backend=settings.database.storage_backend,
            raw_dir=settings.database.raw_dir,
            db_url=settings.database.url,
        )

    def is_postgres(self) -> bool:
        return self.backend == "postgres"
