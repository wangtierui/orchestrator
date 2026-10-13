# -*- coding: utf-8 -*-
"""refactor.clause — clean 之后的条文抽取阶段（storage-native）。

把 clean 后的文档正文（storage namespace="cleaned"）解析为章/条/项结构，
产物写回 storage namespace="clauses"。算法核心在 core/document_structure.py，
从 std_lib.scraper_std.document_structure 逐字抽取（源无关、storage-free）。
"""
