# -*- coding: utf-8 -*-
"""supp/supp_constants — supp 采集的**纯常量与引导**（无业务逻辑）。

为什么单独成文件（2026-10-20，D-遗留拆分第三批）
------------------------------------------------
`supp/ingest.py` 原本 603 行（22 个顶层块：9 常量 + 13 函数）。常量区里有一条
**隐式的顺序依赖**，混在函数里很难看清：

```
HERE / SCRAPERS_ROOT  ← 先算出两个根
for _p in (HERE, SCRAPERS_ROOT): sys.path.insert(...)   ← 再注入 sys.path
from config.exitcodes import ExitCode                    ← 之后才能 import 仓内模块
from std_lib.scraper_std.pipeline import (read_raw_records, write_raw_jsonl)
```

把它抽出来后，「**引导必须先于仓内 import**」这个约束变成文件级显式事实
（本模块只做引导 + 常量，不做业务），下游 `ingest.py` 只要**先导入本模块**
即可安全 import 仓内依赖。

**⚠️ 逐字搬运**：常量与注释全部原样搬自 `ingest.py`，**未改任何取值**
（含 `RAW_OUT` 仍指向 `.json`——那是既有口径，改扩展名属交付面变更，
需单独评估，不在拆分范围）。
"""
from __future__ import annotations

# ---- P3b 仓库引导（迁移脚手架当时注入的固定块）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # …/collectors/supp
# ⚠️ 2026-10-20 修（实测，与 mof/pbc 同类的**第 4 处**）：原 `dirname(HERE)` 只上溯
# **一级** ⇒ 得到 `…/collectors`（注释却写 "modules/regulatory_scrapers"，两者不符）。
# 后果：`SRC_DIR`/`RAW_OUT`/`STATE_DIR` 全部落到 `…/collectors/data/…`
# ⇒ **supp 采集会静默写到错位置、白跑且不报错**（实测：现存产物
#   `…/regulatory_scrapers/data/raw/supplementary_regulations.jsonl`（13.8MB）
#   是旧代码写的，而当前代码算出的 `RAW_OUT` 指向 `…/collectors/data/raw/…`
#   —— 该目录根本不存在）。
# 修正为上溯**两级**到 `modules/regulatory_scrapers`（统一数据根），
# `RAW_OUT` 经 `write_raw_jsonl` 规范化后正好等于现存产物路径 ⇒ **回到既有口径**。
SCRAPERS_ROOT = os.path.dirname(os.path.dirname(HERE))     # modules/regulatory_scrapers

#: **顺序关键**：先把两个根注入 `sys.path`，下游才能 `import config / std_lib`。
for _p in (HERE, SCRAPERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from std_lib.scraper_std.cache_store import docs_root

REPO_ROOT = SCRAPERS_ROOT  # 数据根 = modules/regulatory_scrapers（拍平后单层上溯一次即到）
# 5b 补全（2026-09-04）：supp ingest 收敛至统一 data/（此前写 per-source data/raw 与统一副本分裂；
# _sources 源文本随迁统一 data/raw/_sources；content_ref / OCR 缓存引用在统一后相对 REPO_ROOT 解析一致）
SRC_DIR = os.path.join(REPO_ROOT, "data", "raw", "_sources")
RAW_OUT = os.path.join(REPO_ROOT, "data", "raw", "supplementary_regulations.json")

DOCS_DIR = docs_root("supp")
STATE_DIR = os.path.join(REPO_ROOT, "data", "state")

# 用户提供的本地 PDF 源文件（仅读取，不移动）。
# P3b 去硬编码：由环境变量提供（LOCAL_PDF_F15 / LOCAL_PDF_F17），未设置则为空（跳过该来源）。
LOCAL_PDFS = {
    "F15": os.environ.get("LOCAL_PDF_F15", ""),
    "F17": os.environ.get("LOCAL_PDF_F17", ""),
}

__all__ = ["DOCS_DIR", "HERE", "LOCAL_PDFS", "RAW_OUT", "REPO_ROOT",
           "SCRAPERS_ROOT", "SRC_DIR", "STATE_DIR", "docs_root"]
