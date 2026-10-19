# -*- coding: utf-8 -*-
"""interfaces.worklist_fields — 待办条目的**展示字段派生**（单一事实源）。

背景（2026-10-19 用户反馈）
--------------------------
待办列表里 `relevance_boundary` 的 `subject` **部分是文号、部分是名称**
（生产者写 subject 的口径不统一），业务方无法判断"这条到底指哪份文件"。
用户要求按 kind 分别列示：

    relevance_boundary / timeliness_conflict / rfn_theme_uncertain
        ⇒ **名称、文号、访问地址**
    internal_unaligned
        ⇒ **名称、文号**

设计要点
--------
1. **不猜**：字段一律从 `payload` 的既有关键字取（各 kind 的键实测如下）；
   payload 没有的，才走**桥表反查**（`rfn_api.bridge_rows()` 的
   `登记时标题` / `登记时文号` / `source_url`，实测 939/943 非空）。
2. **降级可辨**：每个字段都带 `*_src` 说明来源，空值明确区分
   `not_applicable`（该 kind 无此概念）与 `not_found`（查不到）——
   绝不把"查不到"显示成空白（本仓反复踩过的坑：静默降级伪装成"无数据"）。
3. **汇总项单列**：`relevance_boundary` 有一类 `subject='summary:588'` 的
   **汇总条目**（payload 只有 `note`/`registered`/`total`，无单条文件）⇒
   不属于"某份文件"，须显式标为 `aggregate`，不能塞进名称列。
4. **internal_unaligned 的"文号"实际是 `ipn`**（内部制度编号 `IPN-…`）——
   该域没有外部文号概念。按"最接近文号的唯一标识"取 `ipn`，
   并在 `document_number_src` 标 `ipn`，让读者知道这不是外部文号。
"""
from __future__ import annotations

from typing import Any

#: **归一化单一事实源**（`std_lib/common_lib/norm.py`）。
#: 本模块只在它之上做两处**极小特化**（均有 `norm-specialization` 豁免标记与理由）：
#:   ① 标题先解码 HTML 实体（跨表匹配需要，SSOT 不解码）；
#:   ② 文号先把 `【】` 换成 `[]`（SSOT 的 `_DOC_CLEAN` 未覆盖该形态）。
#: 其余规则（去括号/书名号/空白/尾注、`N/A` 占位、尾部 `号`）**一律复用 SSOT**，
#: 避免两套归一口径分叉（门禁 `gate_no_duplicate_libs` 亦要求如此）。
from std_lib.common_lib.norm import norm_docno as _S_norm_docno
from std_lib.common_lib.norm import norm_title as _S_norm_title

#: 需要「名称 + 文号 + 访问地址」的 kind
NEEDS_URL = frozenset({"relevance_boundary", "timeliness_conflict",
                       "rfn_theme_uncertain"})

#: 只需「名称 + 文号」的 kind（该域无对外 URL）
NAME_NO = frozenset({"internal_unaligned"})

#: 全部受支持的 kind（前端据此决定列）
SUPPORTED = NEEDS_URL | NAME_NO


# norm-specialization: 标题归一**复用 SSOT**，仅前置解码 HTML 实体。
#   理由：桥表与 clean 产物的标题来自不同采集路径，个别含 `&lt;` 等实体
#   （实测 clean 里有 `&lt;` 形态），而 SSOT 的 `norm_title` 只去标点、不解码实体
#   （实测 `&lt;测试&gt;决定` → `&lt测试&gt决定`，实体残留会破坏跨表匹配）。
#   实体解码是**前置修正**，不复制 SSOT 的归一逻辑。
def _norm_title(s: str) -> str:
    """标题归一：前置实体解码 + SSOT `norm_title`。"""
    s = (s or "")
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                 ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return _S_norm_title(s)


