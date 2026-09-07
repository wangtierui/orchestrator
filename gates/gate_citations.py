# -*- coding: utf-8 -*-
"""
gates/gate_citations — 制度引用门禁（drafter verify_regulatory_citations --strict 迁移）

P0：骨架放行。P4 接入：drafter docs 引用校验数据源切换为 interfaces.rfn_api/theme_api
（消除 md 手工表漂移）；--strict 任一 FAIL/漂移/时效枚举违规 → exit 1。
"""
from __future__ import annotations


def run():
    return True, {"note": "待接入（P4 drafter 结构化支撑改造后启用）"}
