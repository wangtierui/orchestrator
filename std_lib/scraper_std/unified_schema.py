# -*- coding: utf-8 -*-
"""
unified_schema.py —— 统一元数据 Schema 与五源项目字段映射（第五/六/七节）

统一输出字段对齐 GB/T 42147-2022《政府网站网页电子文件元数据》核心元数据集：
  公文标识（索引号/文种/发文时间）、内容描述（标题/正文/主题分类）、
  格式信息（数据格式/文件格式）、管理信息（栏目名称/专题名称/生成更新时间）。

五源覆盖：gov / mof / nfra / pbc（原生映射器）+ supplementary（map_supp，2026-08-28 纳入；
supp 项目以 normalize_raw 一致化 + 委托 map_supp 的方式复用本映射，字段一致化留在 supp 项目）。

每条清洗后记录含 _metadata 顶层对象（抓取时间、源URL、清洗版本号、OCR是否存疑、
表格恢复方法、正文文档来源、缺失字段、version、last_modified）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from typing import Any
from collections.abc import Callable

# R5（2026-09-08）：受控枚举唯一事实源上收为 config/enums.py，本模块 re-export。
# 确保仓库根在 sys.path（config 为顶级包）：脚本直接运行本模块时自动插入。
_ORCH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
from config.enums import (  # noqa: E402,F401
    BODY_SOURCE,
    SOURCE_ALIASES,
    SOURCE_SET,
    SUPP_SOURCE_CHANNEL,
    TIMELINESS_STATUS,
)

from .doc_number import (
    extract_from_title,
    extract_from_body,
    in_abolish_context,
    looks_like_doc_number,
    normalize_doc_number,
    rebuild_multi_org_docno,
)

LOG = logging.getLogger("scraper_std.unified_schema")


def _resolve_doc_number(rec: dict[str, Any]) -> str:
    """统一文号解析（2026-09-05 五源接入 doc_number 模块）：

    1) 取原始文号字段（document_number / doc_no / document_no）并规范化
       （半角 [ ] → 全角〔〕、去脏、压缩）；
    2) 规范化结果不具文号形态（空/纯编号/占位）→ 从标题抽取内嵌文号兜底
       （如『关于印发《X办法》的通知（银发〔1999〕407号）』）。
    """
    raw = (rec.get("document_number") or rec.get("doc_no")
           or rec.get("document_no") or "")
    body = empty_str(rec.get("body_text") or rec.get("content"))
    title = empty_str(rec.get("title"))
    dn = normalize_doc_number(raw)
    if dn and looks_like_doc_number(dn):
        # 2026-09-07 case 9 及同类 32 例：raw 文号若实为被本文件废止的他文文号
        # （正文「《旧文件》（旧文号）…同时废止」语境），判定不可信 → 不采用，
        # 继续走标题/正文兜底。判据见 doc_number.in_abolish_context。
        if not (body and in_abolish_context(body, dn)):
            # case 7：多机关联合公告以标题机构链重建欠抓的前缀（保守，仅机构数更多时）
            return rebuild_multi_org_docno(dn, title)
    title_dn = extract_from_title(title)
    if title_dn:
        return rebuild_multi_org_docno(title_dn, title)
    # 兜底：原始文号与标题均无文号形态时，从正文发布语境抽取
    # （如『（2001年11月23日国务院令第324号发布）』→ 中华人民共和国国务院令第324号）
    return extract_from_body(body)

# --------------------------------------------------------------------------- #
# 共享受控枚举常量 —— R5：re-export 自 config/enums.py（《三项目状态码值统一规范》v3
# 唯一事实源上收后，本文件不再定义字面量，仅导出同名符号保持旧 import 兼容）。
# 新增值须先登记 config/enums.py，再经 gate_enum_values 校验。
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# 统一 Schema 定义（JSON Schema 风格，供 schema_validation 使用）
# --------------------------------------------------------------------------- #
UNIFIED_SCHEMA: dict[str, Any] = {
    "index_no": {"type": "string", "required": True},
    "title": {"type": "string", "required": True},
    "doc_type": {"type": "string"},
    "category": {"type": "string"},
    "publish_date": {"type": "datetime"},
    "effective_date": {"type": "datetime"},
    "issue_organ": {"type": "string"},
    "document_number": {"type": "string"},
    "source_url": {"type": "url"},
    # 枚举字段一律绑定上方受控常量（S-3）：禁止在此处写字面量，防枚举漂移
    "source": {"type": "enum", "enum_values": sorted(SOURCE_SET)},
    "body_text": {"type": "string", "required": True},
    "summary": {"type": "string"},
    "data_format": {"type": "string"},
    "mime_type": {"type": "string"},
    "column_name": {"type": "string"},
    "theme_name": {"type": "string"},
    "status": {"type": "string"},
    "timeliness_status": {"type": "enum", "enum_values": sorted(TIMELINESS_STATUS)},
    "replacement_document": {"type": "string"},
    "verification_source": {"type": "string"},
    "keyword": {"type": "string"},
    "attachment_count": {"type": "int"},
    "dedup_key": {"type": "string", "required": True},
    # 附件/正文文档/表格（可选，类型宽松）
    "attachments": {"type": "array"},
    "attachment_content": {"type": "string"},
    "attachment_content_path": {"type": "string"},
    "attachment_content_md5": {"type": "string"},
    "table_structured": {"type": "array"},
    "table_raw_text": {"type": "string"},
    "table_recovery_method": {"type": "string"},
    "downloaded_doc_path": {"type": "string"},
    "downloaded_doc_url": {"type": "url"},
    "body_source": {"type": "enum", "enum_values": sorted(BODY_SOURCE)},
    "raw_uncut_text": {"type": "string"},
    "split_sentences": {"type": "array"},
    "renamed_filename": {"type": "string"},
    "body_text_webpage": {"type": "string"},
    "body_text_doc": {"type": "string"},
    "_metadata": {"type": "object"},
    "_raw_fields": {"type": "object"},
}

# 空值阈值监测核心字段（第九节）
CORE_NULL_FIELDS = ["index_no", "title", "body_text"]


def build_dedup_key(*parts: Any) -> str:
    """唯一业务主键：索引号 + URL 哈希 + 发布日期（第十一节）。"""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def empty_str(v: Any) -> str:
    return "" if v is None else str(v)


# --------------------------------------------------------------------------- #
# 四项目字段映射器
# --------------------------------------------------------------------------- #
def _mk_meta(rec: dict[str, Any], source_url: str, source: str, clean_version: str,
             captured_at: str = "") -> dict[str, Any]:
    return {
        "source_url": source_url or "",
        "source": source,
        "captured_at": captured_at or "",
        "clean_version": clean_version,
        "ocr_uncertain": False,
        "table_recovery_method": "",
        "doc_source": "webpage",
        "missing_fields": [],
        "version": 1,
        "last_modified": "",
    }


def map_gov(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    index_no = _resolve_doc_number(rec) or empty_str(rec.get("bbbs") or "")
    source_url = empty_str(rec.get("detail_url"))
    # S-3：source 归一为五源标识；gov 抓取层子源标识（xzfgk/flk）保留至 _raw_fields.子源
    raw_source = empty_str(rec.get("source"))
    src = canonical_source(raw_source) or "gov"
    m = _mk_meta(rec, source_url, src, clean_version, captured_at)
    body = empty_str(rec.get("full_text") or rec.get("summary"))
    _raw = {k: rec.get(k) for k in (
        "bbbs", "sxx_label", "flxz", "pub_date_original") if rec.get(k) is not None}
    if raw_source and raw_source != src:
        _raw["子源"] = raw_source
    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "doc_type": "",
        "category": empty_str(rec.get("category")),
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": empty_str(rec.get("issue_organ")),
        "document_number": _resolve_doc_number(rec),
        "source_url": source_url,
        "source": src,
        "body_text": body,
        "body_text_webpage": body,
        "body_text_doc": "",
        "summary": empty_str(rec.get("summary")),
        "data_format": "TXT",
        "mime_type": "text/plain",
        "column_name": "",
        "theme_name": "",
        "status": empty_str(rec.get("sxx_label") or rec.get("flxz") or ""),
        "timeliness_status": "",
        "replacement_document": "",
        "verification_source": "",
        "keyword": "",
        "attachments": rec.get("attachments") or [],
        "attachment_content": empty_str(rec.get("attachment_text") or ""),
        "attachment_count": int(rec.get("attachment_count") or 0),
        "table_structured": [],
        "table_raw_text": "",
        "table_recovery_method": "",
        "downloaded_doc_path": "",
        "downloaded_doc_url": "",
        "body_source": "webpage",
        "dedup_key": build_dedup_key(index_no or source_url, source_url,
                                     rec.get("publish_date")),
        "_metadata": m,
        "_raw_fields": _raw,
    }


def map_mof(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    index_no = _resolve_doc_number(rec) or empty_str(rec.get("id"))
    source_url = empty_str(rec.get("detail_link") or rec.get("api_link"))
    # S-3：source 归一为五源标识；mof 原始站点域名保留至 _raw_fields.子源
    raw_source = empty_str(rec.get("source"))
    src = canonical_source(raw_source) or "mof"
    m = _mk_meta(rec, source_url, src, clean_version, captured_at)
    body = empty_str(rec.get("content_text"))
    _raw = {k: rec.get(k) for k in (
        "id", "category_id", "create_dept", "expire_date", "abolish_date",
        "fetch_time", "active", "change") if rec.get(k) is not None}
    if raw_source and raw_source != src:
        _raw["子源"] = raw_source
    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "doc_type": "",
        "category": empty_str(rec.get("category_name")),
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": empty_str(rec.get("issue_org")),
        "document_number": _resolve_doc_number(rec),  # mof doc_no → 统一解析
        "source_url": source_url,
        "source": src,
        "body_text": body,
        "body_text_webpage": body,
        "body_text_doc": "",
        "summary": empty_str(rec.get("summary")),
        "data_format": "TXT",
        "mime_type": "text/plain",
        "column_name": "",
        "theme_name": "",
        "status": empty_str(rec.get("status")),
        "timeliness_status": "",
        "replacement_document": "",
        "verification_source": "",
        "keyword": "",
        "attachments": rec.get("attachments") or [],
        "attachment_content": "",
        "attachment_count": int(rec.get("attachment_count") or rec.get("file_count") or 0),
        "table_structured": [],
        "table_raw_text": "",
        "table_recovery_method": "",
        "downloaded_doc_path": "",
        "downloaded_doc_url": "",
        "body_source": "webpage",
        "dedup_key": build_dedup_key(index_no, source_url, rec.get("publish_date")),
        "_metadata": m,
        "_raw_fields": _raw,
    }


# nfra 站点特有去噪：正文尾部混入的页面 URL 残渣（导航/分享链接文本，非正文内容）
# 例：…l?docId=1263688&itemId=915&generaltype=0 / …ItemDetail.html?docId=1251732&itemId=915
# 前缀仅允许 URL 安全字符（禁含中文标点，避免误吞句末 。）
_NFRA_URL_FRAG = re.compile(
    r"(?:https?://[A-Za-z0-9_\-./:%]*)?"
    r"(?:ItemDetail\.html|ItemDetail\.aspx|index\.html|detail|l)"
    r"\?docId=\d+&itemId=\d+(?:&generaltype=\d+)?[\s\u3000，,。]?",
    re.I)


def strip_nfra_url_fragments(text: str) -> str:
    """移除 nfra 正文尾部站点 URL 残渣（7.1 去噪：页面导航/页脚无关文本）。
    仅剥离空白，保留合法句末标点（。！？等）。"""
    if not text:
        return text
    t = _NFRA_URL_FRAG.sub("", text)
    return t.strip(" \n\t\u3000")


def map_nfra(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    index_no = empty_str(rec.get("index_no") or rec.get("doc_id"))
    source_url = empty_str(rec.get("detail_url"))
    m = _mk_meta(rec, source_url, "nfra", clean_version, captured_at)
    body = strip_nfra_url_fragments(empty_str(rec.get("content")))
    doc_url = empty_str(rec.get("doc_file_url") or rec.get("pdf_file_url") or "")
    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "doc_type": "",
        "category": empty_str(rec.get("category") or rec.get("category_type")),
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": empty_str(rec.get("issuing_authority")),
        "document_number": _resolve_doc_number(rec),  # nfra document_no → 统一解析
        "source_url": source_url,
        "source": "nfra",
        "body_text": body,
        "body_text_webpage": body,
        "body_text_doc": "",
        "summary": empty_str(rec.get("summary")),
        "data_format": "TXT",
        "mime_type": "text/plain",
        "column_name": "",
        "theme_name": "",
        "status": "",
        "timeliness_status": "",
        "replacement_document": "",
        "verification_source": "",
        "keyword": "",
        "attachments": rec.get("attachments") or [],
        "attachment_content": "",
        "attachment_count": int(len(rec.get("attachments") or [])),
        "table_structured": [],
        "table_raw_text": "",
        "table_recovery_method": "",
        "downloaded_doc_path": "",
        "downloaded_doc_url": doc_url,
        "body_source": "webpage",
        "dedup_key": build_dedup_key(index_no, source_url, rec.get("publish_date")),
        "_metadata": m,
        "_raw_fields": {k: rec.get(k) for k in (
            "doc_id", "build_date", "category_type") if rec.get(k) is not None},
    }


def map_pbc(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    index_no = _resolve_doc_number(rec)
    source_url = empty_str(rec.get("detail_url"))
    m = _mk_meta(rec, source_url, "pbc", clean_version, captured_at)
    body = empty_str(rec.get("content"))
    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "doc_type": "",
        "category": empty_str(rec.get("category")),
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": empty_str(rec.get("issuing_authority")),
        "document_number": _resolve_doc_number(rec),
        "source_url": source_url,
        "source": "pbc",
        "body_text": body,
        "body_text_webpage": body,
        "body_text_doc": "",
        "summary": empty_str(rec.get("summary")),
        "data_format": "TXT",
        "mime_type": "text/plain",
        "column_name": "",
        "theme_name": "",
        "status": empty_str(rec.get("fetch_status")),
        "timeliness_status": "",
        "replacement_document": "",
        "verification_source": "",
        "keyword": "",
        "attachments": [],
        "attachment_content": "",
        "attachment_count": 0,
        "table_structured": [],
        "table_raw_text": "",
        "table_recovery_method": "",
        "downloaded_doc_path": empty_str(rec.get("local_path")),
        "downloaded_doc_url": source_url,
        "body_source": "webpage",
        "dedup_key": build_dedup_key(index_no or source_url, source_url,
                                     rec.get("publish_date")),
        "_metadata": m,
        "_raw_fields": {k: rec.get(k) for k in (
            "link_type", "file_type", "local_path", "fetch_status", "error")
            if rec.get(k) is not None},
    }


# --------------------------------------------------------------------------- #
# 第五源：supplementary（补充法规库）映射器 —— 纳入统一 schema（2026-08-28）
# 说明：supp 项目 utils/supp_mapper.py 的 map_supp 改为「normalize_raw 一致化(I3-I7)
#        + 委托本函数」；本函数仅做纯字段映射，不依赖 supp 项目（共享库可独立存在）。
# --------------------------------------------------------------------------- #
_DOWNLOADED_HINTS = ("downloaded_doc", "本地", "docx", "pdf", "扫描", "ocr", "文本层")
_BOTH_HINTS = ("网页与文档", "网页+文档", "both", "双版本", "网页正文与文档版并存")


def canonical_body_source(raw: Any) -> str:
    """将任意 body_source 取值归并为 'webpage' / 'downloaded_doc' / 'both'（规范 v3 3.4）。"""
    s = str(raw or "").strip().lower()
    if not s:
        return "webpage"
    if any(h in s for h in _BOTH_HINTS):
        return "both"
    if any(h in s for h in _DOWNLOADED_HINTS):
        return "downloaded_doc"
    return "webpage"


# --------------------------------------------------------------------------- #
# S-3 枚举映射层：其余受控枚举的归并函数（与 canonical_body_source 同一模式）
# 注：SOURCE_ALIASES / SUPP_SOURCE_CHANNEL 已 R5 上收至 config/enums.py 并 re-export，
# 此处不再本地定义（防双源漂移）；TIMELINESS_ALIASES 为历史台账中文兼容映射保留于本层。
# --------------------------------------------------------------------------- #
def canonical_source(raw: Any) -> str:
    """将任意 source 取值归并为五源标识（规范 v3 3.5）。

    已知子源/域名统一归并到 nfra/pbc/mof/gov/supp；**无法识别时返回空串**（由调用方
    决定回退策略），绝不臆造源标识。空串在 validate_record 中按「未填」跳过 enum 校验。
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in SOURCE_SET:
        return low
    return SOURCE_ALIASES.get(low, "")


