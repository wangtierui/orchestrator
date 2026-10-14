# -*- coding: utf-8 -*-
"""modules.reg_frameworks.mapping — 条款 ↔ 数据项映射（方案 §5.2.3，质量优先）。

三层置信（**软方法不得自动确认**，阈值纪律沿用「无度量不上线」）：
  1. `explicit`：条款正文显式提及数据项名/表名（归一化精确/包含匹配）→ 1.0 → `confirmed`
  2. `keyword`：数据项关键词组 × 条款共现 → 0.5~0.9 → 达阈值 `confirmed`，否则 `review`
  3. `semantic`（可选增强，走 `semantic_enhance` 门面；未启用则**不出结果**，非降级兜底）：
     相似度映射 → 上限 0.75，**一律 `review`**（语义相似不构成合规判断）

`confidence < review_threshold` 或 `method=semantic` → `worklist(regfw_map_review)` 人工闸门。
"""
from __future__ import annotations

import os
import re

from modules.reg_frameworks import regfw_model as model
from modules.reg_frameworks import regfw_store as store

#: 人工闸门阈值（keyword 方法低于此值即入 worklist；semantic 一律入）
REVIEW_THRESHOLD = 0.75

_NORM = re.compile(r"[\s（）()【】\[\]、,，.。:：;；'\"“”‘’\-_/]+")


def norm(s: str) -> str:
    """归一化（去空白与标点）——中文条款与数据项名的可比前提。"""
    return _NORM.sub("", str(s or "")).lower()


def explicit_hits(clause_text: str, items: list[model.DataItem]) -> list[tuple[model.DataItem, str]]:
    """explicit：条款正文**显式提及**数据项名（或表名）→ (数据项, 证据)。"""
    t = norm(clause_text)
    hits: list[tuple[model.DataItem, str]] = []
    for it in items:
        n = norm(it.name)
        if len(n) >= 2 and n in t:
            hits.append((it, f"explicit:条款含「{it.name}」"))
    return hits


def keyword_hits(clause_text: str, items: list[model.DataItem]) -> list[tuple[model.DataItem, float, str]]:
    """keyword：关键词组命中比例 → (数据项, 置信, 证据)。上限 0.9。"""
    t = norm(clause_text)
    out: list[tuple[model.DataItem, float, str]] = []
    for it in items:
        kws: list[str] = list(it.keywords) or [norm(it.name)]
        kws = [k for k in kws if len(k) >= 2]
        if not kws:
            continue
        hit = [k for k in kws if norm(k) and norm(k) in t]
        if not hit:
            continue
        ratio = len(hit) / len(kws)
        conf = round(min(0.9, 0.5 + 0.4 * ratio), 4)
        out.append((it, conf, f"keyword:{len(hit)}/{len(kws)} 命中「{'、'.join(hit[:3])}」"))
    return out


def _cosine(a, b) -> float:
    """余弦相似度（`embed(normalize=True)` 下即点积；缺失维度按 0 处理）。"""
    import math

    va, vb = list(a or []), list(b or [])
    n = min(len(va), len(vb))
    if not n:
        return 0.0
    dot = sum(float(va[i]) * float(vb[i]) for i in range(n))
    na = math.sqrt(sum(float(x) * float(x) for x in va[:n])) or 1.0
    nb = math.sqrt(sum(float(x) * float(x) for x in vb[:n])) or 1.0
    return max(0.0, min(1.0, dot / (na * nb)))


def semantic_hits(clause_text: str, items: list[model.DataItem]) -> list[tuple[model.DataItem, float, str]]:
    """semantic（可选增强）：经 `semantic_enhance.embed` 取向量算余弦相似度。

    纪律：`embed` 是既有门面的**唯一**语义原语（无 `similarity` 便捷函数——不新造）；
    增强层未启用/不可用 → **不产出结果**（合法状态，非错误兜底）；命中值一律封顶 0.75
    且状态强制 `review`（阈值纪律：无度量不上线）。
    """
    try:
        from std_lib.common_lib import semantic_enhance as se
    except Exception:  # noqa: BLE001  增强层不可用 → 不产出
        return []
    texts = [clause_text] + [(it.definition or it.name) for it in items]
    try:
        res = se.embed(texts, strict=False)
    except Exception:  # noqa: BLE001  嵌入链不可用 → 不产出
        return []
    if not getattr(res, "ok", False):
        return []
    vecs = list(getattr(res, "vectors", None) or [])
    if len(vecs) < len(texts):
        return []
    out: list[tuple[model.DataItem, float, str]] = []
    # strict=True：items 与向量**一一对应**是语义层的前提（长度不等 = 向量错位，
    # 静默截断会让「某数据项永远无语义候选」而不被发现 ✗）。
    for it, vec in zip(items, vecs[1:], strict=True):
        sim = _cosine(vecs[0], vec)
        out.append((it, round(min(0.75, sim), 4),
                    f"semantic:sim={sim:.3f}（上限 0.75，必人工确认）"))
    return out


