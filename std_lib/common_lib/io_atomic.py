# -*- coding: utf-8 -*-
"""
std_lib.common_lib.io_atomic — 原子写/指纹/审计薄组合层（P2 收口）

设计（消除专项三指出的 3-4 处重复实现）：
  - 原子写事实源 = fs_lock.atomic_write_text/json/csv_dict（本模块仅 re-export，不重复实现）；
  - sha256 指纹：收口 rfn/registry.fingerprint、scraper_std 旧实现、mof _atomic 场景；
  - audit：写操作审计追加（audit_log.jsonl），供 register/发布等关键写留痕。
"""
from __future__ import annotations

import hashlib
import json
import os

from std_lib.common_lib.fs_lock import (  # noqa: F401
    atomic_write_csv_dict,
    atomic_write_json,
    atomic_write_text,
)

__all__ = ["atomic_write_text", "atomic_write_json", "atomic_write_csv_dict",
           "sha256_file", "sha256_bytes", "audit_append"]


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_append(audit_path: str, entry: dict) -> None:
    """追加审计行（jsonl）。实现 = 读全量 + 追加一行 + 原子写（fs_lock 事实源）。"""
    os.makedirs(os.path.dirname(os.path.abspath(audit_path)), exist_ok=True)
    lines = []
    if os.path.exists(audit_path):
        with open(audit_path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    lines.append(json.dumps(entry, ensure_ascii=False))
    atomic_write_text(audit_path, "\n".join(lines) + "\n")
