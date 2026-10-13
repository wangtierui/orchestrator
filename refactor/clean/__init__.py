# -*- coding: utf-8 -*-
"""refactor.clean — 清洗阶段包（Phase 1 骨架）。

消费 collectors 产出的"统一信封"记录，转换为 clean 统一 schema（UNIFIED_SCHEMA），
后续再叠加正文清洗 / 断句 / OCR / 校验 / 去重（见 lib/ 各模块，当前为 TODO）。

设计依据（见重构对话）：
  - A：重构信封字段 ↔ clean UNIFIED_SCHEMA 逐字段对照（adapter.py 即其落地）；
  - B：clean stage 目录/模块设计（base/config/adapter/lib/run_clean）。
"""
