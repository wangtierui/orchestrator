# -*- coding: utf-8 -*-
"""nfra.attachment_paths — **路径常量层**（E5 第二层，2026-10-19）

为什么单独一层（设计取舍）
--------------------------
`ATT_DIR` 被 **process / report / 编排**三层共用。三个候选：
  ① 留编排层，各层反向 import —— **成环**（编排层 import 各层做 re-export）；
  ② 每层各复制一份 —— **必漏改其一**，且路径常量改了不报错、只写错地方；
  ③ **下沉为最底层常量模块**，各层都依赖它 —— 依赖单向、单一事实源。✅

本层即方案③。它只依赖 `std_lib.scraper_std.cache_store`（取产物根），
**不依赖任何 nfra 内部层** ⇒ 可被所有层安全 import。

⚠️ 本层的常量在**导入期**求值（`docs_root(...)`）⇒ 层间 import 必须置于使用点之前
（E3/E4 的教训：Python 导入期即求值，不是调用期）。
"""
from __future__ import annotations

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：本目录路径计算需要它
del _p, _os, _sys

import os

from std_lib.scraper_std.cache_store import docs_root, source_cache_root

#: 附件产物根（统一 data/docs；与 fetch_attachments / attachments_extract 一致）
ATT_DIR = docs_root("nfra", "attachments")

#: 列表/详情请求缓存根（原 `CACHE`，编排层的取数扫描用）
CACHE = source_cache_root("nfra")

#: 详情缓存的文件名模式（编排层的 glob 用）
DETAIL_GLOB = os.path.join(CACHE, "SelectByDocId__docId_*.json")

#: 附件质量报告路径（report 层用）
QUALITY_REPORT = os.path.join(ATT_DIR, "_quality_report.json")

#: 本目录绝对路径（process 层的相对路径计算用）
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
