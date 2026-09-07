# -*- coding: utf-8 -*-
"""
gates/gate_rfn_sync — RFN 跨文件一致性（旧 classifier scripts/check_rfn_sync 迁移）

P0：骨架放行。P4 接入：归属表 → final.json/明细表/索引 的 RFN 与发文字号一致性校验，
文号变化超白名单 exit 1；并提供 --fix 回写（经 rfn_api，禁裸写）。
"""
from __future__ import annotations


def run():
    return True, {"note": "待接入（P4 classifier rfn 迁入后启用）"}
