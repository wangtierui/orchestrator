# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.collectors — 五源抓取器包（R15：sources.yaml collector 字段消费）。

**结构（2026-10-15 W4 L1 重组，方案 §7.9.2）**：由「单层拍平 + 源前缀命名」改为
**按源建子包**，源内按职责分层命名（编排 collector / 解析 parse / 附件 attachments /
子源 zhengceku / 离线 weekly 等）：

    collectors/
      gov/    collector.py  parse.py  attachments.py  zhengceku.py
      mof/    collector.py  attachments.py
      nfra/   collector.py  common.py  fetch_lists.py  fill_details.py
              fetch_attachments.py  attachments_extract.py  weekly.py
      pbc/    collector.py  parse.py  backfill.py  recover.py
      supp/   ingest.py  ingest_local_dir.py  ingest_batch.py  normalize.py
              parser.py  ocr.py  sanitize.py  extract_doc_text.py

`config/sources.yaml` 的 collector 字段随之由单段模块名（`collectors.gov_collector`）
改为**两级点号路径**（`collectors.gov.collector`），由 `config.loader.collector_module`
/ `collector_path` 解析（点号→路径）。

**双形态 import 纪律**：每个采集脚本既要能被包形态加载
（`modules.regulatory_scrapers.collectors.<src>.<role>`），又要能以脚本形态独立启动
（`python collectors/<src>/<role>.py` —— 编排器以子进程调用的真实方式）。故兄弟模块
import 一律「相对导入 + 裸名兜底」，仓根引导的上溯层数随目录深度同步（均为 4 级）。
`tests/test_collectors_dual_form.py` 对两个维度与 yaml/argv 接线做全量契约锁定。

本 __init__ 仅为合法包化，不承载业务逻辑；各文件保持自含引导可独立脚本运行。
"""
