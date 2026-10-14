# -*- coding: utf-8 -*-
"""gate_implementation_audit — 落地执行检视契约（W3，方案 §5.4 门禁）。

四组断言：
  A1 **规则表完整性**：`rules/gap_rules.yaml` 规则集覆盖四个 `GAP_CLASS`、severity ∈ 受控域、
      规则 id 唯一且 order 唯一（首个命中即止 ⇒ order 冲突会让规则静默失效）；
  A2 **契约登记**：`contract_manifest.json` 登记 gap / impl_record / evidence 键集；
  A3 **差距闭合**：每条 `Gap` 的 `ipn` 必须在制度索引中存在（悬空制度编号 = 检出垃圾 ✗）；
  A4 **受控域闭包**：事实源中 exec_status / gap_class / severity / evidence kind 取值 ⊆ 受控域。
"""
from __future__ import annotations

import json
import os

import paths


def run() -> tuple[bool, dict]:
    from config import enums as _en
    from modules.implementation_audit import implaudit_store as _store
    from modules.implementation_audit.engine import load_rules

    problems: list[str] = []
    detail: dict = {}

    # A1 规则表
    rules = list(load_rules())
    detail["rules"] = len(rules)
    ids = [str(r.get("id")) for r in rules]
    orders = [int(r.get("order") or 0) for r in rules]
    if len(set(ids)) != len(ids):
        problems.append(f"A1: 规则 id 重复：{ids}")
    if len(set(orders)) != len(orders):
        problems.append(f"A1: 规则 order 重复（首个命中即止 ⇒ 冲突会让规则静默失效）：{orders}")
    covered = {str(r.get("gap_class")) for r in rules}
    need = set(_en.GAP_CLASS)
    if covered != need:
        problems.append(f"A1: 规则未覆盖全部 gap_class（缺 {sorted(need - covered)} / 多 {sorted(covered - need)}）")
    bad_sev = sorted({str(r.get("severity")) for r in rules} - _en.GAP_SEVERITY)
    if bad_sev:
        problems.append(f"A1: 规则 severity 越受控域 {bad_sev}")

    # A2 契约登记
    try:
        with open(os.path.join(paths.CONFIG_DIR, "schema", "contract_manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        # 清单真实结构：`contracts` 为**字典**（键 = 契约名），非 list
        entries = man.get("contracts") or {}
        names = set(entries) if isinstance(entries, dict) else {
            str(e.get("name") or e.get("id") or "") for e in entries}
        miss = sorted({"implaudit_gaps", "implaudit_records",
                      "implaudit_evidence"} - names)
        if miss:
            problems.append(f"A2: contract_manifest 缺登记 {miss}")
        detail["contracts"] = len(names)
    except Exception as e:  # noqa: BLE001
        problems.append(f"A2: contract_manifest 不可读（{type(e).__name__}: {e}）")

    # A3 差距闭合（制度编号存在性）
    gaps = _store.list_gaps()
    detail.update(_store.stats())
    if gaps:
        ipn_index = os.path.join(
            paths.MODULES_DIR, "internal_policy_base", "data", "internal_policy_index.json"
        )
        known: set[str] = set()
        if os.path.exists(ipn_index):
            try:
                with open(ipn_index, encoding="utf-8") as fh:
                    idx = json.load(fh)
                items = idx if isinstance(idx, list) else (idx.get("items") or [])
                for r in items:
                    if isinstance(r, dict):
                        v = str(r.get("ipn") or r.get("IPN") or "")
                        if v:
                            known.add(v)
            except (OSError, ValueError):
                known = set()
        if known:
            orphan = sorted({str(g.get("ipn")) for g in gaps} - known)
            if orphan:
                problems.append(f"A3: 差距引用不存在的制度编号 {orphan[:5]}（悬空检出 ✗）")
        else:
            detail["ipn_index"] = "不可读（跳过 A3 存在性核验，已披露）"

    # A4 受控域闭包
    bad_exec = sorted({str(r.get("exec_status")) for r in _store.list_records()} - _en.EXEC_STATUS)
    if bad_exec:
        problems.append(f"A4: exec_status 越受控域 {bad_exec}")
    bad_gap = sorted({str(g.get("gap_class")) for g in gaps} - _en.GAP_CLASS)
    if bad_gap:
        problems.append(f"A4: gap_class 越受控域 {bad_gap}")
    bad_sev = sorted({str(g.get("severity")) for g in gaps} - _en.GAP_SEVERITY)
    if bad_sev:
        problems.append(f"A4: severity 越受控域 {bad_sev}")
    bad_ev = sorted({str(e.get("kind")) for e in _store.list_evidence()} - _en.EVIDENCE_KIND)
    if bad_ev:
        problems.append(f"A4: evidence kind 越受控域 {bad_ev}")

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":
    from gates import run_cli

    raise SystemExit(run_cli("implementation_audit", run))
