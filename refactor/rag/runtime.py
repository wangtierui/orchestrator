# -*- coding: utf-8 -*-
"""runtime — 运行环境探测（BGE-M3 推理设备）。

    - macOS (Darwin) → "mps"（Apple Silicon Metal 加速）；
    - 其它平台       → "cpu"。
"""
from __future__ import annotations

import platform


def is_mac() -> bool:
    """是否为 macOS。"""
    return platform.system() == "Darwin"


def detect_device() -> str:
    """返回 embedding 推理使用的 torch 设备字符串。"""
    return "mps" if is_mac() else "cpu"
