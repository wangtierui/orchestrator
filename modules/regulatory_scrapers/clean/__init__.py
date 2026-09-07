# -*- coding: utf-8 -*-
"""regulatory_scrapers.clean — 五源统一清洗入口（P3a 落地）。

单实例 run_clean_pipeline.py --project {gov|mof|nfra|pbc|supp} 取代旧五源各自
scripts/run_clean_pipeline.py 近似副本；supp 定制（mapper 注册/sanitize）由 supp_runtime 承担。
"""
