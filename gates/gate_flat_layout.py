# -*- coding: utf-8 -*-
"""
gates/gate_flat_layout — 目录拍平校验（S-5，旧 classifier check_flat_layout 语义）

规则：modules/*/docs 不得出现子目录；modules/*/data 不得出现子目录，
**唯一例外**：modules/regulatory_scrapers/data/cleaned —— 五源统一 cleaned 数据根
（N-1 决策：gov/mof/nfra/pbc/supp 的 cleaned_*.{csv,jsonl} 全量集中于此，
clean_index 亦只扫该目录），属合法扁平布局。

P0 骨架 modules/* 尚为空时放行；P4 数据迁入后生效。
"""
from __future__ import annotations

import os

import paths

# 允许的 data 子目录（唯一例外：scraper 统一 cleaned 数据根）
ALLOWED_DATA_SUBDIRS = {"cleaned"}


def run():
    problems = []
    checked = []
    for name in ("regulatory_scrapers", "regulatory_classifier",
                 "internal_policy_base", "internal_policy_drafter"):
        mod_root = os.path.join(paths.MODULES_DIR, name)
        if not os.path.isdir(mod_root):
            checked.append(f"{name}: 未创建")
            continue
        docs = os.path.join(mod_root, "docs")
        if os.path.isdir(docs):
            subdirs = [d for d in os.listdir(docs)
                       if os.path.isdir(os.path.join(docs, d))]
            if subdirs:
                problems.append(f"{name}/docs 存在子目录: {sorted(subdirs)}")
        data = os.path.join(mod_root, "data")
        if os.path.isdir(data):
            subdirs = [d for d in os.listdir(data)
                       if os.path.isdir(os.path.join(data, d))]
            bad = [d for d in subdirs if d not in ALLOWED_DATA_SUBDIRS]
            if bad:
                problems.append(f"{name}/data 存在子目录: {sorted(bad)}"
                                f"（允许 {sorted(ALLOWED_DATA_SUBDIRS)}）")
    return (not problems), {"problems": problems, "checked": checked}
