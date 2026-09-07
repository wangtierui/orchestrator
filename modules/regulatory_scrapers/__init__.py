# -*- coding: utf-8 -*-
"""modules.regulatory_scrapers — 外部监管采集+清洗（P1 复制迁入）。

迁移白名单（v2 §1.2）：collectors/*（源特有抓取）+ clean/run_clean_pipeline.py +
timeliness_review/；不复制：各源 scripts/downloader|parser|spider_main 薄壳、
utils/alert_mail|db_client 重复件（由 std_lib 提供）、backups/。
"""
