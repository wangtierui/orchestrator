# -*- coding: utf-8 -*-
"""
gates/gate_rfn_drift — RFN↔clean 漂移门禁（专项评估 v2 §2.3 / R7 实装）

语义（防「clean 重清洗改名/改号 → 下游 base/final 静默重建出双 RFN/错配」）：
  - 读 data/rfn_drift_state.json（reconcile_clean_drift 写）与当前 clean_index latest 快照日期；
  - state 缺失 → PASS + 提示先跑 reconcile（首次基线，不阻断）；
  - state.clean_snapshots 落后于当前 clean 快照（五源任一）→ FAIL：快照已推进但未重新核验
    漂移，阻断下游底座重建，须先 `python reconcile_clean_drift.py [--apply]`；
  - 快照日期一致 → PASS：reconcile 已尽核验职责；C1/C2/legacy 为持续治理项（提示数量，
    不阻塞交付门禁——存量治理与「快照推进阻断」解耦，防历史遗留长期卡死）。
"""
from __future__ import annotations

import json
import os
import re
import sys

import paths

_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
_SCRAPERS_MOD = os.path.join(paths.MODULES_DIR, "regulatory_scrapers")
_STATE = os.path.join(_MOD_CLASS, "data", "rfn_drift_state.json")
_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")
for _p in (_SCRAPERS_MOD, paths.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _current_clean_dates():
    try:
        from clean_index import get_clean_index  # noqa: PLC0415
        ci = get_clean_index()
    except Exception:  # noqa: BLE001
        return None
    out = {}
    for s in _SOURCES:
        p = ci.latest_csv_path(s)
        m = re.search(r"cleaned_(\d{8})\.csv$", p or "")
        out[s] = m.group(1) if m else ""
    return out


def run():
    if not os.path.exists(_STATE):
        # A-07（2026-09-12）：输入缺失不得空跑放行（"首次基线"=未实检，先运行 reconcile）。
        return False, {"error": "尚未建立漂移基线（先运行 modules/regulatory_classifier/scripts/"
                                "reconcile_clean_drift.py [--apply]）；快照推进阻断未实检，不得视为通过",
                      "state": None}
    state = json.load(open(_STATE, encoding="utf-8"))
    cur = _current_clean_dates()
    stale = []
    if cur:
        for s in _SOURCES:
            if cur.get(s) and state.get("clean_snapshots", {}).get(s) != cur[s]:
                stale.append(f"{s}: state={state['clean_snapshots'].get(s)} 现={cur[s]}")
    if stale:
        return False, {
            "problems": [f"clean 快照已推进但未重新 reconcile: {', '.join(stale)}",
                         "请先运行 modules/regulatory_classifier/scripts/reconcile_clean_drift.py [--apply]"],
            "state": state.get("stats", {}),
        }
    return True, {
        "ok": "快照日期与 reconcile 核验一致",
        "治理项(不阻断)": {
            "c1_pending": state.get("c1_pending"),
            "c2_pending": state.get("c2_pending"),
            "legacy_mismatch": state.get("legacy_mismatch"),
        },
        "clean_snapshots": state.get("clean_snapshots", {}),
        "checked_at": state.get("checked_at"),
    }


if __name__ == "__main__":
    passed, detail = run()
    print("[rfn_drift]", "PASS" if passed else "FAIL", json.dumps(detail, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
