# -*- coding: utf-8 -*-
"""存储配置（12-factor：配置外置到环境变量）。仅支持 Postgres 后端。

后端形态：Postgres 存元数据（raw_docs / raw_{ns} / cleaned_items / docgraph_edges 等），
正文等大对象落本地文件、由 content_ref 指针指向（即"PG + 文件指向"）。
file 后端已移除。

环境变量：
  REFACTOR_STORAGE  postgres   （默认 postgres；仅支持 PG 后端）
  REFACTOR_RAW_DIR   正文落盘根目录 content_root（默认 data/raw）
  REFACTOR_DB_URL    postgres 连接串，如 postgresql+psycopg://user:pwd@host:5432/db
"""
import os
from dataclasses import dataclass


@dataclass
class StorageSettings:
    backend: str = "postgres"
    raw_dir: str = "data/raw"  # 作为 content_root：正文本地落盘根目录
    db_url: str | None = None

    @classmethod
    def load(cls) -> "StorageSettings":
        return cls(
            backend=(os.environ.get("REFACTOR_STORAGE") or "postgres").strip().lower(),
            raw_dir=(os.environ.get("REFACTOR_RAW_DIR") or "data/raw").strip(),
            db_url=(os.environ.get("REFACTOR_DB_URL") or None),
        )

    def is_postgres(self) -> bool:
        return self.backend == "postgres"
