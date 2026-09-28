# -*- coding: utf-8 -*-
"""common_lib.clause_locator — 条款定位（N-49，2026-09-27）：把"文件级引用"升级为"条款级引用"。

定位两个方向
------------
① **源侧**（引用发起处）：关系行的 `source_offset`（字符偏移）→ 反查所属条款。
   · 内部制度：`processed/<ipn>_clauses.json` 的 `articles[]` + `_fulltext.json` 的全文；
   · 监管文件：clauses 的 `articles[]`（按 `dedup_key` 取）+ **该文档自己的正文**
     （`cleaned` 的 `body_text`，由调用方传入——不重复读盘）。
② **目标侧**（被引用处）：关系行的 `source_snippet` 中的『第 M 条』→ 目标文件的条款表
   （监管 clauses；按 `dst_ref`（RFN）**或** `dst_key`（cleaned dedup_key）取）。

动机
----
`relations_index` 现有 `article` 是**源侧且靠文本邻域推断**（实测 4195 行仅 545 行非空
= 13%），而 `source_snippet` 里的『第 M 条』（如"根据《民法典》第一百八十六条"）**被直接
丢弃**——目标侧原无任何条款字段。两侧条款表已是结构化 `articles[{no, number, body}]`，
可零猜测地补齐。

单一事实源（不另建副本）
------------------------
· 内部条款：`interfaces.internal_policy_api`（唯一入口读 processed）；
· 监管条款：`interfaces.clause_index_api.latest_clause_path`（唯一入口）；
· 条号形态：`articles[].number`（"第一百八十六条"）与 `[].no`（186）同源共存，
  `norm_article_no()` 只做形态归一（去『第/条』与空白），不引入第二套编号语义。

歧义纪律
--------
· 目标侧：snippet 中可能同时出现**源侧自身条号** → 只采用"能在目标文件条款表命中"且
  **唯一**的结果；多命中/未命中一律留空（宁缺勿错，保持可审计）。
· 源侧：offset 落在条款区间外（头部标题区等）→ 留空。
· 索引键：目标侧优先 `dst_ref`（RFN 强实体），退化 `dst_key`（cleaned 弱键）——二者均
  指向同一批 clauses 记录的两个键空间，**不合并**（保持"未登记 RFN"与"已登记"可分辨）。
"""

from __future__ import annotations

import json
import os
import re

# 条号抽取：中文数字（含"零〇两"）或阿拉伯数字；支持"之一"后缀（如"第一百八十六条之一"）
_ART_IN_SNIPPET = re.compile(
    r"第([一二三四五六七八九十百千零〇两\d]+)条(之[一二三四五六七八九十]+)?"
)
_NORM_STRIP = re.compile(r"^第|条$|之[一二三四五六七八九十]+$|\s")


def norm_article_no(number: str) -> str:
    """条号形态归一：去『第』『条』『之N』与空白 → 比对键（"第一百八十六条"→"一百八十六"）。

    刻意**不做**中文→阿拉伯转换：两侧（clauses / processed）的 `articles[].number` 本就
    用同一中文形态，字符串比对即零歧义；阿拉伯形态由 `_cn_to_int` 兜底。
    """
    return _NORM_STRIP.sub("", (number or "").strip())


def _cn_to_int(num: str) -> int:
    """中文/阿拉伯数字 → int（百千内；失败 0）。与 `document_structure._to_int` 同口径。"""
    if num.isdigit():
        return int(num)
    table = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    unit = {"十": 10, "百": 100, "千": 1000}
    total = cur = 0
    for ch in num:
        if ch in table:
            cur = table[ch]
        elif ch in unit:
            if cur == 0:
                cur = 1
            total += cur * unit[ch]
            cur = 0
        else:
            return 0
    return total + cur


# --------------------------------------------------------------------------- #
# 源侧：offset → 条号
# --------------------------------------------------------------------------- #
def article_no_to_int(number: str) -> int:
    """条号 → 整数（"第一百八十六条" → 186；无法解析 → 0）。供复核/报告判定"条号超范围"。"""
    return _cn_to_int(norm_article_no(number))


def build_article_spans(fulltext: str, articles: list[dict]) -> list[tuple[int, int, str]]:
    """条款在全文中的字符区间 `[(start, end, number)]`（按 `articles` 顺序逐个定位 `number`）。

    `_clauses.json` 的 `articles[]` 只有 `{no, number, body}`（无 offset），故以 `number`
    字面量在全文中的**顺序出现**为锚；定位失败（脏文本）则该条跳过（不猜位置）。
    """
    hits: list[tuple[int, str]] = []
    pos = 0
    for a in articles or []:
        num = (a.get("number") or "").strip()
        if not num:
            continue
        i = fulltext.find(num, pos)
        if i < 0:
            i = fulltext.find(num)
        if i < 0:
            continue
        hits.append((i, num))
        pos = i + len(num)
    out: list[tuple[int, int, str]] = []
    for k, (s, num) in enumerate(hits):
        e = hits[k + 1][0] if k + 1 < len(hits) else len(fulltext)
        out.append((s, e, num))
    return out


def locate_src_article(offset: int, spans: list[tuple[int, int, str]]) -> str:
    """字符偏移 → 所属条款号（区间外 → 空串）。"""
    if offset is None or offset < 0:
        return ""
    for s, e, num in spans:
        if s <= offset < e:
            return num
    return ""


