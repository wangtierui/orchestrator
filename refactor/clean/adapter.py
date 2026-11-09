# -*- coding: utf-8 -*-
"""adapter — raw/规范记录 → clean 统一 schema 的映射入口（**源无关**）。

Phase 2：消除旧版五源 ``map_*`` 的 5× 重复（每个源各写一整套 39 字段字面量）。
现在所有源共用同一个 ``_build_record`` 装配器。公共字段（content / detail_url /
issuing_authority / category / document_number …）的契约就是 ``raw_docs`` 的物理列，
clean 按列名直读，**没有任何「源 → 字段名」映射表**；``_POLICY`` 里只剩
真正的源相关清洗策略（索引号回退、附件路径、nfra 正文去噪等）。

设计要点（保证对账零漂移）：
  - adapter 读取的是按 ``init/ddl.py`` 表列组装的记录，字段结构不在本模块定义；
  - 所有源一律读同一组统一字段，不按源翻译字段名；
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


# 源相关「策略」（不是字段契约）：字段一律按 raw_docs 列名直读，这里只描述
# 索引号回退、附件路径、正文去噪等无法从表结构推导的行为。
_POLICY: dict[str, dict[str, Any]] = {
    "gov": {
        "index_spec": ("__resolve__", "bbbs"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": None,
        "att_text_fallback": True,
        "att_join_fallback": True,
        "raw_fields": ("bbbs", "pub_date_original"),
    },
    "mof": {
        "index_spec": ("__resolve__", "id"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": None,
        "att_text_fallback": False,
        "att_join_fallback": True,
        "raw_fields": ("id", "category_id", "create_dept", "expire_date",
                       "abolish_date", "fetch_time", "active", "change"),
    },
    "nfra": {
        "index_spec": ("index_no", "doc_id"),
        "downloaded_doc_path": None,
        "downloaded_doc_url": ("doc_file_url", "pdf_file_url"),
        "att_text_fallback": False,
        "att_join_fallback": True,
        "raw_fields": ("doc_id", "build_date", "category_type"),
        "body_preclean": "nfra",
    },
    "pbc": {
        "index_spec": ("__resolve__",),
        "downloaded_doc_path": "local_path",
        "downloaded_doc_url": "source_url",
        "att_text_fallback": False,
        "att_join_fallback": False,
        "raw_fields": ("link_type", "file_type", "local_path", "fetch_status", "error"),
    },
}

# 未知 source（如内部文档）的兜底策略：字段仍按统一列名读，无需登记任何映射。
_DEFAULT_POLICY: dict[str, Any] = {
    "index_spec": ("__resolve__",),
    "downloaded_doc_path": None,
    "downloaded_doc_url": None,
    "att_text_fallback": False,
    "att_join_fallback": False,
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

    # 正文：content_ref 指向的本地文件读回后落在 content_text，列名 content 兜底。
    body_raw = empty_str(rec.get("content") or rec.get("content_text"))
    body = empty_str(_body_preclean(spec.get("body_preclean", ""), body_raw))

    index_no = _index_no(rec, spec)
    # 公共字段的契约就是 raw_docs 的物理列，按列名直读，不做源别名翻译。
    issue_organ = empty_str(rec.get("issuing_authority"))
    source_url = empty_str(rec.get("detail_url"))
    category = empty_str(rec.get("category"))

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

    # _raw_fields：源专有字段保留（溯源）
    _raw: dict[str, Any] = {
        k: rec.get(k) for k in spec["raw_fields"] if rec.get(k) is not None
    }
    if raw_source and raw_source != src:
        _raw["子源"] = raw_source

    m = _mk_meta(rec, source_url, src, clean_version, captured_at)

    # 精简 schema（PG 硬约束）：只保留有下游消费者的字段 + attachment_content。
    # 其余（空占位/反规范化透传/_raw_fields/attachments 数组）不再进入 clean。
    return {
        "index_no": index_no,
        "title": empty_str(rec.get("title")),
        "document_number": _resolve_doc_number(rec),
        "detail_url": source_url,
        "source": src,
        "content": body,
        "attachment_content": attachment_content,
        "publish_date": empty_str(rec.get("publish_date")),
        "issuing_authority": issue_organ,
        "category": category,
        "dedup_key": build_dedup_key(source_url),
    }


def to_clean(
    rec: dict[str, Any],
    source: str,
    *,
    clean_version: str = "v1.0.0",
    captured_at: str = "",
) -> dict[str, Any]:
    """把一条按 DDL 列组装的 raw 记录映射为 clean 统一 schema 记录（源无关）。"""
    # 未知 source（如内部文档）走默认策略；字段一律按统一列名读，无需登记映射。
    spec = _POLICY.get(source, _DEFAULT_POLICY)
    return _build_record(rec, source, spec, clean_version, captured_at)


def to_clean_batch(records: list[dict[str, Any]], source: str, **kw) -> list[dict[str, Any]]:
    return [to_clean(r, source, **kw) for r in records]


if __name__ == "__main__":  # 离线自检（含 S-3 枚举校验）
    from .schema import (
        assert_enum_bindings,
        canonical_source,
        canonical_timeliness_status,
    )

    # 该用例模拟按 raw_docs 列名组装后的 mof 记录（统一字段名即物理列名）。
    rec = to_clean(
        {
            "id": "1",
            "title": "测试办法",
            "document_number": "财会〔2024〕1号",
            "publish_date": "2024-01-01",
            "category": "部门规章",
            "content": "第一条 为规范……",
            "issuing_authority": "财政部",
            "detail_url": "https://fgk.mof.gov.cn/a",
            "source": "mof",
        },
        "mof",
        clean_version="v1.0",
        captured_at="2026-08-19",
    )
    assert rec["index_no"] == "财会〔2024〕1号"
    assert rec["dedup_key"]
    assert rec["content"] == "第一条 为规范……"
    assert rec["detail_url"] == "https://fgk.mof.gov.cn/a"
    assert rec["issuing_authority"] == "财政部"
    assert rec["category"] == "部门规章"
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
