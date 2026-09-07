# -*- coding: utf-8 -*-
"""
gates/gate_flat_layout — 目录拍平校验（S-5，旧 classifier check_flat_layout 语义）

规则：modules/*/data 与 modules/*/docs 不得出现子目录（下游按「目录根+文件名前缀」定位产物，
出现子目录会导致静默失效）。P0 骨架 modules/* 尚为空时放行；P4 数据迁入后生效。
"""
from __future__ import annotations

import os

import paths

SCAN_SUBDIRS = ("data", "docs")


def run():
    problems = []
    checked = []
    for name in ("regulatory_scrapers", "regulatory_classifier",
                 "internal_policy_base", "internal_policy_drafter"):
        mod_root = os.path.join(paths.MODULES_DIR, name)
        if not os.path.isdir(mod_root):
            checked.append(f"{name}: 未创建")
            continue
        for sub in SCAN_SUBDIRS:
            base = os.path.join(mod_root, sub)
            if not os.path.isdir(base):
                continue
            subdirs = [d for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d))]
            if subdirs:
                problems.append(f"{name}/{sub} 存在子目录: {sorted(subdirs)}")
    return (not problems), {"problems": problems, "checked": checked}