# norm-specialization: 文号归一**复用 SSOT**，仅前置把中文方头括号 `【】` 换成 `[]`。
#   理由：SSOT 的 `_DOC_CLEAN` 覆盖 `〔〕（）[]` 但**未覆盖 `【】`**，
#   而 clean 产物里确实存在该形态（实测 `保监发【2014】19号`）。
#   先补正字符再归一，不复制 SSOT 的剥离规则。
def _norm_docno(s: str) -> str:
    """文号归一：前置 `【】` 补正 + SSOT `norm_docno`。"""
    s = (s or "").replace("【", "[").replace("】", "]")
    return _S_norm_docno(s)


class _Bridge:
    """访问地址反查索引（懒加载 + 一次建索引，避免 N 次全表扫）。

    **两级来源**（逐级降级，每级都可辨）：
      1. `rfn_clean_bridge.csv`（`登记时标题`/`登记时文号` → `source_url`）——
         只覆盖**已进 RFN 且已 reconcile** 的文件（939/943 有 URL）。
      2. **clean 产物**（`nfra_cleaned_*.jsonl` 等的 `title`/`document_number`
         → `source_url`）——覆盖**全部已采集**文件。
         为什么需要它：`register_doc` 手工登记的文件**没有桥记录**
         （实测 T3 登记的 RFN-d6063763945de936 桥表查不到），
         而 clean 里有它的 URL ⇒ 不建这层，这类条目的"访问地址"永远空白。
    """

    _by_title: dict[str, str] | None = None
    _by_docno: dict[str, str] | None = None

    @classmethod
    def _add(cls, by_t: dict[str, str], by_d: dict[str, str],
             title: str, docno: str, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        t = _norm_title(title)
        d = _norm_docno(docno)
        if t and t not in by_t:
            by_t[t] = url
        if d and d not in by_d:
            by_d[d] = url

    @classmethod
    def _build(cls) -> None:
        if cls._by_title is not None:
            return
        by_t: dict[str, str] = {}
        by_d: dict[str, str] = {}
        # ── 级 1：桥表 ──────────────────────────────────────────────
        try:
            from interfaces import rfn_api

            for r in rfn_api.bridge_rows():
                cls._add(by_t, by_d,
                         str(r.get("登记时标题") or ""),
                         str(r.get("登记时文号") or ""),
                         str(r.get("source_url") or ""))
        except Exception:  # noqa: BLE001  桥表不可读 ⇒ 降级到级 2（可辨）
            pass
        # ── 级 2：clean 产物 ────────────────────────────────────────
        try:
            import glob
            import json as _json
            import os as _os

            root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            pats = ("data/docs/cleaned/*_cleaned_*.jsonl",
                    "modules/regulatory_scrapers/data/cleaned/*_cleaned_*.jsonl")
            seen_files: set[str] = set()
            for pat in pats:
                for f in sorted(glob.glob(_os.path.join(root, pat)), reverse=True):
                    # 每个源只读**最新一份**（文件名含日期，倒序首见即最新）
                    src = _os.path.basename(f).split("_cleaned_")[0]
                    if src in seen_files:
                        continue
                    seen_files.add(src)
                    try:
                        with open(f, encoding="utf-8", errors="replace") as fh:
                            for ln in fh:
                                if '"source_url"' not in ln:
                                    continue
                                try:
                                    d = _json.loads(ln)
                                except ValueError:
                                    continue
                                cls._add(by_t, by_d,
                                         str(d.get("title") or ""),
                                         str(d.get("document_number") or ""),
                                         str(d.get("source_url") or ""))
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            pass
        cls._by_title, cls._by_docno = by_t, by_d

    @classmethod
    def url_for(cls, title: str, docno: str) -> tuple[str, str]:
        """返回 `(url, 依据)`；依据 ∈ {bridge:title, bridge:docno,
        clean:title, clean:docno, ''}。

        先按**文号**匹配（比标题稳定：标题常有书名号/废止尾缀差异），
        再按标题。桥表与 clean 合并成同一索引 ⇒ 无需区分优先级，
        但依据里保留来源以便追溯。
        """
        cls._build()
        t = _norm_title(title)
        d = _norm_docno(docno)
        if d and cls._by_docno and d in cls._by_docno:
            return cls._by_docno[d], "bridge:docno"
        if t and cls._by_title and t in cls._by_title:
            return cls._by_title[t], "bridge:title"
        return "", ""

    @classmethod
    def reset(cls) -> None:
        """清索引（测试用；桥表/clean 更新后需重建）。"""
        cls._by_title = cls._by_docno = None


def _payload(item: dict[str, Any]) -> dict:
    p = item.get("payload")
    return p if isinstance(p, dict) else {}


def _first(p: dict, *keys: str) -> str:
    for k in keys:
        v = p.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def derive(item: dict[str, Any]) -> dict[str, Any]:
    """为一条待办派生展示字段。

    返回 `{kind, aggregate, title, title_src, document_number,
    document_number_src, url, url_src, extra}`。

    `*_src` 取值：`payload:<键>` / `bridge:title` / `bridge:docno` /
    `ipn` / `subject` / `not_applicable`（该 kind 无此概念）/
    `not_found`（查不到）/ `aggregate`（汇总条目，无单条文件）。
    """
    kind = str(item.get("kind") or "")
    p = _payload(item)
    subject = str(item.get("subject") or "").strip()
    out: dict[str, Any] = {
        "kind": kind, "aggregate": False,
        "title": "", "title_src": "not_found",
        "document_number": "", "document_number_src": "not_applicable",
        "url": "", "url_src": "not_applicable",
        "extra": {},
    }

    # ── 汇总条目（relevance_boundary 的 `summary:NNN`）───────────────
    # 它不是"某份文件"，而是"超上限未逐条登记的汇总" ⇒ 单独标识。
    if "total" in p and "registered" in p and not _first(p, "title"):
        out.update({
            "aggregate": True,
            "title": f"汇总项（未逐条登记）：候选 {p.get('total')} 条，"
                     f"已登记 {p.get('registered')} 条",
            "title_src": "aggregate",
            "document_number_src": "not_applicable",
            "url_src": "not_applicable",
            "extra": {"total": p.get("total"),
                      "registered": p.get("registered"),
                      "note": str(p.get("note") or "")},
        })
        return out

    # ── 名称 ─────────────────────────────────────────────────────
    title = _first(p, "title", "文件名称", "name")
    if title:
        out["title"], out["title_src"] = title, "payload:title"
    else:
        # payload 无 title ⇒ 用 subject（但**标注来源**，让读者知道它可能是文号）
        out["title"], out["title_src"] = subject, "subject"

    # ── 文号 ─────────────────────────────────────────────────────
    docno = _first(p, "document_number", "发文字号", "docno")
    if docno:
        out["document_number"], out["document_number_src"] = docno, "payload:document_number"
    elif kind in NAME_NO:
        # 该域无外部文号 ⇒ 用内部编号 ipn（明确标注，不冒充外部文号）
        ipn = _first(p, "ipn")
        if ipn:
            out["document_number"], out["document_number_src"] = ipn, "ipn"
        else:
            out["document_number_src"] = "not_found"
    else:
        out["document_number_src"] = "not_found"

    # ── 访问地址 ─────────────────────────────────────────────────
    if kind in NEEDS_URL:
        url = _first(p, "url", "source_url", "访问地址")
        if url:
            out["url"], out["url_src"] = url, "payload:url"
        else:
            url2, how = _Bridge.url_for(out["title"], out["document_number"])
            if url2:
                out["url"], out["url_src"] = url2, how
            else:
                out["url_src"] = "not_found"
    # internal_unaligned 等 ⇒ not_applicable（该域无对外 URL）

    return out


def needs_url(kind: str) -> bool:
    return kind in NEEDS_URL


def decorate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给一批待办条目附加 `fields`（**不改原字典语义**，只加一个键）。"""
    out = []
    for it in items:
        d = dict(it)
        d["fields"] = derive(it)
        out.append(d)
    return out
