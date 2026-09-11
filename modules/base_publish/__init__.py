# -*- coding: utf-8 -*-
"""base_publish — 双底座发布层（Base Contract v1 实装，2026-09-12）

架构方案 §4.1/§5 落地：把双底座"工作目录"整理为**发布契约**（published/ 发布件）：
  - 外部底座（modules/regulatory_scrapers/published/）：external_records / external_clauses /
    external_attachments + publish_manifest.json + external_index.sqlite（SQLite+FTS5）
  - 内部底座（modules/internal_policy_base/published/）：internal_policies / internal_clauses
    + publish_manifest.json + internal_index.sqlite

调用纪律：应用模块只经 `interfaces/base_api`（进程内）或 `cli.py base ...`（跨进程）
读发布件；禁止裸读底座内部目录。
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
