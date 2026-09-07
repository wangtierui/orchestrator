# -*- coding: utf-8 -*-
"""
gates/gate_hardcoded_snapshots — 硬编码 cleaned 快照日期 lint（N-3 / 旧 lint_hardcoded_snapshots 迁移）

P1 实装：扫描本仓 modules/* 与 std_lib 下全部 .py，发现形如
``{src}_cleaned_{YYYYMMDD}.csv|.jsonl`` 的**文件路径字面量**即 FAIL，
强制下游一律经 clean_index latest 动态取最新快照（杜绝重跑读旧快照回归）。

排除（既定隔离，勿动）：
  - backups/ data/ cache/ logs/ reports/（非代码或历史）；
  - 本文件自身（正则字符串即定义非使用）；
  - clean_index 包（其正则字符串本身含该模式，属定义）。
"""
from __future__ import annotations

import os
import re

PAT = re.compile(r"[A-Za-z]+_cleaned_\d{8}\.(?:csv|jsonl)", re.IGNORECASE)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_DIRS = {"backups", "data", "cache", "logs", "reports", "venv", ".git",
                "__pycache__", "clean_index"}
EXCLUDE_FILES = {"gate_hardcoded_snapshots.py", "clean_index.py"}


def lint_hardcoded(root: str = ROOT) -> list[tuple[str, int, str]]:
    findings = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn in EXCLUDE_FILES:
                continue
            fp = os.path.join(dirpath, fn)
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in PAT.finditer(text):
                line = text[: m.start()].count("\n") + 1
                findings.append((os.path.relpath(fp, root), line, m.group(0)))
    return findings


def run():
    findings = lint_hardcoded()
    return (not findings), {"count": len(findings),
                            "examples": [f"{f}:{l}: {t}" for f, l, t in findings[:10]]}
