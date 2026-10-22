# -*- coding: utf-8 -*-
"""规范记录（canonical record）契约与采集边界校验层。

== 规范字段 SSOT（单一事实源）==
采集器在解析时必须**直接吐出**以下规范字段名。源私有字段名不在此列——映射已前移到
各采集器的解析逻辑中（例如 mof 的 lwh→document_number、lbbdw→issuing_authority；
nfra 的 document_no→document_number、category_type→category；gov 的 issue_organ→
issuing_authority、full_text→content 等）。

规范字段          含义                      类型/约束
key*              源内唯一 ID（hash 成 key_md5，落库去重）  由采集器 key_of 提供，不入本字典
title             标题                      str
detail_url        详情链接                  str
publish_date      发布日期 YYYY-MM-DD       str
effective_date    施行日期 YYYY-MM-DD       str
document_number   文号（经 normalize_doc_number 规范化） str
issuing_authority 发文机关                  str
category          分类                      str
content           正文全文（统一键名；源别名 full_text/content_text 等已在采集器内归并） str
summary           题注/摘要                 str
fetch_status      抓取状态                  str（见 lib.fetch_status）
attachments       附件元数据                list[dict]（落 raw_{ns}.attachments）
extra             源私有剩余字段            dict（落 raw_{ns}.extra 或 raw_docs 之外）

* key 由采集器 key_of 提供，不写在记录里。

== canonicalize 的职责 ==
canonicalize 只做**薄校验层**：补全规范字段默认、规范化 document_number、推导
fetch_status；它**不再**做源私有名→规范名的翻译（那一步在采集器解析时已完成）。若仍
见到遗留源私有键，仅告警、不静默翻译——以免数据在共享层被悄悄改名而丢失。

== 真正「只在采集时、不落 PG」的是映射代码；字段名本身即等于 raw_docs 的列名
（契约 = 列名 = 规范名，三者合一），这才是统一的终点。 ==
"""
from typing import Any, Dict

from common.doc_number import normalize_doc_number

# 规范字段契约（既是对采集器的接口说明书，也直接对应 raw_docs 的物理列名）。
CANONICAL_FIELDS = (
    "title", "detail_url", "publish_date", "effective_date",
    "document_number", "issuing_authority", "category", "content",
    "summary", "fetch_status",
)
# 已前移到采集器映射的源私有别名；若 canonicalize 仍见到，说明该采集器没改干净（告警）。
# 注意：build_date / api_link 等是合法的 raw_{ns} 子表列名，不算遗留别名，不在此列。
# 已前移到采集器映射的源私有别名；若 canonicalize 仍见到，说明该采集器没改干净（告警）。
# 注意：build_date / api_link 等是合法的 raw_{ns} 子表列名；rich_text 是附件富文本
# （flatten_attachment_extras 产出），均不算遗留别名，不在此列。
LEGACY_ALIASES = frozenset({
    "doc_no", "document_no", "issue_organ", "issue_org", "doc_source",
    "full_text", "content_text", "detail_link",
    "category_name", "category_type",
})


def _norm_text(v: Any) -> str:
    return v if isinstance(v, str) and v.strip() else ""


def canonicalize(namespace: str, item: dict) -> dict:
    """薄校验层：补全规范字段默认、规范化文号、推导 fetch_status。

    不再做源私有名→规范名翻译（那一步已在采集器解析时完成）。若仍见到遗留源私有键，
    仅告警、不静默翻译，以免数据在共享层被悄悄改名而丢失。
    """
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = dict(item)  # 保留全部字段（含子表列/extra 兜底）

    # 遗留源私有键告警（契约未被采集器完全兑现的信号）
    legacy = LEGACY_ALIASES & set(out.keys())
    if legacy:
        import logging
        logging.getLogger("canonical").warning(
            "[%s] 采集器仍吐源私有字段 %s，应在解析时映射为规范字段",
            namespace, sorted(legacy))

    out["title"] = _norm_text(item.get("title"))
    out["category"] = _norm_text(item.get("category"))
    out["detail_url"] = _norm_text(item.get("detail_url"))
    out["publish_date"] = _norm_text(item.get("publish_date"))
    out["effective_date"] = _norm_text(item.get("effective_date"))
    out["document_number"] = normalize_doc_number(_norm_text(item.get("document_number")))
    out["issuing_authority"] = _norm_text(item.get("issuing_authority"))
    out["content"] = _norm_text(item.get("content"))
    out["summary"] = _norm_text(item.get("summary"))
    # fetch_status：源自带则保留；否则由正文是否存在推导
    fs = item.get("fetch_status")
    if fs in (None, ""):
        fs = "ok" if out["content"] else "no_content"
    out["fetch_status"] = fs
    return out
