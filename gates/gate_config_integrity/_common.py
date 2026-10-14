# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._common — 包内公共基础设施（W4-c 五拆）。

与 `std_lib.common_lib.governance_store._db` 同构的分层纪律：**共用件住在独立的
私有子模块**，各族判据模块（_manifest/_schedule/_steps/_domains/_structure）与
门面 `__init__` 都从 `._common` 导入 —— 禁止「子模块 → 包门面」的环状导入。

为什么不能沿用「子模块 from . import ROOT」：门面为了提供 `run()` 聚合入口必须
在文件底部 import 各族判据模块，子模块再回头 import 门面即形成环；运行期 Python
可解（底部导入时 ROOT 已定义），但 mypy 在环上无法确定类型
（"Cannot determine type of ROOT" [has-type]，CI mypy 阻断项）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import paths

ROOT = paths.ROOT
_TOOLS = paths.TOOLS_DIR
_MANIFEST = os.path.join(_TOOLS, "_manifest.json")
_RETIRED_DIR = "retired"
_RETIRED_README = os.path.join(_TOOLS, _RETIRED_DIR, "README.md")

# N-173（2026-09-30）：**扫描排除集的公共基集** —— `.git`/`__pycache__`/`.pytest_cache` 是
# 三处扫描（J3 引用 / J8 语料 / U 常量）**共同**要排除的；其余项**各检查语义不同**，
# 仍各自追加（**不可强行合一**：J3 跳 `tools`（由本门禁与 README 覆盖），
# 语料/U 跳 `data`/`reports` 等 —— 语义不同，合并会让某检查漏扫或多扫）。
# 统一基集是**拆分本门禁的前置**（否则拆后各检查会各自维护一份排除集 → 制造新重复）。
_SCAN_BASE_SKIP = frozenset({".git", "__pycache__", ".pytest_cache"})

# J3：引用扫描的扩展名（文档 .md 允许提及，但须带"已退役"字样，由人工维护）
_REF_EXTS = (".py", ".yaml", ".yml", ".toml", ".bat", ".cfg", ".ini", ".ps1")
_REF_SKIP_DIRS = set(_SCAN_BASE_SKIP) | {"tools"}  # tools 由本门禁自身与 README 覆盖


def _load_manifest() -> dict:
    with open(_MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def _load_module_from_path(mod_name: str, path: str):
    """按文件路径加载模块（schedule/triggers 等无包语义的脚本）。"""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
