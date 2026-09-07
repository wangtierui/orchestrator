# -*- coding: utf-8 -*-
"""
gates/gate_no_duplicate_libs — 重复工具/重名再定义扫描（专项三 / R20）

P0：骨架放行。P1 起检测：
  - modules 四仓本地重复定义 norm_docno/norm_title/_atomic_write/msvcrt 锁 → FAIL；
  - sys.path.insert 含盘符 → FAIL（与 gate_hardcoded_paths 联动）；
  - 重名再定义（如 supp 旧 downloader 内联 sniff_kind 与 scraper_std 同名异义）→ FAIL。
"""
from __future__ import annotations

DUPLICATE_SYMBOLS = ("norm_docno", "norm_title", "_atomic_write")


def run():
    return True, {"note": "待接入（P1 迁移后启用）"}
