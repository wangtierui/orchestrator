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
# 构建产物目录亦排除（2026-09-13）：pip wheel / pip install . 会把整棵树复制到 build/lib，
# 若被扫描会报"符号重复定义"误报（实测）——这些目录 .gitignore 已忽略，非源码。
EXCLUDE_DIRS = {"backups", "data", "cache", "logs", "venv", ".venv", ".git", "__pycache__",
                "reports", "build", "dist", ".pytest_cache"}
# 本文件自身含 msvcrt.locking 字面量（检测正则定义，非使用）
EXCLUDE_FILES = {"gate_no_duplicate_libs.py"}
# 共享库为事实源，允许其定义规范符号（只允许一次）
SHARED_PREFIXES = (os.path.join("std_lib", "common_lib"), os.path.join("std_lib", "scraper_std"))
# 必须单点实现于共享库的符号（在各文件出现 def <sym> 即计数）。
# 注：不带下划线的公共原子写/指纹名须唯一；"_atomic_write" 等采集器本地私有实现不纳入
# （属模块内自包含封装，非跨仓规范符号；共享库公共名为 atomic_write_text 等）。
SINGLE_IMPL_SYMBOLS = ("norm_docno", "norm_title", "atomic_write_text", "atomic_write_json",
                       "atomic_write_csv_dict", "sha256_file", "fingerprint")
# 盘符 sys.path 插入
PAT_SYSPATH_DRIVE = re.compile(r"sys\.path\.(?:insert|append)\(\s*[0-9]*\s*,\s*[\"'][A-Za-z]:")
# msvcrt 自锁（应统一走 common_lib.fs_lock）
PAT_MSVCRT = re.compile(r"\bmsvcrt\.locking")
# A-10（2026-09-12）：私有副本扫描（_norm_docno/_norm_title）——原符号表仅查公开名 →
# 私有副本长期逃检。P3A 清零后：标准层副本一律 import std_lib/common_lib/norm（SSOT 分层）；
# 有意特化须加 `# norm-specialization: <理由>` 豁免标记——**未标注副本 = FAIL**（防增量）。
PAT_PRIVATE_NORM = re.compile(r"^def\s+(_norm_docno|_norm_title)\s*\(", re.M)
MARK_TOKEN = "# norm-specialization"


def _walk_py():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
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
    private_unmarked: dict[str, list[str]] = {}
    private_exempt: dict[str, list[str]] = {}
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
            # A-10 清零（2026-09-12）：私有副本须显式豁免标记；未标注 = 违规 FAIL（防增量）
            for m in PAT_PRIVATE_NORM.finditer(text):
                loc = f"{rel}:{text[:m.start()].count(chr(10))+1}"
                ctx = text[max(0, m.start() - 200):m.start()]
                if MARK_TOKEN in ctx:
                    private_exempt.setdefault(m.group(1), []).append(loc)
                else:
                    private_unmarked.setdefault(m.group(1), []).append(loc)
            if PAT_SYSPATH_DRIVE.search(text):
                problems.append(f"{rel}: 含盘符 sys.path 插入（应经 paths/interfaces）")
            if PAT_MSVCRT.search(text):
                problems.append(f"{rel}: 直接使用 msvcrt.locking（应统一 common_lib.fs_lock）")
    for sym, locs in impl_hits.items():
        if locs:
            problems.append(f"符号 {sym} 在非共享库重复定义: {locs[:5]}")
    for sym, locs in private_unmarked.items():
        if locs:
            problems.append(
                f"私有归一 {sym} 存在未豁免副本（应 import std_lib/common_lib/norm 或加 "
                f"`# norm-specialization: <理由>` 豁免标记）: {locs[:8]}")
    return (not problems), {
        "problems": problems, "checked_symbols": SINGLE_IMPL_SYMBOLS,
        "private_norm_exempt": {k: v[:12] for k, v in sorted(private_exempt.items())},
        "private_norm_unmarked": {k: v[:12] for k, v in sorted(private_unmarked.items())},
        "note": "SSOT 分层=std_lib/common_lib/norm（norm_docno / norm_title / norm_title_strict）；"
                "豁免副本须带 `# norm-specialization` 标记（清单见 private_norm_exempt）。",
    }
