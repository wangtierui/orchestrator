# -*- coding: utf-8 -*-
"""让 pytest 始终以 refactor/ 为项目根解析顶层包。

父工程 orchestrator/ 也有 pyproject.toml / config 包，pytest 启动阶段可能
把 ``config`` 解析成父工程的 ``config/``（没有 settings）并缓存到 sys.modules；
此时仅调整 sys.path 不会重新解析，必须同时清掉错误缓存。

本文件只声明项目内路径，不注入父目录，也不改变运行环境以外的行为。
"""
import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

_cached = sys.modules.get("config")
if _cached is not None:
    _file = os.path.abspath(getattr(_cached, "__file__", "") or "")
    if not _file.startswith(_ROOT + os.sep):
        del sys.modules["config"]
