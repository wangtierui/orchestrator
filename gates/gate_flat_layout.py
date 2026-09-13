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
#   - scraper:             cleaned（五源统一 cleaned 数据根）/ docs（文档产物统一根 2026-09-08）
#   - internal_policy_base: originals/processed（原始件副本 + 结构化正文，P6 数据随仓）
ALLOWED_DATA_SUBDIRS = {
    # docs：五源正文原文/附件统一根（docs_root(source) → data/docs/<scraper_slug>/）
    # reports/state：collector 运行产物（mof_laws_report.html 等报告、status.json/运行时锁，
    #   2026-09-09 mof 全量轮实证创建，写入位置=dirname(outdir) 下的 reports/state）
    # corpus：外部语料归集根（F-L05：EAST2.0/部门制度等按域归集，保留原始目录树；
    #   清单 reports/corpus/*.manifest.json 入库，语料本体不入库）
    "regulatory_scrapers": {"cleaned", "clauses", "history", "raw", "docs", "reports", "state", "corpus"},
    # ledgers/misc：非制度正文的隔离区（2026-09-13）——台账/清单类与图片/压缩/数据库等
    # 从 originals 分离出来，使 originals 回归"单一扁平原件层"（命名规范 文号_名称）。
    "internal_policy_base": {"originals", "processed", "ledgers", "misc"},
    "internal_policy_drafter": {"draft_clause"},      # 条款级对照素材（P8 端到端编排输出，R21 驱动）
    # relations：依据/废止关系产物根（R-F01，2026-09-14）——事实源 relations_index.jsonl
    #   + 派生视图 cross_basis.jsonl + 统计 relations_stat.json
    "regulatory_classifier": {"relations"},
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
