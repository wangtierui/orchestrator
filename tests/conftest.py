# -*- coding: utf-8 -*-
"""tests/conftest.py — 测试侧统一引导（v2 方案 §3.11 T1、§3.1.2）

背景：改造前 26 个测试文件**各自** `sys.path.insert`（口径不一、易漂移），
且无 conftest.py。本文件是全仓测试的唯一引导点：pytest 会在导入任何测试模块前
加载它，随后各测试文件内的历史 `sys.path.insert` 成为幂等冗余（不再需要，但
保留以兼容——由 `gate_import_bootstrap` 以基线方式跟踪收敛）。

同时显式声明 `data` 标记（`pyproject.toml` 已注册），供无数据环境
`pytest tests -m "not data"` 使用。
"""
from __future__ import annotations

import pytest

from bootstrap import bootstrap

# 唯一引导点：仓根 + 全部业务模块 + tools（测试普遍需要 `from tools import ...`）
bootstrap("all", include_tools=True)


@pytest.fixture(scope="session")
def repo_root():
    """仓库根路径（避免各测试文件重复计算）。"""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent
