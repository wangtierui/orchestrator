# -*- coding: utf-8 -*-
"""
interfaces/contract.py — 数据契约（列头/枚举/键集/等价别名/血缘字典）

设计（R24）：本模块为**程序可读契约**，供 gates.gate_contract 与各接口校验使用；
文本只做说明，实际以各实现模块 symbol 为准（见 config/schema/contract_manifest.json）。

P0 状态：以下常量从既有代码复制（值已核验）；P1 迁移 scraper_std/unified_schema 后，
CSV_COLUMNS 等改为 re-export（R5）避免双定义。
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# cleaned CSV 39 列（source: std_lib/scraper_std/unified_schema.py CSV_COLUMNS，2026-09 实测）
# --------------------------------------------------------------------------- #
CLEANED_CSV_COLUMNS: list[str] = [
    "index_no", "title", "doc_type", "category", "publish_date", "effective_date",
    "issue_organ", "document_number", "source_url", "source", "body_text",
    "body_text_webpage", "body_text_doc", "summary", "data_format", "mime_type",
    "column_name", "theme_name", "status", "timeliness_status",
    "replacement_document", "verification_source", "keyword", "attachment_count",
    "attachment_content", "attachment_content_path", "attachment_content_md5",
    "table_structured", "table_raw_text", "table_recovery_method",
    "downloaded_doc_path", "downloaded_doc_url", "body_source", "dedup_key",
    "raw_uncut_text", "split_sentences", "renamed_filename",
    "_metadata", "_raw_fields",
]

# --------------------------------------------------------------------------- #
# 监管文件归属表 8 列（source: modules/regulatory_classifier/rfn/registry.py CSV_FIELDS）
# 注：归属表保持 8 列零变更（Q1）；RFN↔clean 溯源走独立 rfn_clean_bridge.csv。
# --------------------------------------------------------------------------- #
REGISTRY_CSV_FIELDS: list[str] = [
    "监管文件编号", "文件名称", "发文字号", "发布日期",
    "文件来源", "时效状态", "判定日期", "编号备注",
]

# 主题归属表 3 列
THEME_FIELDS: list[str] = ["监管文件编号", "主题", "判定依据"]

# RFN↔clean 溯源桥 9 列（Q1=A；写者=reconcile 后处理 R7）
RFN_CLEAN_BRIDGE_FIELDS: list[str] = [
    "监管文件编号", "文件来源", "source_url", "dedup_key",
    "登记时标题", "登记时文号", "最近确认日期", "最近状态", "relation",
]

# --------------------------------------------------------------------------- #
# 等价别名映射（N7：源标识 5 称谓收敛）— 供历史数据迁移/读取兼容
# --------------------------------------------------------------------------- #
# 源标识等价：目标规范名 → 历史曾用名（读取旧底座/旧明细时归一）
SOURCE_ALIAS_EQUIV: dict[str, tuple[str, ...]] = {
    "gov": ("gov", "scraper_gov", "govcn"),
    "mof": ("mof", "scraper_mof"),
    "nfra": ("nfra", "scraper_nfra"),
    "pbc": ("pbc", "scraper_pbc"),
    "supp": ("supp", "supplementary", "scraper_supp"),
}
# 字段语义等价（不同层同义不同名）
FIELD_SEMANTIC_EQUIV: dict[str, tuple[str, ...]] = {
    "document_number": ("document_number", "doc_no", "document_no", "发文字号"),
    "title": ("title", "文件名称"),
    "source": ("source", "文件来源", "file_src"),
    "timeliness_status": ("timeliness_status", "时效状态", "eff_status", "status"),
}

# --------------------------------------------------------------------------- #
# 底座 JSON 键集（Gate4 核心键，source: recall_audit/run_retrieval_after_checks.py:455-464）
# 超集判定：允许未来加字段，缺字段必报。
# --------------------------------------------------------------------------- #
BASE_KEYS = {"监管文件编号", "doc_no", "eff_status", "file_src", "real_year",
             "title", "year_reported"}
FINAL_KEYS = BASE_KEYS | {"cluster", "source_origin", "src_mark"}
MATCHED_KEYS = {"监管文件编号", "body", "body_len", "docno", "lib", "title"}
CITEREFS_KEYS = {"监管文件编号", "art_refs", "basis", "body_len", "lib",
                 "name_refs_top", "title"}
