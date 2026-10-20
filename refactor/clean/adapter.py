# -*- coding: utf-8 -*-
"""adapter — raw/信封记录 → clean 统一 schema 的映射入口（**源无关**）。

Phase 2：消除旧版五源 ``map_*`` 的 5× 重复（每个源各写一整套 39 字段字面量）。
现在所有源共用同一个 ``_build_record`` 装配器，源之间的差异收敛为 ``_SPECS``
声明式方言表——绝大多数字段（body→content、detail_url…）已由采集侧 canonicalize
在入库入口归一成统一字段，clean 直接读，不再按源分支；仅少数 canonicalize 未覆盖的
原始字段名（issue_organ / category 的源相关别名）与真正的源相关行为
（nfra 正文去噪、pbc 的 local_path、gov/mof 的索引号回退）留在 ``_SPECS``。

设计要点（保证对账零漂移）：
  - adapter 读取的是采集侧**保留的原始字段**（信封 canonicalize 会保留原始字段），
    因此无论输入是裸 raw 还是信封，映射结果一致；
  - body / detail_url 读统一字段（canonicalize 产出），issue_organ / category 按源
    方言读（canonicalize 的 unified 列表未覆盖这些原始别名，强改会丢已入库旧值）；
  - 装配逻辑、字段读取顺序、dedup_key 配方与旧 ``map_*`` 逐字段等价；
  - 文号解析统一走 ``schema._resolve_doc_number``（早已源无关）。
"""
from typing import Any

from .schema import (
    _mk_meta,
    _resolve_doc_number,
    build_dedup_key,
    canonical_source,
    dedup_webpage_lead,
    empty_str,
    strip_nfra_url_fragments,
)


def _pick(rec: dict[str, Any], spec: Any) -> Any:
    """从 rec 按 spec 取值；spec 可为字段名或字段名元组（取首个非空）。"""
    if isinstance(spec, str):
        spec = (spec,)
    for k in spec:
        v = rec.get(k)
        if v not in (None, ""):
            return v
    return ""


