# -*- coding: utf-8 -*-
"""
interfaces/contract.py — 数据契约（列头/枚举/键集/等价别名/血缘字典）

设计（R24）：本模块为**程序可读契约**，供 gates.gate_contract 与各接口校验使用；
文本只做说明，实际以各实现模块 symbol 为准（见 config/schema/contract_manifest.json）。

P0 状态：以下常量从既有代码复制（值已核验）；P1 迁移 scraper_std/unified_schema 后，
CSV_COLUMNS 等改为 re-export（R5）避免双定义。
"""
from __future__ import annotations

import os
import sys

# F-D11 单源化（2026-09-12）：cleaned CSV 列契约唯一事实源 = scraper_std.unified_schema.CSV_COLUMNS
# （原复制字面量改为 re-export；interfaces → std_lib 为合法依赖方向，std_lib 不反向依赖本层）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STD_LIB = os.path.join(_ROOT, "std_lib")
if _STD_LIB not in sys.path:
    sys.path.insert(0, _STD_LIB)


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

# 明细表列（R10 provenance 2026-09-08 加 generated_by/at；写者 build_detail_tables.FIELDS re-export 此契约）
# F-L03（2026-09-12）：增"时效状态/核验来源"两列——法宝核验标记联入分析层（规划 2.1.1.4
# "核心文件引用-细化链条表（含法宝核验标记）"数据面落地；来源 clean 记录 timeliness_status/
# verification_source 字段）。
DETAIL_TABLE_FIELDS: list[str] = [
    "监管文件编号", "主题", "标题", "发文字号", "文件来源",
    "正文状态", "时效状态", "核验来源", "立法依据", "条款引用", "备注", "子主题",
    "generated_by", "generated_at",
]

