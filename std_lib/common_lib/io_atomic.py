# -*- coding: utf-8 -*-
"""
std_lib.common_lib.io_atomic — 原子写/指纹/审计共享件（专项三收口）

用途：
  - 原子写：文本/json/csv（委托 fs_lock.atomic_write_* 保持单实现）
  - sha256 指纹：文件或字符串（收口 rfn/registry.fingerprint、scraper_std、mof _atomic 场景）
  - audit：写操作审计追加（audit_log.jsonl），供 register/发布等关键写留痕
"""
from __future__ import annotations

import hashlib
import json
import os

from std_lib.common_lib.fs_lock import atomic_write_csv_dict, atomic_write_json, atomic_write_text

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
    """追加审计行（jsonl，原子写到 .tmp 后 replace 的简化版；供 register/发布调用）。"""
    os.makedirs(os.path.dirname(os.path.abspath(audit_path)), exist_ok=True)
    lines = []
    if os.path.exists(audit_path):
        with open(audit_path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    lines.append(json.dumps(entry, ensure_ascii=False))
    tmp = audit_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.replace(tmp, audit_path)
    except OSError:
        with open(audit_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
