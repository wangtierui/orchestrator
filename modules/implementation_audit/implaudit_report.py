# -*- coding: utf-8 -*-
"""modules.implementation_audit.report — 差距运行与报告（§5.3.3 的挂接面）。

流程：条款要求（只读投影 `draft_clause` 产物）+ 执行记录 + 作证材料
  → `engine.evaluate` → `Gap` 事实源 upsert → **全部差距入 worklist(`implaudit_gap`)**
  → 统计摘要（CLI/门禁/确认台消费）。

工作流单次上限 20 条待办写入（防淹没，同 `filter_clean_relevance` 策略），并在返回值中
**如实披露**剩余条数——「入待办数 < 差距数」必须可见，不得静默截断。
"""
from __future__ import annotations

import glob
import json
import os

import paths
from modules.implementation_audit import engine
from modules.implementation_audit import implaudit_store as store
from modules.implementation_audit.implaudit_model import (
    ClauseRequirement,
    EvidenceMaterial,
    ImplementationRecord,
)

#: 单次 worklist 写入上限（防淹没；超出部分仅落事实源并披露）
WORKLIST_MAX = 20


def load_requirements(*, limit: int = 500) -> list[ClauseRequirement]:
    """条款要求：读 `draft_clause` 产物（条款级对照素材，**只读**）。"""
    out: list[ClauseRequirement] = []
    pattern = os.path.join(
        paths.MODULES_DIR, "internal_policy_drafter", "**", "*clause*.json*"
    )
    for d in sorted(glob.glob(pattern, recursive=True)):
        if not os.path.exists(d):
            continue
        try:
            with open(d, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        rows = data if isinstance(data, list) else (
            data.get("clauses") or data.get("items") or []
        )
        for r in rows:
            if not isinstance(r, dict):
                continue
            ipn = str(r.get("ipn") or r.get("IPN") or "")
            art = str(r.get("article_no") or r.get("article") or "")
            if not ipn or not art:
                continue
            out.append(ClauseRequirement(
                ipn=ipn, article_no=art,
                text=str(r.get("text") or r.get("quote") or ""),
                rfn=(str(r.get("rfn")) if r.get("rfn") else None),
            ))
            if len(out) >= limit:
                return out
    return out


def run_gaps(*, limit: int = 500, persist: bool = True, emit_worklist: bool = True) -> dict:
    """跑一次差距引擎 → 事实源 + 待办 + 摘要。"""
    reqs = load_requirements(limit=limit)
    recs = [ImplementationRecord(**r) for r in store.list_records()]
    evs = [EvidenceMaterial(**r) for r in store.list_evidence()]
    gaps = engine.evaluate(reqs, recs, evs)
    res: dict = {
        "requirements": len(reqs), "records": len(recs), "evidence": len(evs),
        **engine.summarize(gaps),
    }
    if persist and gaps:
        res["write"] = store.write_rows(store.F_GAPS, [g.to_dict() for g in gaps], key="gid")
    if emit_worklist and gaps:
        res["worklist"] = _emit(gaps)
        if res["worklist"].get("skipped"):
            res["note"] = (
                f"待办单次上限 {WORKLIST_MAX}：已写 {res['worklist']['written']}，"
                f"其余 {res['worklist']['skipped']} 条仅在事实源"
                "（`cli.py implaudit gaps` 可查）"
            )
    return res


def _emit(gaps) -> dict:
    """差距 → worklist（kind=`implaudit_gap`，含规则与严重度证据）。"""
    from std_lib.common_lib import governance_store as gs

    if not gs.enabled():
        return {"written": 0, "skipped": len(gaps), "reason": "治理库未启用"}
    written = 0
    for g in gaps[:WORKLIST_MAX]:
        gs.worklist_add(
            "implaudit_gap",
            subject=f"{g.ipn} {g.article_no or ''} 落地差距（{g.gap_class}/{g.severity}）".strip(),
            payload={"ipn": g.ipn, "article_no": g.article_no, "rfn": g.rfn,
                     "gap_class": g.gap_class, "severity": g.severity,
                     "dept": g.dept, "period": g.period, "gid": g.gid},
            suggestion=g.detail,
            confidence={"high": 0.9, "medium": 0.7, "low": 0.5}.get(g.severity, 0.5),
        )
        written += 1
    return {"written": written, "skipped": max(0, len(gaps) - written)}


def render_markdown(limit: int = 50) -> str:
    """差距报告（markdown）：按严重度分组的明细表（供人工审阅与留档）。"""
    rows = store.list_gaps()
    if not rows:
        return "# 落地执行差距报告\n\n> 暂无差距记录（先跑 `cli.py implaudit ingest` 与 `gaps`）\n"
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order.get(str(r.get("severity")), 3), str(r.get("ipn"))))
    L = ["# 落地执行差距报告", "",
         f"> 共 {len(rows)} 条；high={sum(1 for r in rows if r.get('severity') == 'high')} "
         f"medium={sum(1 for r in rows if r.get('severity') == 'medium')} "
         f"low={sum(1 for r in rows if r.get('severity') == 'low')}", "",
         "| 严重度 | 差距类别 | 制度 IPN | 条款 | 部门 | 期间 | 说明 |",
         "|---|---|---|---|---|---|---|"]
    for r in rows[:limit]:
        L.append(f"| {r.get('severity')} | {r.get('gap_class')} | {r.get('ipn')} | "
                 f"{r.get('article_no') or '—'} | {r.get('dept') or '—'} | "
                 f"{r.get('period') or '—'} | {r.get('detail') or ''} |")
    if len(rows) > limit:
        L.append(f"\n> 明细截断至 {limit} 条（共 {len(rows)} 条，见事实源 `impl_gaps.jsonl`）")
    return "\n".join(L) + "\n"
