# -*- coding: utf-8 -*-
"""modules.regulatory_scrapers — 外部监管采集+清洗。

P3 目录规划：
  clean/run_clean_pipeline.py    单实例 --project {gov|mof|nfra|pbc|supp}（替代五份近似脚本）
  clean/supp_runtime.py          supp 运行时注册（supp_mapper/sanitize_newlines）
  collectors/                    源特有抓取器（去壳聚合，专项一）
  timeliness_review/             效力核验（P4 迁入）
共享库一律 import std_lib（含 scraper_std）；禁止 sys.path 盘符。
"""
