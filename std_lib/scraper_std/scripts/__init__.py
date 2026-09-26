# -*- coding: utf-8 -*-
"""
scraper_std.scripts —— 五源 scripts/ 再导出层「单一事实源」。

2026-09-04 共享库对齐审计 · 阶段 5：
将 gov/mof/nfra/pbc 四源逐字节相同的 scripts/downloader.py、parser.py、
spider_main.py 收敛到本包。各源 scripts/ 下仅保留薄再导出壳（sys.path 引导
+ 显式 re-export），保证 `from scripts.downloader import X` 与
`python scripts/spider_main.py` 的对外入口与改造前完全一致（零行为变更）。

注意：supplementary_regulations_scraper 的 3 件为不同变体（含内联 sniff_kind
等），不在此收敛范围，维持项目内联实现。
"""

from __future__ import annotations
