# -*- coding: utf-8 -*-
"""modules.regulatory_scrapers — 外部监管采集+清洗。

P3 目录规划：
  clean/run_clean_pipeline.py    单实例 --project {gov|mof|nfra|pbc|supp}（替代五份近似脚本）
  collectors/                    源特有抓取器（去壳聚合，专项一）
  timeliness_review/             效力核验（P4 迁入）
共享库一律 import std_lib（含 scraper_std）；禁止 sys.path 盘符。

W0-4（2026-10-13，C1）：删除 `clean/supp_runtime.py` 声称——该文件**不存在**（幽灵声称，
supp_mapper 已随 W4 退役计划处置）；「docstring 声称的模块必须可 import」由 W0-5 新增的
`gate_declaration_executor` 判据机器守护（AST 提取 + import 验证）。
"""
