# -*- coding: utf-8 -*-
"""clean.core — clean 阶段的核心清洗算法（业务核心，非公用库）。

这些才是 clean 真正"增值"的逻辑——把爬虫抓下来的脏数据洗干净。编排层
（clean.stages / clean.pipeline）只负责按顺序调度，本包放具体的算法实现，可单独单测：

  cleaner.py            clean_text（去噪/空白/乱码）/ dedup_records
                        （normalize_date 已上收到 refactor/common/text.py，collectors 与 clean 共用）
  sentence_split.py     repair_text（断句+表格判定）
  ocr_correction.py     correct_ocr_text / JiebaDict / build_high_freq_dict /
                        run_ocr_correction（OCR 组合配方，依赖 jieba）
  body.py               clean_and_repair（正文清洗 + 断句修复组合配方）
  doc_number.py         文号抽取/规范化 —— 现仅再导出 common.doc_number（唯一事实源）
  load.py               load_raw_records（双格式载入，供 stages.LoadStage）

映射层（raw/信封 → clean 统一 schema）在 clean.adapter，契约定义在 clean.schema。
极简 clean（见 clean.pipeline）只做"洗脏数据"：载入 → 适配 → 日期/正文/OCR/去重 → 写出。
交付闸门、隔离、sidecar、history、CSV 治理等不属本包（不属于清洗）。
"""
