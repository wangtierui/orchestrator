# -*- coding: utf-8 -*-
"""interfaces/timeliness_api — 时效核验状态（timeliness_review）**唯一访问接口**（阶段 3）

定位：`modules/regulatory_scrapers/timeliness_review/` 的时效 SSOT
（`verification_state.json`）与核验摘要/变更台账此前被 classifier 的
`recall_audit`（R13 三态校验）与镜像脚本**各自 `sys.path.insert` 直连**。
本接口把引导收敛到一处（方案 §6 阶段 3）。

事实源口径（勿改）：
  - 时效 SSOT = `verification_state.json`，键为 `doc:<归一化文号>` 或
    `title:<标题前40字>`；记录体只有 status/replacement/verification_source/
    last_checked_at/changed_at/prev_status —— **身份在键上**。
  - 合格判定（gate_timeliness_ssot）：`now - last_checked_at <= 90 天`
    且 status ∉ {pending, uncertain}。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths  # noqa: E402

REVIEW_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "timeliness_review")
STATE_PATH = os.path.join(REVIEW_DIR, "verification_state.json")
if REVIEW_DIR not in sys.path:
    sys.path.insert(0, REVIEW_DIR)


def _impl():
    import verification_state as _vs  # noqa: PLC0415

    return _vs


def state_path() -> str:
    return STATE_PATH


def load_state() -> dict:
    """全量状态（键 → 记录）。文件缺失返回 {}。"""
    import json  # noqa: PLC0415

    if not os.path.exists(STATE_PATH):
        return {}
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state) -> None:
    """写回状态（唯一写方语义；仅供核验链调用）。"""
    _impl().save_state(state)


def mark_checked(state, key, rec):
    """按 SSOT 规则登记一条核验结果。"""
    return _impl().mark_checked(state, key, rec)


def is_fresh(rec, days: int = 90) -> bool:
    """记录是否为"新鲜且可作 SSOT 断言依据"（陈旧记录不参与门禁断言）。"""
    return _impl().is_fresh(rec, days=days)


def sync_to_classifier(changed_records, dry_run: bool = False):
    """核验变更 → 归属表「时效状态」同步（返回 (matched, unmatched, csv_path)）。

    ⚠️ 该函数的**写入目标是 classifier 的归属表**（另一模块的事实源）——按方案 §4.3
    "表级唯一写方"它应迁往 classifier 侧；当前保留既有实现（键精确匹配逻辑已充分验证），
    仅在访问路径上收口到本接口，**该单写方越权已登记为待治理项**。
    """
    return _impl().sync_to_classifier(changed_records, dry_run=dry_run)


def latest_summary() -> dict | None:
    """最新 `verify_summary_*.json`（无则 None）。"""
    import glob  # noqa: PLC0415
    import json  # noqa: PLC0415

    files = sorted(glob.glob(os.path.join(REVIEW_DIR, "verify_summary_*.json")))
    if not files:
        return None
    try:
        return json.load(open(files[-1], encoding="utf-8"))
    except (OSError, ValueError):
        return None
