# -*- coding: utf-8 -*-
"""commands.ping — orchestrator 命令：ping（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import paths


def run(argv):
    print(f"REG_ORCH_ROOT = {paths.ROOT}")
    print("P0 骨架 OK：paths / config / interfaces / gates / std_lib 已就位")
    return 0
