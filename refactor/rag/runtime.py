# -*- coding: utf-8 -*-
"""runtime — 运行环境探测（跨平台兼容的唯一来源）。

规则（按你的要求）：
  - 检测到 macOS (Darwin)  → 判定为你的 Mac 笔记本（M1 Pro），走 mps 加速；
  - 其它（Windows / Linux）→ 走 CPU。
这样同一份代码在 Mac 上用神经网络引擎加速，在 Windows 上纯 CPU 也能跑，
不依赖任何手动开关。
"""
from __future__ import annotations

import platform


def is_mac() -> bool:
    """是否为 macOS（即你的 M1 Pro 笔记本）。"""
    return platform.system() == "Darwin"


def detect_device() -> str:
    """返回 embedding / reranker 使用的 torch 设备字符串。

    Mac -> "mps"（M1 Pro 的 Metal/神经网络引擎）；其余 -> "cpu"。
    """
    return "mps" if is_mac() else "cpu"
