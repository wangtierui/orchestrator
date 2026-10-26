# -*- coding: utf-8 -*-
"""
schema.py —— 统一元数据 Schema 与受控枚举归并（clean 阶段数据层）。

历史背景：本模块早期内含五源 ``map_*`` 字段映射器（map_gov/mof/nfra/pbc/supp），
已在 Phase 2 折叠到 ``clean.adapter``（源无关、声明式字段表）。
本模块现在只负责两类职责：
  1) 统一 Schema 数据定义（UNIFIED_SCHEMA / CSV_COLUMNS / CORE_NULL_FIELDS），
     作为 clean 的单一契约来源；
  2) 受控枚举归并（canonical_source / canonical_timeliness_status）与 nfra 正文去噪工具（供 adapter 调用）。

每条清洗后记录含 _metadata 顶层对象（抓取时间、源URL、清洗版本号、OCR是否存疑、
表格恢复方法、缺失字段、version、last_modified）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from collections.abc import Callable
from typing import Any

# 原则1：受控枚举唯一事实源收口为 refactor/common/enums.py（脱根目录依赖，refactor 自包含）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REFACTOR_ROOT = os.path.dirname(_HERE)
if _REFACTOR_ROOT not in sys.path:
    sys.path.insert(0, _REFACTOR_ROOT)
from common.enums import (
    SOURCE_ALIASES,
    SOURCE_SET,
    TIMELINESS_STATUS,
)

from .core.doc_number import (
    extract_from_body,
    extract_from_title,
    in_abolish_context,
    looks_like_doc_number,
    normalize_doc_number,
    rebuild_multi_org_docno,
)

LOG = logging.getLogger("clean.schema")


def _resolve_doc_number(rec: dict[str, Any]) -> str:
    """统一文号解析（2026-09-05 五源接入 doc_number 模块）：

    1) 取原始文号字段（document_number / doc_no / document_no）并规范化
       （半角 [ ] → 全角〔〕、去脏、压缩）；
    2) 规范化结果不具文号形态（空/纯编号/占位）→ 从标题抽取内嵌文号兜底
       （如『关于印发《X办法》的通知（银发〔1999〕407号）』）。
    """
    raw = rec.get("document_number") or rec.get("doc_no") or rec.get("document_no") or ""
    body = empty_str(rec.get("content") or rec.get("body_text"))
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
# 统一 Schema 定义（JSON Schema 风格，供 schema_validation 使用）
# --------------------------------------------------------------------------- #
UNIFIED_SCHEMA: dict[str, Any] = {
    "index_no": {"type": "string", "required": True},
    "title": {"type": "string", "required": True},
    "doc_type": {"type": "string"},
    "category": {"type": "string"},
    "publish_date": {"type": "datetime"},
    "effective_date": {"type": "datetime"},
    "issuing_authority": {"type": "string"},
    "document_number": {"type": "string"},
    "detail_url": {"type": "url"},
    # 枚举字段一律绑定上方受控常量（S-3）：禁止在此处写字面量，防枚举漂移
    "source": {"type": "enum", "enum_values": sorted(SOURCE_SET)},
    "scope": {"type": "string"},  # 文档内/外部标记（internal / external）
    "content": {"type": "string", "required": True},
    "summary": {"type": "string"},
    "data_format": {"type": "string"},
    "mime_type": {"type": "string"},
    "column_name": {"type": "string"},
    "theme_name": {"type": "string"},
    "timeliness_status": {"type": "enum", "enum_values": sorted(TIMELINESS_STATUS)},
    "replacement_document": {"type": "string"},
    "verification_source": {"type": "string"},
    "keyword": {"type": "string"},
    "attachment_count": {"type": "int"},
    "dedup_key": {"type": "string", "required": True},
    # 附件/正文文档/表格（可选，类型宽松）
    "attachments": {"type": "array"},
    "attachment_content": {"type": "string"},
    "table_structured": {"type": "array"},
    "table_raw_text": {"type": "string"},
    "table_recovery_method": {"type": "string"},
    "downloaded_doc_path": {"type": "string"},
    "downloaded_doc_url": {"type": "url"},
    "raw_uncut_text": {"type": "string"},
    "split_sentences": {"type": "array"},
    "_metadata": {"type": "object"},
    "_raw_fields": {"type": "object"},
}

# 空值阈值监测核心字段（第九节）
CORE_NULL_FIELDS = ["index_no", "title", "content"]


def build_dedup_key(url: str = "") -> str:
    """去重标识键 = 存储层 key_md5 的同一派生（URL 归一 md5），与 storage 单一身份契约一致（原则7）。"""
    return hashlib.md5((url or "").encode("utf-8")).hexdigest()


def empty_str(v: Any) -> str:
    return "" if v is None else str(v)


def _mk_meta(
    rec: dict[str, Any], source_url: str, source: str, clean_version: str, captured_at: str = ""
) -> dict[str, Any]:
    return {
        "source_url": source_url or "",
        "source": source,
        "captured_at": captured_at or "",
        "clean_version": clean_version,
        "ocr_uncertain": False,
        "table_recovery_method": "",
        "missing_fields": [],
        "version": 1,
        "last_modified": "",
    }


# --------------------------------------------------------------------------- #
# _metadata 受控契约（C 收口）：所有清洗标记键集中在此，壳层不得私自发明键。
# --------------------------------------------------------------------------- #
METADATA_KEYS = frozenset(
    {
        "source_url",             # 来源详情链接
        "source",                 # 数据源标识
        "captured_at",            # 抓取时间
        "clean_version",          # 清洗版本号
        "ocr_uncertain",          # OCR 是否存疑（bool）
        "ocr_corrected",          # 字形替换次数
        "ocr_dict_fixed",         # 词典修正次数
        "table_recovery_method",  # 表格恢复方法（structured/raw_only/pending…）
        "missing_fields",         # 缺失字段列表
        "version",                # 记录版本号
        "last_modified",          # 最后修改时间
    }
)


def annotate(rec: dict[str, Any], **fields) -> dict[str, Any]:
    """把清洗标记写入 ``rec["_metadata"]``，仅允许 :data:`METADATA_KEYS` 内的键。

    未知键直接抛 ``KeyError``，杜绝壳层随意发明 metadata 字段（横切关注点收敛到一处）。
    ``value`` 为 ``None`` 的键被忽略（不覆盖已有值）。返回同一 ``rec``，便于链式调用。
    """
    if not fields:
        return rec
    for k in fields:
        if k not in METADATA_KEYS:
            raise KeyError(
                f"未知 _metadata 键：{k!r}（允许键：{sorted(METADATA_KEYS)}）"
            )
    meta = rec.setdefault("_metadata", {})
    for k, v in fields.items():
        if v is not None:
            meta[k] = v
    return rec


# --------------------------------------------------------------------------- #
# 受控枚举归并层（canonical_*）：将任意取值归并为规范值；无法归并→空串（未核验）。
# --------------------------------------------------------------------------- #
def canonical_source(raw: Any) -> str:
    """将任意 source 取值归并为五源标识（规范 v3 3.5）。

    已知子源/域名统一归并到 nfra/pbc/mof/gov；**无法识别时返回空串**（由调用方
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
    "n/a": "uncertain",
    "na": "uncertain",
    "none": "uncertain",
    "null": "uncertain",
    "未知": "uncertain",
    "未核验": "uncertain",
    "不确定": "uncertain",
    "现行有效": "valid",
    "有效": "valid",
    "已废止": "repealed",
    "废止": "repealed",
    "已失效": "expired",
    "失效": "expired",
    "修订": "amended",
    "部分废止": "partially_repealed",
    "待定": "pending",
    "尚未施行": "pending",
}