# 时效状态归并表：占位值/中文 → 规范 7 值
TIMELINESS_ALIASES: dict[str, str] = {
    "n/a": "uncertain", "na": "uncertain", "none": "uncertain", "null": "uncertain",
    "未知": "uncertain", "未核验": "uncertain", "不确定": "uncertain",
    "现行有效": "valid", "有效": "valid",
    "已废止": "repealed", "废止": "repealed",
    "已失效": "expired", "失效": "expired",
    "修订": "amended", "部分废止": "partially_repealed",
    "待定": "pending", "尚未施行": "pending",
}


def canonical_timeliness_status(raw: Any) -> str:
    """将任意 timeliness_status 取值归并为规范 7 值；无法归并返回空串（表示未核验）。

    占位值 N/A 归并为 uncertain（不确定），而非丢弃——既去除非法值，又不掩盖
    「该记录尚未完成效力核验」这一事实；空串在 validate_record 中跳过 enum 校验。
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in TIMELINESS_STATUS:
        return low
    return TIMELINESS_ALIASES.get(low) or TIMELINESS_ALIASES.get(s, "")


def assert_enum_bindings() -> None:
    """S-3 枚举一致性自检：UNIFIED_SCHEMA 的 enum_values 必须与受控常量严格一致。

    防止 schema 声明与常量漂移（历史上 body_source 的 enum_values 为硬编码字面量，
    与 BODY_SOURCE 常量存在不一致风险）。不一致即抛 AssertionError，供门禁与自检调用。
    """
    assert set(UNIFIED_SCHEMA["source"]["enum_values"]) == SOURCE_SET, \
        "source enum_values 与 SOURCE_SET 不一致"
    assert set(UNIFIED_SCHEMA["timeliness_status"]["enum_values"]) == TIMELINESS_STATUS, \
        "timeliness_status enum_values 与 TIMELINESS_STATUS 不一致"
    assert set(UNIFIED_SCHEMA["body_source"]["enum_values"]) == BODY_SOURCE, \
        "body_source enum_values 与 BODY_SOURCE 不一致"


def map_supp(rec: dict[str, Any], clean_version: str, captured_at: str = "") -> dict[str, Any]:
    """补充法规记录 → 统一 Schema（与 map_gov/map_nfra 同形）。

    注意：supp 项目在 run_clean_pipeline 启动时用「normalize_raw + 本函数」的包装
    覆盖 MAPPERS["supp"]，保证字段一致化后进入统一映射；本函数纯映射、可独立调用。
    """
    doc_no = empty_str(rec.get("document_number") or rec.get("task_index"))
    source_url = empty_str(rec.get("source_url"))
    src_raw = empty_str(rec.get("source")) or "gov.cn补充"
    src_channel = SUPP_SOURCE_CHANNEL.get(src_raw, "gov_website")  # source 归并五源标识（v3）
    m = _mk_meta(rec, source_url, "supp", clean_version, captured_at)
    body = empty_str(rec.get("body_text"))
    body_src = canonical_body_source(rec.get("body_source"))
    m["doc_source"] = body_src
    if rec.get("_retrieval_channel"):
        m["retrieval_channel"] = rec["_retrieval_channel"]
    m["missing_fields"] = [f for f in ("index_no", "title", "body_text")
                           if not empty_str(rec.get(f)) and f == "body_text" and not body]
    data_format = "PDF" if body_src == "downloaded_doc" else "TXT"
    mime_type = "application/pdf" if body_src == "downloaded_doc" else "text/plain"
    _raw = dict(rec.get("_raw_fields") or {})
    if rec.get("task_index") and "task_index" not in _raw:
        _raw["task_index"] = rec["task_index"]
    _raw["补充途径"] = src_channel
    _raw.setdefault("补充途径原文", src_raw)
    return {
        "index_no": doc_no,
        "title": empty_str(rec.get("title")),
        "doc_type": empty_str(rec.get("doc_type")),
        "category": empty_str(rec.get("category")),
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": empty_str(rec.get("issue_organ")),
        "document_number": _resolve_doc_number(rec),
        "source_url": source_url,
        "source": "supp",
        "body_text": body,
        "body_text_webpage": body if body_src == "webpage" else "",
        "body_text_doc": body if body_src == "downloaded_doc" else "",
        "summary": empty_str(rec.get("summary")),
        "data_format": data_format,
        "mime_type": mime_type,
        "column_name": empty_str(rec.get("column_name")),
        "theme_name": empty_str(rec.get("theme_name")),
        "status": empty_str(rec.get("status")),
        "timeliness_status": empty_str(rec.get("timeliness_status")) or "valid",
        "replacement_document": empty_str(rec.get("replacement_document")),
        "verification_source": empty_str(rec.get("verification_source")),
        "keyword": empty_str(rec.get("keyword")),
        "attachments": rec.get("attachments") or [],
        "attachment_content": empty_str(rec.get("attachment_content")),
        "attachment_count": int(rec.get("attachment_count") or len(rec.get("attachments") or [])),
        "table_structured": rec.get("table_structured") or [],
        "table_raw_text": empty_str(rec.get("table_raw_text")),
        "table_recovery_method": empty_str(rec.get("table_recovery_method")),
        "downloaded_doc_path": empty_str(rec.get("downloaded_doc_path")),
        "downloaded_doc_url": empty_str(rec.get("downloaded_doc_url")),
        "body_source": body_src,
        "dedup_key": build_dedup_key(doc_no, source_url, rec.get("publish_date")),
        "raw_uncut_text": "",
        "split_sentences": [],
        "renamed_filename": "",
        "_metadata": m,
        "_raw_fields": _raw,
    }


MAPPERS: dict[str, Callable[[dict[str, Any], str, str], dict[str, Any]]] = {
    "gov": map_gov,
    "mof": map_mof,
    "nfra": map_nfra,
    "pbc": map_pbc,
    "supp": map_supp,
}

# 数据字典（交付物 3 附带）
DATA_DICTIONARY: dict[str, dict[str, str]] = {
    "index_no": {"含义": "索引号（政府信息唯一标识；无则 URL MD5 前 8 位）", "类型": "string"},
    "title": {"含义": "信息标题", "类型": "string"},
    "doc_type": {"含义": "文件类型标识（受控三档 ≈62 值：法定文种 16/法规类型 6/其他 40，令→命令、法→法律；提取失败→pending；规范 v3）", "类型": "string"},
    "category": {"含义": "效力位阶（受控 13 级：constitution/law/judicial_interpretation/admin_regulation/local_regulation/autonomous_regulation/dept_rule/local_government_rule/state_council_normative/dept_normative/local_government_normative/industry_rule/other；规范 v3）", "类型": "string"},
    "publish_date": {"含义": "发文/公布时间", "类型": "datetime YYYY-MM-DD"},
    "effective_date": {"含义": "生效日期", "类型": "datetime YYYY-MM-DD"},
    "issue_organ": {"含义": "发布机构", "类型": "string"},
    "document_number": {"含义": "文号", "类型": "string"},
    "source_url": {"含义": "来源详情链接", "类型": "url"},
    "source": {"含义": "数据源标识", "类型": "string"},
    "body_text": {"含义": "正文全文（清洗后，含断句修复）", "类型": "string"},
    "body_text_webpage": {"含义": "网页版正文（正文文档差异时保留）", "类型": "string"},
    "body_text_doc": {"含义": "正文文档版全文（差异时保留）", "类型": "string"},
    "summary": {"含义": "摘要", "类型": "string"},
    "data_format": {"含义": "数据格式（TXT/DOC/PDF）", "类型": "string"},
    "mime_type": {"含义": "文件格式（MIME）", "类型": "string"},
    "column_name": {"含义": "栏目名称", "类型": "string"},
    "theme_name": {"含义": "专题名称", "类型": "string"},
    "status": {"含义": "有效性状态（规范 v3：由 timeliness_status 派生英文值，中文仅展示层）", "类型": "string"},
    "timeliness_status": {"含义": "时效状态（受控 7 值：valid/amended/repealed/partially_repealed/expired/pending/uncertain；规范 v3）", "类型": "string"},
    "replacement_document": {"含义": "现行替代文件标题（已过期记录指向替代版本，无则空）", "类型": "string"},
    "verification_source": {"含义": "核验来源（北大法宝/数据源标注/规则判断）", "类型": "string"},
    "keyword": {"含义": "关键词", "类型": "string"},
    "attachment_count": {"含义": "附件数量", "类型": "int"},
    "attachments": {"含义": "附件元数据列表（文件名/URL/路径/MD5/状态/字数）", "类型": "array[object]"},
    "attachment_content": {"含义": "附件全文文本（多附件用 | 分隔）", "类型": "string"},
    "attachment_content_path": {"含义": "超大附件全文 .txt 相对路径", "类型": "string"},
    "attachment_content_md5": {"含义": "超大附件全文 MD5", "类型": "string"},
    "table_structured": {"含义": "表格结构化二维数组 list[list[str]]", "类型": "array[array[string]]"},
    "table_raw_text": {"含义": "表格原始提取文本（兜底）", "类型": "string"},
    "table_recovery_method": {"含义": "表格恢复方法（structured/raw_only）", "类型": "string"},
    "downloaded_doc_path": {"含义": "正文文档下载路径（6.2）", "类型": "string"},
    "downloaded_doc_url": {"含义": "正文文档来源 URL", "类型": "url"},
    "body_source": {"含义": "正文来源（受控 3 值：webpage/downloaded_doc/both；规范 v3 3.4）", "类型": "enum"},
    "dedup_key": {"含义": "唯一业务主键（索引号+URL哈希+发布日期）", "类型": "string"},
    "raw_uncut_text": {"含义": "无标点兜底切分时的原文留存（7.2④）", "类型": "string"},
    "split_sentences": {"含义": "无标点长文兜底切分短句列表（7.2④）", "类型": "array[string]"},
    "renamed_filename": {"含义": "标准重命名后的主文档/附件文件名（6.4/7.4）", "类型": "string"},
    "_metadata": {"含义": "元数据（抓取时间/清洗版本/OCR存疑/表格恢复方法/缺失字段/version）", "类型": "object"},
    "_raw_fields": {"含义": "源记录专有字段保留（溯源；supp 补充 task_index/检索词/检索途径/OCR缓存等）", "类型": "object"},
}

# CSV 列顺序（稳定可复现）
CSV_COLUMNS = [
    "index_no", "title", "doc_type", "category", "publish_date", "effective_date",
    "issue_organ", "document_number", "source_url", "source", "body_text",
    "body_text_webpage", "body_text_doc", "summary", "data_format", "mime_type",
    "column_name", "theme_name", "status", "timeliness_status",
    "replacement_document", "verification_source", "keyword", "attachment_count",
    "attachment_content", "attachment_content_path", "attachment_content_md5",
    "table_structured", "table_raw_text", "table_recovery_method",
    "downloaded_doc_path",     "downloaded_doc_url", "body_source", "dedup_key",
    "raw_uncut_text", "split_sentences", "renamed_filename",
    "_metadata", "_raw_fields",
]


if __name__ == "__main__":  # 离线自检
    rec = map_mof({"id": "1", "title": "测试办法", "doc_no": "财会〔2024〕1号",
                   "publish_date": "2024-01-01", "category_name": "部门规章",
                   "content_text": "第一条 为规范……", "issue_org": "财政部",
                   "detail_link": "https://fgk.mof.gov.cn/a"}, "v1.0", "2026-08-19")
    assert rec["index_no"] == "财会〔2024〕1号"
    assert rec["dedup_key"]
    assert rec["body_text"] == "第一条 为规范……"
    # S-3：枚举绑定一致性 + canonical 归并函数自检
    assert_enum_bindings()
    assert canonical_source("xzfgk") == "gov"          # gov 行政法规库子源
    assert canonical_source("flk") == "gov"            # gov 国家法律法规库子源
    assert canonical_source("fgk.mof.gov.cn") == "mof"
    assert canonical_source("nfra") == "nfra"
    assert canonical_source("某未知源") == ""            # 无法识别→空，不臆造
    assert canonical_timeliness_status("N/A") == "uncertain"
    assert canonical_timeliness_status("已废止") == "repealed"
    assert canonical_timeliness_status("valid") == "valid"
    assert canonical_timeliness_status("") == ""
    assert canonical_timeliness_status("火星值") == ""    # 无法归并→空（未核验）
    assert len(CSV_COLUMNS) == len(set(CSV_COLUMNS))
    print("[scraper_std.unified_schema] 离线自检通过（含 S-3 枚举校验）")
