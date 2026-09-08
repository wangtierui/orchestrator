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

# 各模块 data/ 允许子目录白名单（其余一律拍平）：
#   - scraper:             cleaned（五源统一 cleaned 数据根）
#   - internal_policy_base: originals/processed（原始件副本 + 结构化正文，P6 数据随仓）
ALLOWED_DATA_SUBDIRS = {
    "regulatory_scrapers": {"cleaned", "clauses", "history"},  # history：清洗运行历史备份（时间戳归档）
    "internal_policy_base": {"originals", "processed"},
    "internal_policy_drafter": {"draft_clause"},      # 条款级对照素材（P8 端到端编排输出，R21 驱动）
}
# docs/ 允许子目录（报告产物目录等）：classifier docs/reports（R9 主题报告输出）
ALLOWED_DOCS_SUBDIRS = {
    "regulatory_classifier": {"reports"},
}


def run():
    problems = []
    checked = []
    for name in ("regulatory_scrapers", "regulatory_classifier",
                 "internal_policy_base", "internal_policy_drafter"):
        mod_root = os.path.join(paths.MODULES_DIR, name)
        if not os.path.isdir(mod_root):
            checked.append(f"{name}: 未创建")
            continue
        allowed = ALLOWED_DATA_SUBDIRS.get(name, set())
        allowed_docs = ALLOWED_DOCS_SUBDIRS.get(name, set())
        docs = os.path.join(mod_root, "docs")
        if os.path.isdir(docs):
            subdirs = [d for d in os.listdir(docs)
                       if os.path.isdir(os.path.join(docs, d))]
            bad_docs = [d for d in subdirs if d not in allowed_docs]
            if bad_docs:
                problems.append(f"{name}/docs 存在子目录: {sorted(bad_docs)}"
                                f"（允许 {sorted(allowed_docs) or '无'}）")
        data = os.path.join(mod_root, "data")
        if os.path.isdir(data):
            subdirs = [d for d in os.listdir(data)
                       if os.path.isdir(os.path.join(data, d))]
            bad = [d for d in subdirs if d not in allowed]
            if bad:
                problems.append(f"{name}/data 存在子目录: {sorted(bad)}"
                                f"（允许 {sorted(allowed) or '无'}）")
    return (not problems), {"problems": problems, "checked": checked}
