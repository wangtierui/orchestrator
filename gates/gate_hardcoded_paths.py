# -*- coding: utf-8 -*-
"""
gates/gate_hardcoded_paths — 盘符/跨仓 sys.path 硬编码扫描（R4 / 专项三收口）

扫描本仓全部 .py，发现以下任一模式即 FAIL：
  1) 盘符字面量：D:/ D:\\ d:/ d:\\ C:/ C:\\（作为代码中的路径/导入前缀）
  2) sys.path.insert / sys.path.append 指向旧仓绝对路径（跨仓导入行为）
  3) OCR 旧硬编码：C:\\Program Files\\Tesseract（R17）

排除：backups/、data/、cache/、logs/、reports/、venv/、本文件自身、paths.py。
说明注释/文档中的模块路径提及（如 modules/regulatory_classifier/rfn/registry.py）
不构成违规（非盘符、非 sys.path 场景），不报。

P0 对本仓骨架生效；P1 覆盖 std_lib 复制代码（旧仓脚本常见 sys.path.insert 需清理）。
"""
from __future__ import annotations

import os
import re

# 盘符字面量：排除 URL 协议（http://、https:// 等前有字母/冒号）与十六进制/带冒号符号场景。
# 负向前瞻 (?<![A-Za-z:]) 使 "p:/"（http）/"s:/"（https）不命中，而 "D:/"、'C:\\' 正常命中。
PAT_DRIVE = re.compile(r"(?<![A-Za-z:])[A-Za-z]:[/\\]")
# sys.path 场景的跨仓引用
PAT_SYSPATH_LEGACY = re.compile(r"sys\.path\.(?:insert|append)\([^)]*(?:regulatory_scrapers|regulatory_classifier|internal_policy_drafter|internal_policy_base)")
# OCR 旧硬编码
PAT_TESS = re.compile(r"Program Files[/\\]Tesseract", re.IGNORECASE)
# 注释/docstring 中说明性路径提及（非代码逻辑），亦拦截但可豁免——由白名单子串决定
PAT_WINPATH_NOTE = re.compile(r"[A-Za-z]:\\")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
EXCLUDE_DIRS = {"backups", "data", "cache", "logs", "venv", ".git", "__pycache__", "reports"}
# 定义/注释含"盘符"字样者跳过：本文件、paths.py（ROOT 定义）；tools 显式 --root 默认值豁免在下方处理
EXCLUDE_FILES = {"paths.py", "gate_hardcoded_paths.py"}

# tools/* 默认参数中的演示路径白名单（如 build_migration_manifest --root default）
ALLOW_SUBSTR = ("--root", "--out", "default=")


def _is_allowed_line(line: str) -> bool:
    """允许行：tools CLI 默认参数演示路径（非业务硬编码）。"""
    return any(a in line for a in ALLOW_SUBSTR)


def _walk_py():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn in EXCLUDE_FILES:
                continue
            yield os.path.join(dirpath, fn)


def run():
    findings = []
    for fp in _walk_py():
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_allowed_line(line):
                        continue
                    if PAT_DRIVE.search(line) or PAT_SYSPATH_LEGACY.search(line) or PAT_TESS.search(line):
                        findings.append((os.path.relpath(fp, ROOT), lineno, line.strip()[:90]))
        except OSError:
            continue
    passed = not findings
    detail = {"count": len(findings),
              "examples": [f"{f}:{l}: {t}" for f, l, t in findings[:10]]}
    return passed, detail
