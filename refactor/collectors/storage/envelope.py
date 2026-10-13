# -*- coding: utf-8 -*-
"""采集器异构 payload → 统一信封（canonical envelope）。

各源字段命名不一致（正文 pbc=content / gov=full_text / mof=content_text / nfra=content；
链接 pbc|gov|nfra=detail_url / mof=detail_link|api_link；文号 pbc|gov=document_number /
mof=doc_no / nfra=document_no；发文机关 pbc|gov=issuing_authority / mof=issue_organ 等）。

策略：保留原始字段（不破坏各采集器自身的断点续跑读取），并**补上**规范字段，使下游
clean 只需认一套（content / detail_url / document_number / issuing_authority /
publish_date / summary / fetch_status / title / category）。
"""
from typing import Any, Dict

from common.doc_number import normalize_doc_number


def _first(item: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


# ── 治理步骤（采集边界，非采集逻辑）：规范信封（contract normalization）──
def canonicalize(namespace: str, item: dict) -> dict:
    """把任意源的 payload 映射为统一信封（保留原始字段）。"""
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = dict(item)  # 保留全部原始字段

    out["title"] = item.get("title") or ""
    out["category"] = _first(item, "category", "category_name") or out.get("category") or ""
    out["detail_url"] = (
        _first(item, "detail_url", "url", "detail_link", "api_link") or ""
    )
    out["publish_date"] = _first(item, "publish_date", "build_date") or ""
    out["document_number"] = normalize_doc_number(
        _first(item, "document_number", "doc_no", "document_no") or ""
    )
    out["issuing_authority"] = (
        _first(item, "issuing_authority", "issue_organ", "doc_source") or ""
    )
    content = _first(item, "content", "full_text", "content_text", "rich_text")
    out["content"] = content or ""
    out["summary"] = item.get("summary") or ""
    # fetch_status：源自带则保留；否则由正文是否存在推导
    fs = item.get("fetch_status")
    if fs in (None, ""):
        fs = "ok" if content else "no_content"
    out["fetch_status"] = fs
    return out
