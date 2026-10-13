# -*- coding: utf-8 -*-
"""存储配置（12-factor：配置外置到环境变量）。零额外依赖。

环境变量：
  REFACTOR_STORAGE  file | postgres   （默认 file）
  REFACTOR_RAW_DIR   file 后端产物目录 （默认 data/raw）
  REFACTOR_DB_URL    postgres 连接串，如 postgresql+psycopg://user:pwd@host:5432/db
"""
import os
from dataclasses import dataclass


@dataclass
class StorageSettings:
    backend: str = "file"
    raw_dir: str = "data/raw"
    db_url: str | None = None

    @classmethod
    def load(cls) -> "StorageSettings":
        return cls(
            backend=(os.environ.get("REFACTOR_STORAGE") or "file").strip().lower(),
            raw_dir=(os.environ.get("REFACTOR_RAW_DIR") or "data/raw").strip(),
            db_url=(os.environ.get("REFACTOR_DB_URL") or None),
        )

    def is_postgres(self) -> bool:
        return self.backend == "postgres"
