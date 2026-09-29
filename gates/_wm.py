# -*- coding: utf-8 -*-
"""gates._wm — 产物**水位**状态查询（门禁共用实现，2026-09-29）

背景
----
`_wm_status` 曾在 `gate_citations` / `gate_relations` / `gate_rfn_drift` 中**逐字重复三份**
（`tools/audit_health.py` 的「实现重复」检查捕获）。重复的风险不是"多几行"，而是
**改一处漏两处**：水位判据的语义（`ok`/`stale`/`unknown` 三分与回退口径）必须三处一致，
否则同一产物在不同门禁下结论不同 —— 那正是"门禁失效"的隐蔽形态。

纪律
----
  · **唯一事实源**：水位查询只有本实现；三个门禁一律 `from gates._wm import wm_status`；
  · **不得吞错**：进度不可用时返回 `unknown`（**不是** `ok`）→ 调用方须**退回指纹判据**或 FAIL，
    绝不允许"查不到水位就当通过"；
  · 本模块**非门禁**（文件名不以 `gate_` 开头）→ 不会被 `ALL_GATES` 注册表或判据 U 误纳。
"""

from __future__ import annotations


def wm_status(key: str) -> tuple:
    """→ `(state, detail)`；`state ∈ {ok, stale, unknown}`。

    `unknown` 表示水位不可用（治理库未启用/该产物无水位记录/接口异常）——
    调用方**必须**据此退回次级判据（如 inputs 指纹）或直接 FAIL，不得放行。
    """
    try:
        from interfaces.governance_api import wm_status as _impl

        return _impl(key)
    except Exception as e:  # noqa: BLE001  水位不可用 → unknown（调用方退回指纹判据）
        return "unknown", {"reason": f"{type(e).__name__}: {e}"}
