# -*- coding: utf-8 -*-
"""
std_lib — 共享库唯一副本（P1 自旧仓复制 scraper_std；common_lib 为新统一通用层）。

分层（R20）：
  - std_lib/scraper_std/        领域共享（清洗/OCR/文号/附件/docx 等）
  - std_lib/common_lib/         跨域通用（fs_lock/io_atomic/norm/index_store/audit）
依赖方向：modules → std_lib；common_lib 不 import scraper_std；禁止反向 import modules。
路径一律 from paths import ...（R4）；不内置第二套路径逻辑。
"""
