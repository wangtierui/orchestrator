# -*- coding: utf-8 -*-
"""
std_lib.scraper_std — 五源清洗/OCR/文号/附件/文档解析共享库（领域共享层）

P0：本包为占位（含 __init__ 与 scripts 命名空间声明）；P1 自旧仓
regulatory_scraper_std_lib/scraper_std（含 common/、scripts/、doc_number.py、
ocr_engine.py、unified_schema.py、pipeline.py 等）**整体复制**入本目录，并按 R5/R19 改造：
  - unified_schema 受控枚举改为 from config.enums import ... re-export；
  - 硬编码路径清零（gate_hardcoded_paths 覆盖本包自身）；
  - 新增 document_structure（章-条-款-项，R21）。
"""
