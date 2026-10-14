# -*- coding: utf-8 -*-
"""modules.implementation_audit.engine — 三方差距引擎（确定性求值器，方案 §5.3.2）。

输入三方（制度条款 × 执行记录 × 作证材料）→ 输出 `Gap` 列表。**纯函数**：无 IO、无随机、
无全局状态 ⇒ 可全量单测、结果可复现（合规场景对「同一输入必得同一结论」的要求）。

规则来自 `rules/gap_rules.yaml`（领域资产，可审阅可版本化）；求值顺序按 `order`，
首个命中即止 —— 保证每个「条款×部门×期间」只出一个差距类别且**可解释**（detail 带规则 id）。
"""
from __future__ import annotations

import functools
import os
from collections.abc import Iterable

import paths
from modules.implementation_audit.implaudit_model import (
    ClauseRequirement,
    EvidenceMaterial,
    Gap,
    ImplementationRecord,
)

RULES_FILE = os.path.join(paths.MODULES_DIR, "implementation_audit", "rules", "gap_rules.yaml")


@functools.lru_cache(maxsize=1)
def load_rules() -> tuple[dict, ...]:
    """读规则表（唯一事实源；`order` 排序后返回）。"""
    import yaml

    with open(RULES_FILE, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rows = list(data.get("rules") or [])
    rows.sort(key=lambda r: int(r.get("order") or 0))
    return tuple(rows)


def _index_records(records: Iterable[ImplementationRecord]) -> dict[tuple, list[ImplementationRecord]]:
    idx: dict[tuple, list[ImplementationRecord]] = {}
    for r in records:
        idx.setdefault((r.ipn or "", r.article_no or ""), []).append(r)
    return idx


def _index_evidence(evidence: Iterable[EvidenceMaterial]) -> dict[tuple, list[EvidenceMaterial]]:
    idx: dict[tuple, list[EvidenceMaterial]] = {}
    for e in evidence:
        idx.setdefault((e.ipn or "", e.article_no or ""), []).append(e)
    return idx


def _rule_hit(rule: dict, recs: list[ImplementationRecord], evs: list[EvidenceMaterial]) -> bool:
    """单条规则的判定（确定性；`condition` 目前三种形态，扩展需同步本函数与规则 schema）。"""
    cond = str(rule.get("condition") or "always")
    allowed = set(rule.get("exec_statuses") or ())
    if cond == "no_record":
        return not recs
    if allowed:
        if not any(r.exec_status in allowed for r in recs):
            return False
    elif not recs:
        return False
    # `evidence_rule` = **无差距**所需的作证条件（语义见 rules/gap_rules.yaml 头注）：
    #   none      → 作证与本规则无关（不因作证多少而命中/不命中）；
    #   min_count → 作证少于 min_count 件才算差距（有足够作证则不命中）。
    ev_rule = str(rule.get("evidence_rule") or "none")
    if ev_rule == "zero":
        if evs:
            return False          # 有作证 → 不是 no_evidence
    elif ev_rule == "min_count":
        need = int(rule.get("min_count") or 2)
        if len(evs) >= need:
            return False
    return True


def evaluate(
    requirements: Iterable[ClauseRequirement],
    records: Iterable[ImplementationRecord],
    evidence: Iterable[EvidenceMaterial],
    *,
    include_recommended: bool = False,
) -> list[Gap]:
    """对每条「条款要求」求差距（确定性、顺序稳定：按条款键排序）。"""
    rules = load_rules()
    rec_idx = _index_records(records)
    ev_idx = _index_evidence(evidence)
    gaps: list[Gap] = []
    for req in sorted(requirements, key=lambda r: (r.ipn, r.article_no)):
        if req.requirement_level != "required" and not include_recommended:
            continue
        key = (req.ipn, req.article_no)
        recs = rec_idx.get(key, [])
        evs = ev_idx.get(key, [])
        for rule in rules:
            if not _rule_hit(rule, recs, evs):
                continue
            dept = recs[0].dept if recs else ""
            period = recs[0].period if recs else ""
            gaps.append(Gap(
                gid=Gap.mk_id(req.ipn, req.article_no, str(rule["gap_class"])),
                ipn=req.ipn, article_no=req.article_no, rfn=req.rfn,
                gap_class=str(rule["gap_class"]), severity=str(rule["severity"]),
                dept=dept, period=period,
                detail=f"{rule.get('detail') or ''}（规则 {rule.get('id')}）",
            ))
            break  # 首个命中即止：一个条款一个差距类别（可解释优先）
    return gaps


def summarize(gaps: Iterable[Gap]) -> dict:
    """差距统计（按 class × severity）——供 CLI/门禁/确认台消费。"""
    by_class: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for g in gaps:
        by_class[g.gap_class] = by_class.get(g.gap_class, 0) + 1
        by_sev[g.severity] = by_sev.get(g.severity, 0) + 1
    return {"total": sum(by_class.values()), "by_class": by_class, "by_severity": by_sev}