# 残留方言（仅两类）：
#  - issue_organ / category 按源读：因 canonicalize 的 unified 列表未覆盖 mof 的 issue_org、
#    mof 的 category_name、nfra 的 category_type；若强改读统一字段会让已入库旧记录丢值
#    （不在本次回刷范围），故保留按源映射。
#  - index_spec / body_preclean / downloaded_* / att_* / dedup_index_or_url / raw_fields
#    为真正的源相关行为。
# 其余字段（body→content、detail_url）已由 canonicalize 在入库入口归一成统一字段，clean
# 直接读，不再按源分支——新增数据源（如内部文档）无需在 _SPECS 登记即可被 clean 处理。
_SPECS: dict[str, dict[str, Any]] = {
    "gov": {
        "issue_organ": "issue_organ",
        "category": "category",
        "index_spec": ("__resolve__", "bbbs"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": None,
        "att_text_fallback": True,
        "att_join_fallback": True,
        "dedup_index_or_url": True,
        "raw_fields": ("bbbs", "pub_date_original"),
    },
    "mof": {
        "issue_organ": "issue_org",
        "category": "category_name",
        "index_spec": ("__resolve__", "id"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": None,
        "att_text_fallback": False,
        "att_join_fallback": True,
        "dedup_index_or_url": False,
        "raw_fields": ("id", "category_id", "create_dept", "expire_date",
                       "abolish_date", "fetch_time", "active", "change"),
    },
    "nfra": {
        "issue_organ": "issuing_authority",
        "category": ("category", "category_type"),
        "index_spec": ("index_no", "doc_id"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": ("doc_file_url", "pdf_file_url"),
        "att_text_fallback": False,
        "att_join_fallback": True,
        "dedup_index_or_url": False,
        "raw_fields": ("doc_id", "build_date", "category_type"),
        "body_preclean": "nfra",
    },
    "pbc": {
        "issue_organ": "issuing_authority",
        "category": "category",
        "index_spec": ("__resolve__",),
        "downloaded_doc_path": "local_path",
        "downloaded_doc_url": "source_url",
        "att_text_fallback": False,
        "att_join_fallback": False,
        "dedup_index_or_url": True,
        "raw_fields": ("link_type", "file_type", "local_path", "fetch_status", "error"),
    },
}

# 默认方言：未知 source（如内部文档）的兜底。body / detail_url 已由 canonicalize 统一，
# 直接读；issue_organ / category 按统一字段名读取（新增源也应产出统一字段）。新增数据源
# 只要产出统一字段，clean 自动可处理——消除「加新源要改映射表」这个坑。
_DEFAULT_DIALECT: dict[str, Any] = {
    "issue_organ": "issuing_authority",
    "category": "category",
    "index_spec": ("__resolve__",),
    "downloaded_doc_path": None,
    "downloaded_doc_url": None,
    "att_text_fallback": False,
    "att_join_fallback": False,
    "dedup_index_or_url": False,
    "raw_fields": (),
}


def _body_preclean(name: str, text: str) -> str:
    if name == "nfra":
        return dedup_webpage_lead(strip_nfra_url_fragments(text))
    return text


def _index_no(rec: dict[str, Any], spec: dict[str, Any]) -> str:
    parts = spec["index_spec"]
    if parts and parts[0] == "__resolve__":
        idx = _resolve_doc_number(rec)
        if idx:
            return idx
        return empty_str(_pick(rec, parts[1:]))
    return empty_str(_pick(rec, parts))


def _attachment_count(rec: dict[str, Any], source: str) -> int:
    atts = rec.get("attachments") or []
    if source == "nfra":
        return int(len(atts))
    if source == "mof":
        return int(rec.get("attachment_count") or rec.get("file_count") or 0)
    if source == "pbc":
        return int(rec.get("attachment_count") or len(atts))
    return int(rec.get("attachment_count") or 0)


def _build_record(
    rec: dict[str, Any], source: str, spec: dict[str, Any], clean_version: str, captured_at: str
) -> dict[str, Any]:

    src = canonical_source(rec.get("source")) or source
    raw_source = empty_str(rec.get("source"))

    # body / detail_url 已由 canonicalize 在入库入口归一成统一字段，直接读。
    body_raw = empty_str(rec.get("content"))
    body = empty_str(_body_preclean(spec.get("body_preclean", ""), body_raw))

    index_no = _index_no(rec, spec)
    # issue_organ / category 仍按源方言读（canonicalize 的 unified 列表未覆盖这些原始字段名）。
    issue_organ = empty_str(rec.get(spec["issue_organ"]))
    source_url = empty_str(rec.get("detail_url"))
    category = empty_str(_pick(rec, spec["category"]))

    # 附件正文聚合：attachment_content 优先；gov/mof/nfra 额外回退 attachment_text 与
    # attachments[].text 聚合；pbc 不回退聚合（与旧 map_pbc 逐字段等价）。
    att = rec.get("attachment_content")
    if not att and spec.get("att_text_fallback"):
        att = rec.get("attachment_text")
    if not att and spec.get("att_join_fallback"):
        att = "\n\n".join(
            str(a.get("text") or "")
            for a in (rec.get("attachments") or [])
            if isinstance(a, dict) and a.get("text")
        )
    attachment_content = empty_str(att)

    if spec["downloaded_doc_path"]:
        downloaded_doc_path = empty_str(rec.get(spec["downloaded_doc_path"]))
    else:
        downloaded_doc_path = ""
    if spec["downloaded_doc_url"] == "source_url":
        downloaded_doc_url = source_url
    elif spec["downloaded_doc_url"]:
        downloaded_doc_url = empty_str(_pick(rec, spec["downloaded_doc_url"]))
    else:
        downloaded_doc_url = ""

    dedup_first = index_no or source_url if spec["dedup_index_or_url"] else index_no

    # _raw_fields：源专有字段保留（溯源）
    _raw: dict[str, Any] = {
        k: rec.get(k) for k in spec["raw_fields"] if rec.get(k) is not None
    }
    if raw_source and raw_source != src:
        _raw["子源"] = raw_source

    m = _mk_meta(rec, source_url, src, clean_version, captured_at)

    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "doc_type": "",
        "category": category,
        "publish_date": empty_str(rec.get("publish_date")),
        "effective_date": empty_str(rec.get("effective_date")),
        "issue_organ": issue_organ,
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
        "status": "",
        "timeliness_status": "",
        "replacement_document": "",
        "verification_source": "",
        "keyword": "",
        "attachments": rec.get("attachments") or [],
        "attachment_content": attachment_content,
        "attachment_count": _attachment_count(rec, source),
        "table_structured": rec.get("table_structured") or [],
        "table_raw_text": empty_str(rec.get("table_raw_text")),
        "table_recovery_method": empty_str(rec.get("table_recovery_method")),
        "downloaded_doc_path": downloaded_doc_path,
        "downloaded_doc_url": downloaded_doc_url,
        "body_source": "webpage",
        "dedup_key": build_dedup_key(dedup_first, source_url, rec.get("publish_date")),
        # scope：文档内/外部标记（内部文档=internal；历史外部数据缺省=external，向后兼容）
        "scope": empty_str(rec.get("scope")) or "external",
        "_metadata": m,
        "_raw_fields": _raw,
    }


def to_clean(
    rec: dict[str, Any],
    source: str,
    *,
    clean_version: str = "v1.0.0",
    captured_at: str = "",
) -> dict[str, Any]:
    """把一条 raw/信封记录映射为 clean 统一 schema 记录（源无关）。"""
    # 未知 source（如内部文档）自动走 _DEFAULT_DIALECT（统一字段 + 默认方言），
    # 无需在 _SPECS 登记字段映射。
    spec = _SPECS.get(source, _DEFAULT_DIALECT)
    return _build_record(rec, source, spec, clean_version, captured_at)


def to_clean_batch(records: list[dict[str, Any]], source: str, **kw) -> list[dict[str, Any]]:
    return [to_clean(r, source, **kw) for r in records]


if __name__ == "__main__":  # 离线自检（含 S-3 枚举校验）
    from .schema import (
        assert_enum_bindings,
        canonical_source,
        canonical_timeliness_status,
    )

    # 该用例模拟「canonicalize 在入库入口已归一出字段」后的 mof 记录：
    # 统一字段 content / detail_url（由 content_text / detail_link 产出），
    # 原始字段 issue_org / category_name 保留（canonicalize 未归并这两项）。
    rec = to_clean(
        {
            "id": "1",
            "title": "测试办法",
            "doc_no": "财会〔2024〕1号",
            "publish_date": "2024-01-01",
            "category_name": "部门规章",
            "content": "第一条 为规范……",
            "issue_org": "财政部",
            "detail_url": "https://fgk.mof.gov.cn/a",
            "source": "mof",
        },
        "mof",
        clean_version="v1.0",
        captured_at="2026-08-19",
    )
    assert rec["index_no"] == "财会〔2024〕1号"
    assert rec["dedup_key"]
    assert rec["body_text"] == "第一条 为规范……"
    assert_enum_bindings()
    assert canonical_source("xzfgk") == "gov"
    assert canonical_source("flk") == "gov"
    assert canonical_source("fgk.mof.gov.cn") == "mof"
    assert canonical_source("nfra") == "nfra"
    assert canonical_source("某未知源") == ""
    assert canonical_timeliness_status("N/A") == "uncertain"
    assert canonical_timeliness_status("已废止") == "repealed"
    assert canonical_timeliness_status("valid") == "valid"
    assert canonical_timeliness_status("") == ""
    assert canonical_timeliness_status("火星值") == ""
    print("[clean.adapter] 离线自检通过（含 S-3 枚举校验）")
