# -*- coding: utf-8 -*-
"""refactor.clause.pipeline — storage-native 条文抽取编排。

读 storage(namespace="cleaned") 的全量记录 → 逐条 parse_document →
validate_clauses → 写 storage(namespace="clauses")。

注意：clean 阶段产物使用自身 schema（正文=body_text、链接=source_url、
发文机关=issue_organ），并非采集器规范前字段名（content / detail_url /
issuing_authority）。本管线以 clean 实际字段为准，并兼容规范前字段名回退。

设计要点：
  - 源无关、backend-agnostic（file / postgres 通吃，统一走 collectors.storage.get_store）；
  - clause 是 cleaned 的**派生索引**，随 cleaned 变化而重建 → out_store 用
    on_conflict="update"（file 后端整文件覆盖天然等价；PG 下按 key 覆盖）；
  - 身份字段从 cleaned 记录透传（dedup_key / source / source_url 等），
    source_url(=detail_url) 同时作为 PG 端 key_md5 派生源（详见 collectors.storage.sql_backend._extract_key）；
  - **rfn 投影（方向 A：本阶段不做）**：本管线不引入 `interfaces.rfn_api` / RFN 桥表。
    RFN 是监管文件主数据 ID，属独立子系统（modules/regulatory_classifier/rfn），
    其 storage 化是单独的工程，不应塞进条文抽取。clause 行已透传全部锚点键
    （dedup_key / source_url(=detail_url) / document_number / title），将来 rfn 子系统
    就位后，一次 join 即可把 rfn 回填，**无需重解析**。
  - 增量/状态：clauses 是 cleaned 的派生索引，storage 后端天然幂等（file=整文件覆盖、
    PG=按 key 覆盖），无需旧 clause_index_state.json 式的独立状态机。
"""
from __future__ import annotations

from typing import Any, Dict, List

from collectors.storage import get_store
from collectors.storage.config import StorageSettings
from clause.core.document_structure import parse_document, validate_clauses


# clause 行从 cleaned 记录透传的身份/元数据字段。
# clean 阶段用自身 schema：链接=source_url、发文机关=issue_organ（非规范前字段名 detail_url/issuing_authority）。
# ⚠️ 这些字段同时是"将来 rfn 投影"的 join 句柄（方向 A：rfn 暂缺，锚点键先透传）：
#   dedup_key / source_url(=detail_url) → RFN↔clean 桥表锚点；
#   document_number / title             → 归属表兜底命中键。
_IDENTITY_FIELDS = (
    "dedup_key",
    "source",
    "title",
    "document_number",
    "publish_date",
    "effective_date",
    "category",
    "summary",
    "source_url",
    "issue_organ",
    "scope",
)


def _identity_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """从 cleaned 记录抽取 clause 行必备的身份/元数据字段。

    clean 产物用 source_url / issue_organ；采集器规范记录用 detail_url / issuing_authority。
    两者统一为同义字段透传（供 PG 去重键派生与 rfn 锚点），并保留 issue_organ 溯源。
    """
    row = {k: (rec.get(k) or "") for k in _IDENTITY_FIELDS}
    url = rec.get("source_url") or rec.get("detail_url") or ""
    row["source_url"] = url
    row["detail_url"] = url
    auth = rec.get("issuing_authority") or rec.get("issue_organ") or ""
    row["issuing_authority"] = auth
    row["issue_organ"] = rec.get("issue_organ") or auth
    return row


def build_clause_for_record(rec: Dict[str, Any]) -> Dict[str, Any] | None:
    """单条 cleaned 记录 → clause 行；无正文（body_text 为空，兼容 content）返回 None。"""
    content = rec.get("body_text") or rec.get("content") or ""
    if not content.strip():
        return None
    parsed = parse_document(content)
    row = _identity_fields(rec)
    structure = parsed.get("structure") or []
    row.update(
        {
            "mode": parsed["mode"],
            "score": parsed.get("score", {}),
            "is_fallback": parsed.get("is_fallback", False),
            "chapter_count": parsed["chapter_count"],
            "article_count": parsed["article_count"],
            "chapters": parsed["chapters"],
            "articles": parsed["articles"],
            "article_structure": parsed["article_structure"],
            "tail_marker": parsed.get("tail_marker", ""),
            "tail_cut": parsed.get("tail_cut", {}),
            "repair": parsed.get("repair", {}),
        }
    )
    # law 模式（主导）structure 恒为空 → 不落 structure/structure_count（冗余字段）；
    # 仅层级体（notice/bulletin/plan）模式有 structure 时才落。
    if structure:
        row["structure"] = structure
        row["structure_count"] = len(structure)
    # 方向 A：clause 暂不投影 rfn → 跳过 V007（RFN 有效性）校验，避免全量误报 WARN
    row["validation"] = validate_clauses(row, skip_rules=("V007",))
    return row


def build_clause_index(
    settings: StorageSettings | None = None,
    *,
    on_conflict: str = "update",
) -> Dict[str, Any]:
    """对 cleaned 全量做条文抽取并落库 clauses（storage-native，幂等）。

    返回汇总 {namespace, sources, total_records, parsed, skipped, clause_count, locator}。

    说明：
      - in_store 只读（load 返回该 namespace 当前全量并集，file/PG 语义一致）；
      - out_store 用 on_conflict="update"：同一 detail_url 的条款随正文变化覆盖；
        file 后端为整文件覆盖，天然等同于"每次重建"。
    """
    in_store = get_store("cleaned", settings, on_conflict="nothing")
    out_store = get_store("clauses", settings, on_conflict=on_conflict)

    cleaned = in_store.load()
    items = cleaned.items or []

    sources: Dict[str, int] = {}
    clause_items: List[Dict[str, Any]] = []
    parsed = skipped = 0
    for rec in items:
        src = rec.get("source") or "unknown"
        sources[src] = sources.get(src, 0) + 1
        row = build_clause_for_record(rec)
        if row is None:
            skipped += 1
            continue
        clause_items.append(row)
        parsed += 1

    locator = out_store.save(clause_items, on_conflict=on_conflict)
    return {
        "namespace": "clauses",
        "sources": sources,
        "total_records": len(items),
        "parsed": parsed,
        "skipped": skipped,
        "clause_count": len(clause_items),
        "locator": locator,
    }


if __name__ == "__main__":
    # 离线自检：在临时 file 后端跑一轮 mini 端到端。
    import json
    import tempfile

    tmp = tempfile.mkdtemp(prefix="clause_smoke_")
    s = StorageSettings(backend="file", raw_dir=tmp)
    sample = [
        {
            "source": "gov",
            "dedup_key": "gov-1",
            "source_url": "http://example.gov/1",
            "title": "测试规定",
            "document_number": "2020-1",
            "publish_date": "2020-01-01",
            "effective_date": "2020-02-01",
            "body_text": "第一章 总则\n第一条 为了规范，制定本办法。\n第二条 机构应当审慎。",
        },
        {"source": "gov", "dedup_key": "gov-2", "source_url": "", "title": "无正文", "body_text": ""},
    ]
    get_store("cleaned", s).save(sample, on_conflict="update")
    summary = build_clause_index(s)
    print("[clause] mini 端到端：", json.dumps(summary, ensure_ascii=False))
    assert summary["parsed"] == 1 and summary["skipped"] == 1, summary
    out = get_store("clauses", s).load()
    assert out.count == 1, out.count
    print("[clause] 自检通过：clauses 产物 =", out.count, "条")