def canonical_timeliness_status(raw: Any) -> str:
    """将任意 timeliness_status 取值归并为规范 7 值；无法归并返回空串（表示未核验）。

    注：四源主链路 timeliness_status 恒为空（由下游回写阶段派生）；本函数保留供该阶段。
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

    防止 schema 声明与常量漂移（source / timeliness_status 的 enum_values 必须与
    受控常量逐字一致）。不一致即抛 AssertionError，供门禁与自检调用。
    """
    assert set(UNIFIED_SCHEMA["source"]["enum_values"]) == SOURCE_SET, (
        "source enum_values 与 SOURCE_SET 不一致"
    )
    assert set(UNIFIED_SCHEMA["timeliness_status"]["enum_values"]) == TIMELINESS_STATUS, (
        "timeliness_status enum_values 与 TIMELINESS_STATUS 不一致"
    )


# --------------------------------------------------------------------------- #
# nfra 站点特有正文去噪（供 adapter 在 nfra 正文装配前调用）
# --------------------------------------------------------------------------- #
# nfra 站点特有去噪：正文尾部混入的页面 URL 残渣（导航/分享链接文本，非正文内容）
_NFRA_URL_FRAG = re.compile(
    r"(?:https?://[A-Za-z0-9_\-./:%]*)?"
    r"(?:ItemDetail\.html|ItemDetail\.aspx|index\.html|detail|l)"
    r"\?docId=\d+&itemId=\d+(?:&generaltype=\d+)?[\s\u3000，,。]?",
    re.I,
)