def map_clause(rfn: str, article_no: str, clause_text: str,
               items: list[model.DataItem], *, fid: str = "") -> dict:
    """对单条条款跑三层映射 → `(maps, worklist_rows)`。"""
    maps: list[model.ClauseItemMap] = []
    seen: set[str] = set()

    for it, ev in explicit_hits(clause_text, items):
        maps.append(model.ClauseItemMap(
            map_id=model.ClauseItemMap.mk_id(rfn, article_no, it.iid),
            rfn=rfn, article_no=article_no, iid=it.iid,
            method="explicit", confidence=1.0, evidence=ev, status="confirmed",
        ))
        seen.add(it.iid)

    for it, conf, ev in keyword_hits(clause_text, items):
        if it.iid in seen:
            continue
        status = "confirmed" if conf >= REVIEW_THRESHOLD else "review"
        maps.append(model.ClauseItemMap(
            map_id=model.ClauseItemMap.mk_id(rfn, article_no, it.iid),
            rfn=rfn, article_no=article_no, iid=it.iid,
            method="keyword", confidence=conf, evidence=ev, status=status,
        ))
        seen.add(it.iid)

    for it, conf, ev in semantic_hits(clause_text, items):
        if it.iid in seen:
            continue
        # semantic 一律 review（阈值纪律）
        maps.append(model.ClauseItemMap(
            map_id=model.ClauseItemMap.mk_id(rfn, article_no, it.iid),
            rfn=rfn, article_no=article_no, iid=it.iid,
            method="semantic", confidence=conf, evidence=ev, status="review",
        ))
        seen.add(it.iid)

    wl: list[dict] = []
    for m in maps:
        if m.needs_review:
            wl.append({
                "kind": "regfw_map_review",
                "subject": f"{rfn} {article_no} ↔ 数据项 {m.iid}",
                "suggestion": f"复核映射（method={m.method}, conf={m.confidence}）",
                "confidence": m.confidence,
                "payload": {"rfn": rfn, "article_no": article_no, "iid": m.iid,
                            "method": m.method, "confidence": m.confidence,
                            "evidence": m.evidence, "fid": fid},
            })
    return {"maps": [m.to_dict() for m in maps], "worklist": wl}


def map_from_clauses(*, limit: int = 200, fid: str = "", persist: bool = True,
                     review_threshold: float = REVIEW_THRESHOLD) -> dict:
    """全量映射：读既有条款事实源（relations_index/clauses）× 全部数据项 → 映射 + 待办。

    只读既有产物（`paths` 定位），不新增采集；`limit` 供首轮小样本试跑（出口判据要求
    「先小样本校验」）。
    """
    global REVIEW_THRESHOLD
    REVIEW_THRESHOLD = float(review_threshold)

    items = [model.DataItem(**r) for r in store.list_items()]
    if not items:
        return {"items": 0, "clauses": 0, "maps": 0, "worklist": 0,
                "note": "无数据项（先跑 extract）"}
    clauses = _load_clauses(limit=limit)
    all_maps: list[dict] = []
    all_wl: list[dict] = []
    for c in clauses:
        r = map_clause(c["rfn"], c["article_no"], c.get("text") or "", items, fid=fid)
        all_maps.extend(r["maps"])
        all_wl.extend(r["worklist"])
    res: dict = {"items": len(items), "clauses": len(clauses), "maps": len(all_maps),
           "worklist": len(all_wl),
           "confirmed": sum(1 for m in all_maps if m["status"] == "confirmed")}
    if persist and all_maps:
        res["write_maps"] = store.write_rows(store.F_MAPS, all_maps, key="map_id")
        if all_wl:
            res["worklist_added"] = _emit_worklist(all_wl)
    return res


def _load_clauses(*, limit: int) -> list[dict]:
    """读既有条款事实源（relations_index.jsonl 的条款边；无则读 clauses JSONL 目录）。"""
    import json

    import paths

    out: list[dict] = []
    rel = paths.relations_index()
    if os.path.exists(rel):
        with open(rel, encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                try:
                    r = json.loads(s)
                except ValueError:
                    continue
                art = str(r.get("article_no") or "")
                if not art:
                    continue
                out.append({"rfn": str(r.get("rfn") or ""), "article_no": art,
                            "text": str(r.get("quote") or r.get("evidence") or r.get("basis") or "")})
                if len(out) >= limit:
                    break
    return out


def _emit_worklist(rows: list[dict]) -> int:
    """低置信映射入待办（`worklist_add`，单条上限沿用既有封顶策略防淹没）。"""
    from std_lib.common_lib import governance_store as gs

    if not gs.enabled():
        return 0
    n = 0
    for r in rows[:20]:  # 单次上限 20（同 filter_clean_relevance 的防淹没策略）
        # kind 用**字面量**（门禁 J7 以 `worklist_add("<kind>")` 静态扫描产生方——
        #   传变量会让「已登记 kind 无产生方」误报，掩盖真实缺口）
        gs.worklist_add("regfw_map_review", r["subject"],
                        payload=r.get("payload") or {}, suggestion=r.get("suggestion", ""),
                        confidence=float(r.get("confidence") or 0.0))
        n += 1
    return n
