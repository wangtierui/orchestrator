# -*- coding: utf-8 -*-
"""
gates/gate_no_duplicate_libs — 重复工具/重名再定义扫描（专项三 / R20）

P1 实装。规则：
  1) 全仓 .py（排除 backups/data/cache/logs/reports/std_lib 共享库自身除外？——不，
     共享库是事实源，不判其"重复"）中，若下列"应唯一实现"符号出现于 **非共享库文件**，
     即视为重复实现候选 → FAIL：
        norm_docno / norm_title / _atomic_write / _save_csv_rows / _lock(msvcrt) / fingerprint
    共享库允许定义一次（std_lib/scraper_std、std_lib/common_lib 为事实源）。
  2) 非共享库文件中出现 sys.path.insert 且带盘符 → FAIL（与 gate_hardcoded_paths 联动，
     此处专防 modules/* 迁移时遗留跨仓插入）。
  3) 重名异义再定义（同名函数两处语义不同，如 supp 旧 downloader 内联 sniff_kind）
     在 modules 迁入后按同名函数多文件计数提示。

P0→P1 语义切换：本 gate 自 P1 起 require_impl=True（见 gates/__init__.py ALL_GATES）。
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_DIRS = {"backups", "data", "cache", "logs", "venv", ".git", "__pycache__", "reports"}
# 本文件自身含 msvcrt.locking 字面量（检测正则定义，非使用）
EXCLUDE_FILES = {"gate_no_duplicate_libs.py"}
# 共享库为事实源，允许其定义规范符号（只允许一次）
SHARED_PREFIXES = (os.path.join("std_lib", "common_lib"), os.path.join("std_lib", "scraper_std"))
# 必须单点实现于共享库的符号（在各文件出现 def <sym> 即计数）
SINGLE_IMPL_SYMBOLS = ("norm_docno", "norm_title", "atomic_write_text", "atomic_write_json",
                       "atomic_write_csv_dict", "sha256_file", "fingerprint", "_atomic_write")
# 盘符 sys.path 插入
PAT_SYSPATH_DRIVE = re.compile(r"sys\.path\.(?:insert|append)\(\s*[0-9]*\s*,\s*[\"'][A-Za-z]:")
# msvcrt 自锁（应统一走 common_lib.fs_lock）
PAT_MSVCRT = re.compile(r"\bmsvcrt\.locking")


def _walk_py():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn in EXCLUDE_FILES:
                continue
            yield os.path.join(dirpath, fn)


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT)


def _is_shared(rel: str) -> bool:
    return rel.startswith(SHARED_PREFIXES)


def run():
    problems = []
    impl_hits: dict[str, list[str]] = {s: [] for s in SINGLE_IMPL_SYMBOLS}
    for fp in _walk_py():
        rel = _rel(fp)
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for sym in SINGLE_IMPL_SYMBOLS:
            # 精确匹配 "def sym("
            for m in re.finditer(rf"^def\s+{re.escape(sym)}\s*\(", text, re.M):
                # 非共享库出现即违规候选
                if not _is_shared(rel):
                    impl_hits[sym].append(f"{rel}:{text[:m.start()].count(chr(10))+1}")
        if not _is_shared(rel):
            if PAT_SYSPATH_DRIVE.search(text):
                problems.append(f"{rel}: 含盘符 sys.path 插入（应经 paths/interfaces）")
            if PAT_MSVCRT.search(text):
                problems.append(f"{rel}: 直接使用 msvcrt.locking（应统一 common_lib.fs_lock）")
    for sym, locs in impl_hits.items():
        if locs:
            problems.append(f"符号 {sym} 在非共享库重复定义: {locs[:5]}")
    return (not problems), {"problems": problems, "checked_symbols": SINGLE_IMPL_SYMBOLS}
