# -*- coding: utf-8 -*-
"""
gates/gate_hardcoded_snapshots — 硬编码 cleaned 快照日期 lint（N-3，旧 lint_hardcoded_snapshots 迁移）

P0：骨架放行。P1 迁入旧仓 lint_hardcoded_snapshots.lint_hardcoded() 实现：
扫描 scraper 与 classifier 下 .py，禁止 {src}_cleaned_{YYYYMMDD}.{csv|jsonl} 文件路径字面量。
"""
from __future__ import annotations


def run():
    return True, {"note": "待接入（P1 复制旧 lint_hardcoded_snapshots 后启用）"}