# nfra 站点特有去噪②（2026-09-20 相邻项）：正文首部**重复导语**（主送机关整段重复两次）。
_DUP_LEAD_RE = re.compile(r"^(.{10,300}?[：:])\1", re.S)


def dedup_webpage_lead(text: str) -> str:
    """消除网页正文首部**重复导语**：同一段（≤300 字、以「：」结尾）连续出现两次时保留一份。

    仅作首部判定（`^` 锚定 + 定长 + 冒号结尾），不触碰正文中后部的合法重复表述。
    """
    if not text:
        return text
    m = _DUP_LEAD_RE.match(text)
    if not m:
        return text
    return (m.group(1) + text[m.end() :]).lstrip()


def strip_nfra_url_fragments(text: str) -> str:
    """移除 nfra 正文尾部站点 URL 残渣（7.1 去噪：页面导航/页脚无关文本）。

    仅剥离空白，保留合法句末标点（。！？等）。
    """
    if not text:
        return text
    t = _NFRA_URL_FRAG.sub("", text)
    return t.strip(" \n\t\u3000")


# --------------------------------------------------------------------------- #
# 数据字典（交付物 3 附带）
# --------------------------------------------------------------------------- #
DATA_DICTIONARY: dict[str, dict[str, str]] = {
    "index_no": {"含义": "索引号（政府信息唯一标识；无则 URL MD5 前 8 位）", "类型": "string"},
    "title": {"含义": "信息标题", "类型": "string"},
    "doc_type": {
        "含义": "文件类型标识（受控三档 ≈62 值：法定文种 16/法规类型 6/其他 40，令→命令、法→法律；提取失败→pending；规范 v3）",
        "类型": "string",
    },
    "category": {
        "含义": "效力位阶（受控 13 级：constitution/law/judicial_interpretation/admin_regulation/local_regulation/autonomous_regulation/dept_rule/local_government_rule/state_council_normative/dept_normative/local_government_normative/industry_rule/other；规范 v3）",
        "类型": "string",
    },
    "publish_date": {"含义": "发文/公布时间", "类型": "datetime YYYY-MM-DD"},
    "effective_date": {"含义": "生效日期", "类型": "datetime YYYY-MM-DD"},
    "issuing_authority": {"含义": "发布机构", "类型": "string"},
    "document_number": {"含义": "文号", "类型": "string"},
    "detail_url": {"含义": "来源详情链接", "类型": "url"},
    "source": {"含义": "数据源标识", "类型": "string"},
    "content": {"含义": "正文全文（清洗后，含断句修复）", "类型": "string"},
    "summary": {"含义": "摘要", "类型": "string"},
    "data_format": {"含义": "数据格式（TXT/DOC/PDF）", "类型": "string"},
    "mime_type": {"含义": "文件格式（MIME）", "类型": "string"},
    "column_name": {"含义": "栏目名称", "类型": "string"},
    "theme_name": {"含义": "专题名称", "类型": "string"},
    "timeliness_status": {
        "含义": "时效状态（受控 7 值：valid/amended/repealed/partially_repealed/expired/pending/uncertain；规范 v3）",
        "类型": "string",
    },
    "replacement_document": {
        "含义": "现行替代文件标题（已过期记录指向替代版本，无则空）",
        "类型": "string",
    },
    "verification_source": {"含义": "核验来源（北大法宝/数据源标注/规则判断）", "类型": "string"},
    "keyword": {"含义": "关键词", "类型": "string"},
    "attachment_count": {"含义": "附件数量", "类型": "int"},
    "attachments": {
        "含义": "附件元数据列表（文件名/URL/路径/MD5/状态/字数）",
        "类型": "array[object]",
    },
    "attachment_content": {"含义": "附件全文文本（多附件用 | 分隔）", "类型": "string"},
    "table_structured": {
        "含义": "表格结构化二维数组 list[list[str]]",
        "类型": "array[array[string]]",
    },
    "table_raw_text": {"含义": "表格原始提取文本（兜底）", "类型": "string"},
    "table_recovery_method": {"含义": "表格恢复方法（structured/raw_only）", "类型": "string"},
    "downloaded_doc_path": {"含义": "正文文档下载路径（6.2）", "类型": "string"},
    "downloaded_doc_url": {"含义": "正文文档来源 URL", "类型": "url"},
    "dedup_key": {"含义": "唯一业务主键（URL 归一 md5，与 storage key_md5 同源）", "类型": "string"},
    "raw_uncut_text": {"含义": "无标点兜底切分时的原文留存（7.2④）", "类型": "string"},
    "split_sentences": {"含义": "无标点长文兜底切分短句列表（7.2④）", "类型": "array[string]"},
    "_metadata": {
        "含义": "元数据（抓取时间/清洗版本/OCR存疑/表格恢复方法/缺失字段/version）",
        "类型": "object",
    },
    "_raw_fields": {
        "含义": "源记录专有字段保留（溯源）",
        "类型": "object",
    },
}

