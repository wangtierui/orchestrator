# -*- coding: utf-8 -*-
"""
scraper_std —— 监管爬虫项目共享标准库（S 级交付基线）

定位：四监管爬虫（gov / mof / nfra / pbc）统一的「标准能力层」，
逐项落地《政府网站信息爬取项目——全链路系统性排查、修复与交付标准》：

  - encoding     ：chardet 编码自动检测（置信度>0.9 采用，否则 gb18030→utf-8 降级）
  - http         ：反爬加固客户端（UA 池含移动端、富请求头、自适应限速、指数退避、
                   Retry-After 尊重、代理开关、流式下载）
  - config       ：settings.yaml 装载（物理开关：enable_proxy / max_pages_today /
                   stop_after_n_errors / delay 区间等）
  - logging_setup：结构化日志（JSON 行：timestamp/project_name/task_id/url/status_code/
                   elapsed_time/error_type）
  - metrics      ：运行指标收集与报告（logs/metrics_{date}.json）
  - circuit_breaker：关键锚点检测 + 熔断（连续 3 次未命中 → 告警 + HTML 快照）
  - schema_validation：字段级语义校验 + 空值阈值告警（>3% 暂停）
  - cleaner      ：基础清洗（去重/去噪/空白与控制字符/日期/全角/缺失值）
  - sentence_split：四级断句修复（含表格污染前置过滤）
  - table_recovery：表格行列结构还原（list[list[str]] 优先、表头合并填充、
                    跨页去重、空值填充、raw_only 兜底）
  - ocr_correction：OCR 混淆校正（长词优先替换/词典编辑距离校验/噪声过滤/低置信标记）
  - naming       ：正文与附件标准重命名（{索引号}_{类型}_{标题摘要}_{日期}{序号}{ext}）
  - attachments  ：附件流式下载 + 全文抽取 + 元数据（MD5/状态/字数）
  - checkpoint   ：断点续抓 + 优雅退出（SIGINT/SIGTERM → checkpoint.json）
  - secret_scan  ：敏感字符串扫描（硬编码口令/密钥检测）
  - pipeline     ：统一清洗管道（原始数据 → 统一 Schema → 清洗 → 校验 → 去重 →
                   CSV(UTF-8 BOM)+JSONL 双轨输出 → 历史版本管理 → 指标报告）
  - unified_schema：统一元数据字段映射（对齐 GB/T 42147-2022 核心元数据集）

设计原则：
  - 零硬依赖：第三方库全部惰性导入，缺失时透明降级并标记状态，不静默丢数据；
  - 优雅降级：任何一步失败不影响已产出数据，失败原因进入结构化日志与指标；
  - 可测试：每模块提供离线自检（`python -m scraper_std.<mod>` 或 pytest）。
"""

__version__ = "1.0.0"
__clean_version__ = "v1.0.0"

import os
import sys

# P1（2026-09-08）：orchestrator 仓库根，供顶级 config 包（config.enums）导入。
# 不再插入旧仓路径；跨模块取数据一律经 interfaces / paths（R4/Q3）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # std_lib/
_ORCH_ROOT = os.path.dirname(_ROOT)  # regulatory_compliance_orchestrator/
for _p in (_ORCH_ROOT, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