def load_internal_spans(ipn: str) -> list[tuple[int, int, str]]:
    """内部制度 → 条款区间（经 `interfaces.internal_policy_api` 唯一入口读 processed）。

    非条文型制度（`articles` 为空，如"通知/函"类）→ 返回空表（无可定位单元，非错误）。
    """
    if not ipn:
        return []
    try:
        from interfaces import internal_policy_api as ipa

        api = ipa.get_internal_policy_api()
        arts = (api.load_processed(ipn, suffix="_clauses.json") or {}).get("articles") or []
        if not arts:
            return []
        text = (api.load_processed(ipn, suffix="_fulltext.json") or {}).get("text") or ""
        if not text:
            return []
        return build_article_spans(text, arts)
    except Exception:  # noqa: BLE001  定位为增强轨：失败不得中断关系抽取
        return []


def load_regulatory_spans(dedup_key: str, text: str, index: dict | None = None) -> list[tuple[int, int, str]]:
    """监管文件 → 条款区间（needs 该文档正文，由调用方从 cleaned 传入，不重复读盘）。

    非条文型（`articles` 空）或无正文 → 空表。
    """
    if not dedup_key or not text:
        return []
    idx = index if index is not None else load_regulatory_index()
    arts = (idx.get("articles") or {}).get(dedup_key) or []
    return build_article_spans(text, arts) if arts else []


# --------------------------------------------------------------------------- #
# 目标侧：snippet → 目标文件条号（监管 clauses 表）
# --------------------------------------------------------------------------- #
_INDEX: dict | None = None


def load_regulatory_index(
    sources: tuple[str, ...] | None = None,
) -> dict:
    """五源 clauses 的**一次性**索引（进程内单例）：

    ```
    {
      "by_rfn":   {rfn:       {"by_norm": {归一化条号: 原条号}, "by_no": {int: 原条号}}},
      "by_dedup": {dedup_key: {"by_norm": ..., "by_no": ...}},
      "articles": {dedup_key: [{"no": int, "number": str}, ...]},   # 仅条号（源侧 spans 用）
    }
    ```

    同一遍读取建三表：`by_rfn` 只收带 `rfn` 的记录（RFN 强实体）；`by_dedup` / `articles`
    收**全部**记录（弱键空间——`dst_key` 与监管源文档普遍只有 dedup_key）。读经
    `interfaces.clause_index_api`（唯一入口）。
    """
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    by_rfn: dict = {}
    by_dedup: dict = {}
    articles_by_dk: dict = {}
    try:
        # N-78（2026-09-28）：来源顺序取自 SSOT（原为本地 5 元组字面量）；本模块一贯
        # 采用**函数内懒引导**（与 `interfaces.*` 同处一个 try），故此处同步入。
        from config.enums import SOURCE_ORDER
        from interfaces import clause_index_api as ci

        for src in (sources or SOURCE_ORDER):
            p = ci.latest_clause_path(src)
            if not p or not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    arts = rec.get("articles") or []
                    if not arts:
                        continue
                    by_norm: dict = {}
                    by_no: dict = {}
                    brief: list = []
                    for a in arts:
                        num = (a.get("number") or "").strip()
                        if not num:
                            continue
                        by_norm.setdefault(norm_article_no(num), num)
                        no = a.get("no")
                        if isinstance(no, int):
                            by_no.setdefault(no, num)
                        brief.append({"no": no, "number": num})
                    if not by_norm:
                        continue
                    # N-59（2026-09-27）：附 `n_articles`（目标文件条款总数）——供复核判定
                    # "条号超出总数"（**必错配**的强信号，比"表内无此条"更确定）。
                    n_arts = rec.get("article_count") or len(brief)
                    dk = (rec.get("dedup_key") or "").strip()
                    if dk:
                        by_dedup.setdefault(
                            dk, {"by_norm": by_norm, "by_no": by_no, "n_articles": n_arts}
                        )
                        articles_by_dk.setdefault(dk, brief)
                    rfn = (rec.get("rfn") or "").strip()
                    if rfn:
                        by_rfn.setdefault(
                            rfn, {"by_norm": by_norm, "by_no": by_no, "n_articles": n_arts}
                        )
    except Exception:  # noqa: BLE001  定位为增强轨：失败降级为空表（字段留空）
        by_rfn, by_dedup, articles_by_dk = {}, {}, {}
    _INDEX = {"by_rfn": by_rfn, "by_dedup": by_dedup, "articles": articles_by_dk}
    return _INDEX


def locate_dst_article(snippet: str, key: str, index: dict | None = None) -> str:
    """snippet → 目标文件条号；**唯一命中**才返回（多义/未命中 → 空串）。

    `key` 可为 RFN（`dst_ref`，优先）或 cleaned dedup_key（`dst_key`，退化）。
    命中口径（双通道）：中文形态按 `norm_article_no` 比对；阿拉伯形态（如"第 186 条"）
    转 int 后按 `by_no` 比对。snippet 中若出现多个条号，只有恰有一个命中才采用。
    """
    if not snippet or not key:
        return ""
    idx = index if index is not None else load_regulatory_index()
    tab = (idx.get("by_rfn") or {}).get(key) or (idx.get("by_dedup") or {}).get(key)
    if not tab:
        return ""
    cands = set()
    for m in _ART_IN_SNIPPET.finditer(snippet):
        hit = tab["by_norm"].get(norm_article_no(m.group(0)))
        if hit:
            cands.add(hit)
            continue
        no = _cn_to_int(m.group(1))
        if no:
            hit2 = tab["by_no"].get(no)
            if hit2:
                cands.add(hit2)
    return next(iter(cands)) if len(cands) == 1 else ""


def reset_caches() -> None:
    """清空进程内索引缓存（测试/长驻进程换数据后调用）。"""
    global _INDEX
    _INDEX = None