# CSV 列顺序（稳定可复现）
CSV_COLUMNS = [
    "index_no",
    "title",
    "doc_type",
    "category",
    "publish_date",
    "effective_date",
    "issuing_authority",
    "document_number",
    "detail_url",
    "source",
    "scope",
    "content",
    "summary",
    "data_format",
    "mime_type",
    "column_name",
    "theme_name",
    "timeliness_status",
    "replacement_document",
    "verification_source",
    "keyword",
    "attachment_count",
    "attachment_content",
    "table_structured",
    "table_raw_text",
    "table_recovery_method",
    "downloaded_doc_path",
    "downloaded_doc_url",
    "dedup_key",
    "raw_uncut_text",
    "split_sentences",
    "_metadata",
    "_raw_fields",
]


if __name__ == "__main__":  # 离线自检
    # S-3：枚举绑定一致性 + canonical 归并函数自检
    assert_enum_bindings()
    assert canonical_source("xzfgk") == "gov"  # gov 行政法规库子源
    assert canonical_source("flk") == "gov"  # gov 国家法律法规库子源
    assert canonical_source("fgk.mof.gov.cn") == "mof"
    assert canonical_source("nfra") == "nfra"
    assert canonical_source("某未知源") == ""  # 无法识别→空，不臆造
    assert canonical_timeliness_status("N/A") == "uncertain"
    assert canonical_timeliness_status("已废止") == "repealed"
    assert canonical_timeliness_status("valid") == "valid"
    assert canonical_timeliness_status("") == ""
    assert canonical_timeliness_status("火星值") == ""  # 无法归并→空（未核验）
    assert len(CSV_COLUMNS) == len(set(CSV_COLUMNS))
    # C：_metadata 受控键 + annotate 拒绝未知键
    _rec = {"_metadata": {}}
    annotate(_rec, clean_version="v1", ocr_uncertain=True)
    assert _rec["_metadata"]["clean_version"] == "v1"
    assert _rec["_metadata"]["ocr_uncertain"] is True
    try:
        annotate(_rec, not_a_real_key=1)
        raise AssertionError("annotate 应拒绝未知键")
    except KeyError:
        pass
    print("[clean.schema] 离线自检通过（含 S-3 枚举校验 + metadata 契约）")
