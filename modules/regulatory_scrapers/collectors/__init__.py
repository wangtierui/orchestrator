# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.collectors — 五源抓取器包（R15：sources.yaml collector 字段消费）。

拍平（2026-09-08 A 项）后全部抓取器为单层文件、源前缀命名，与 config/sources.yaml
「collector: collectors.<模块名>」一一对应。本 __init__ 仅为合法包化（供 importlib 按全限定名
加载 / resolve_collector_path 校验），不承载业务逻辑；各文件保持自含引导可独立脚本运行。
"""