# RFN↔clean 溯源桥 11 列（Q1=A；写者=reconcile 后处理 R7；R10 provenance 加 generated_by/at）
RFN_CLEAN_BRIDGE_FIELDS: list[str] = [
    "监管文件编号", "文件来源", "source_url", "dedup_key",
    "登记时标题", "登记时文号", "最近确认日期", "最近状态", "relation",
    "generated_by", "generated_at",
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
# --------------------------------------------------------------------------- #
# 中文列名受控注册表（G-字段治理 2026-09-08：CSV 系以中文列名为正式编码 → 逐层登记英文规范名；
# gate_field_aliases 强检"权威数据表新增中文列必须在此登记"，防无规范别名漂移。）
# scope ∈ attr/theme/detail/bridge/index/fingerprint/recall
# --------------------------------------------------------------------------- #
CN_FIELD_REGISTRY: dict[str, dict] = {
    # 文件归属表 attr（8）
    "监管文件编号": {"en": "rfn", "scope": "attr", "note": "RFN-<16hex> 实体唯一标识"},
    "文件名称": {"en": "title", "scope": "attr", "note": ""},
    "发文字号": {"en": "doc_no", "scope": "attr", "note": ""},
    "发布日期": {"en": "publish_date", "scope": "attr", "note": "YYYY-MM-DD"},
    "文件来源": {"en": "file_src", "scope": "attr", "note": "∈ SOURCE_SET"},
    "时效状态": {"en": "timeliness_status", "scope": "attr", "note": "7 值受控"},
    "判定日期": {"en": "verified_date", "scope": "attr", "note": "人工判定/核验日期"},
    "编号备注": {"en": "note", "scope": "attr", "note": ""},
    # 主题归属表 theme（3）
    "主题": {"en": "theme", "scope": "theme", "note": "T0..T10 完整名"},
    "判定依据": {"en": "basis", "scope": "theme", "note": ""},
    # 明细表 detail（10 中文 + 2 英文 generated_*）
    "标题": {"en": "title", "scope": "detail", "note": "明细叙述列（按归属表权威投影 R12）"},
    "正文状态": {"en": "body_status", "scope": "detail", "note": "完整/摘要/核心要点/无正文"},
    "核验来源": {"en": "verification_source", "scope": "detail", "note": "F-L03：法宝核验标记（北大法宝/规则判断）"},
    "立法依据": {"en": "basis", "scope": "detail", "note": "抽取（人工列保留）"},
    "条款引用": {"en": "article_refs", "scope": "detail", "note": "抽取（人工列保留）"},
    "备注": {"en": "note", "scope": "detail", "note": "人工列"},
    "子主题": {"en": "cluster", "scope": "detail", "note": "final.cluster 别名"},
    # 桥表 bridge（8 中文 + 3 英文）
    "登记时标题": {"en": "title_at_register", "scope": "bridge", "note": ""},
    "登记时文号": {"en": "doc_no_at_register", "scope": "bridge", "note": ""},
    "最近确认日期": {"en": "last_confirmed_date", "scope": "bridge", "note": ""},
    "最近状态": {"en": "bridge_state", "scope": "bridge", "note": "ok/drift_c1/drift_c2 —— 桥同步状态，非时效状态（同名不同语义）"},
    # rfn 索引 index（6，派生自归属表）
    # 指纹 fingerprint（6，含历史"唯一键"列）
    "唯一键": {"en": "unique_key", "scope": "fingerprint", "note": "DOC:/MD5: 去重键（dedup_key 语义不同=内容 sha256）"},
    "文件指纹": {"en": "fingerprint", "scope": "fingerprint", "note": "MD5 摘要"},
    "登记时间": {"en": "registered_at", "scope": "fingerprint", "note": ""},
    # recall 产物关键列
    "文件名": {"en": "file_name", "scope": "recall", "note": ""},
    "匹配源": {"en": "matched_source", "scope": "recall", "note": ""},
    "决策": {"en": "decision", "scope": "recall", "note": ""},
    "置信度": {"en": "confidence", "scope": "recall", "note": ""},
    "命中摘录": {"en": "hit_snippet", "scope": "recall", "note": ""},
    "可用正文长度": {"en": "body_chars", "scope": "recall", "note": ""},
    "全文状态": {"en": "fulltext_status", "scope": "recall", "note": ""},
    "建议新增词": {"en": "suggested_keyword", "scope": "recall", "note": ""},
    "类别": {"en": "category", "scope": "recall", "note": ""},
    "依据与说明": {"en": "rationale", "scope": "recall", "note": ""},
    "序号": {"en": "seq", "scope": "recall", "note": ""},
    "命中关键词": {"en": "hit_keyword", "scope": "recall", "note": ""},
    "判定理由": {"en": "reason", "scope": "recall", "note": ""},
    "建议主题归类": {"en": "suggested_theme", "scope": "recall", "note": ""},
}
# 字段语义等价（不同层/不同源同义不同名）
FIELD_SEMANTIC_EQUIV: dict[str, tuple[str, ...]] = {
    "document_number": ("document_number", "doc_no", "document_no", "发文字号"),
    "title": ("title", "文件名称", "doc_title"),
    "source": ("source", "文件来源", "file_src"),
    "timeliness_status": ("timeliness_status", "时效状态", "eff_status", "status"),
    # F-D06 契约化（2026-09-12）：raw 五源历史名收口——H-05 正文 5 名 / H-06 日期 3 格式。
    # 写侧保留原始字段（历史数据不重写），**读侧一律经 read_field 归一**（消费纪律）。
    "body_text": ("body_text", "full_text", "content_text", "content"),
    "publish_date": ("publish_date", "pub_date", "publishdate", "publish_date_original"),
    "effective_date": ("effective_date", "eff_date", "effective_date_original"),
    "source_url": ("source_url", "detail_url", "url"),
}

# --------------------------------------------------------------------------- #
# 字段别名消费端归一 API（字段治理 2026-09-08）
# 消费端跨层读值统一走 read_field(row, en)：自动尝试 FIELD_SEMANTIC_EQUIV 别名组 + 中文受控列
# （CN_FIELD_REGISTRY 中 en 命中的中文列名），避免各层硬编码"取 文件名称 还是 title"。
# --------------------------------------------------------------------------- #
def en_aliases(en: str) -> tuple[str, ...]:
    """英文规范名 → 全部可解析别名（FIELD_SEMANTIC_EQUIV 组 + 中文受控列中该 en 的中文名）。"""
    base = list(FIELD_SEMANTIC_EQUIV.get(en, (en,)))
    for cn, meta in CN_FIELD_REGISTRY.items():
        if meta.get("en") == en and cn not in base:
            base.append(cn)
    return tuple(dict.fromkeys(base))


def read_field(row: dict, en: str, default: str = "") -> str:
    """消费端按英文规范名读取行值；行键可为 英文别名/规范名/中文列名。返回 str 值。"""
    if row is None:
        return default
    for a in en_aliases(en):
        if a in row and row[a] is not None:
            v = row[a]
            return v if isinstance(v, str) else str(v)
    return default


def assert_alias_integrity() -> None:
    """别名体系自检：FIELD_SEMANTIC_EQUIV 每组含自身；CN_FIELD_REGISTRY 中文名不与
    FIELD_SEMANTIC_EQUIV 别名重复错配（同名不同 en 时报）。"""
    for en, group in FIELD_SEMANTIC_EQUIV.items():
        assert group and group[0] == en, (en, group)
    en_of_cn = {}
    for cn, meta in CN_FIELD_REGISTRY.items():
        en = meta.get("en") or ""
        assert en, f"CN_FIELD_REGISTRY 缺 en: {cn}"
        if cn in en_of_cn and en_of_cn[cn] != en:
            raise AssertionError(f"中文列 {cn} 映射冲突: {en_of_cn[cn]} vs {en}")
        en_of_cn[cn] = en


# --------------------------------------------------------------------------- #
# 底座 JSON 键集（Gate4 核心键，source: recall_audit/run_retrieval_after_checks.py:455-464）
# 超集判定：允许未来加字段，缺字段必报。
# --------------------------------------------------------------------------- #
BASE_KEYS = {"监管文件编号", "doc_no", "eff_status", "file_src", "real_year",
             "title", "year_reported"}
FINAL_KEYS = BASE_KEYS | {"cluster", "source_origin", "src_mark"}
# matched/citerefs 顶层为 dict{监管文件编号: 记录}；行内**含**「监管文件编号」键（R9 生效后
# 实测一致，F-D11：原注释"行内不再重复"与数据不符，已按实测补入必选键集）。
MATCHED_KEYS = {"监管文件编号", "body", "body_len", "docno", "lib", "title"}
CITEREFS_KEYS = {"监管文件编号", "art_refs", "basis", "body_len", "lib",
                 "name_refs_top", "title"}

# ============ clause_index 条文产物契约（②，2026-09-08） ============
# 产物：modules/regulatory_scrapers/data/clauses/{src}_clauses_{date}.jsonl（每行一份文件条款）
# 键集为程序消费硬契约：三层（文件行 / articles / chapters）须逐字节一致，新增维度须同步本契约与
# build/validate。枚举评估：本产物无真"受控枚举"字段——数值(no/count/article_index)+标识(dedup_key/source_url)
# +半结构(document_number)+自由文本(title/number/body)；半结构形态（第X条/文号）由抽取归一+校验约束，不入受控枚举表。
CLAUSE_LINE_FIELDS: tuple[str, ...] = (
    "dedup_key", "source_url", "document_number", "title",
    "chapter_count", "article_count", "chapters", "articles",
)
CLAUSE_ARTICLE_FIELDS: tuple[str, ...] = ("no", "number", "body")
CLAUSE_CHAPTER_FIELDS: tuple[str, ...] = ("no", "title", "article_index")

# 富内容对象轨（2026-09-09 rich_object；raw/cleaned JSONL 行内轨，不入 CSV 39 列）：
#   写者 = 采集/摄取侧 rich_object_fields（docx/doc/xlsx 图形/公式/图片），pipeline 逐行透传。
RICH_OBJECT_KEYS: tuple[str, ...] = ("rich_structured", "rich_text", "rich_count")

# ============ 附件对象字段契约（F-D07，2026-09-12） ============
# 背景：五源 attachments 字段长期 5 套并存（attachment_name/name/file_name；url/file_url；
# char_count/text_length/size_bytes；ocr_status/fetch_status/extract_status）。
# 契约（读取侧归一，写侧保留原始字段防历史重写；发布件 external_attachments 即规范形态）：
ATTACHMENT_FIELDS: tuple[str, ...] = ("file_name", "kind", "local_path", "sha256",
                                      "bytes", "text_len", "url")
ATTACHMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "file_name": ("file_name", "name", "attachment_name"),
    "kind": ("kind", "mime", "file_type"),
    "local_path": ("local_path", "content_ref", "path"),
    "sha256": ("sha256",),
    "bytes": ("bytes", "size_bytes", "content_length"),
    "text_len": ("text_len", "char_count", "text_length"),
    "url": ("url", "file_url", "download_url"),
}


def attachment_view(att: dict) -> dict:
    """附件对象 → 规范字段视图（F-D07 读取侧归一；缺失字段为 ""）。消费端一律经此读附件。"""
    out = {}
    for en, aliases in ATTACHMENT_ALIASES.items():
        v = ""
        for a in aliases:
            if isinstance(att, dict) and att.get(a) not in (None, ""):
                v = att[a]
                break
        out[en] = v
    return out


# ============ 数据双轨权威声明（F-D09，2026-09-12） ============
# 权威轨：cleaned **JSONL**（字段全集：含 _metadata/_raw_fields/富内容轨/表格结构化/附件对象）。
# 检索投影：cleaned **CSV**（39 列子集，仅检索/人工浏览用；非程序消费事实源）。
# 规则：① 程序消费一律读 JSONL（经发布件/clean_index）；② 人工在 CSV 的改动不得反向覆盖
# JSONL（历史教训：CSV 行对齐回写的兼容轨已收敛到 writeback_source 单点）；③ 两轨差异以
# JSONL 为准，validate_files 的 sha 校验以 JSONL 为主键（CSV 为辅）。
