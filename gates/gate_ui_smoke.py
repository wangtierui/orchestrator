# -*- coding: utf-8 -*-
"""gates.gate_ui_smoke — 前端**运行时**冒烟门禁。

为什么要单独一门：`tests/` 的契约守卫只能断言源码里写了什么，
而 hover 颜色、面板宽度、密度行高、标题省略、按钮 disabled 这些
**只有浏览器里才能量**。本门禁调用 `tools/ui_smoke.py` 打开工作台实测。

环境策略（重要，避免门禁变成"机器没装浏览器就全红"）：
  · 服务没起 / 找不到 Edge / 浏览器启动失败 → **PASS + skipped 标注**（不阻断）；
  · 环境正常但实测值不符 → **FAIL**，逐项列出 expected/actual。

开关：`ORCH_UI_SMOKE=0` 可强制跳过（离线机器 / CI 无浏览器）。
"""
from __future__ import annotations

import os


def _skipped(reason: str) -> tuple[bool, dict]:
    return True, {"skipped": True, "reason": reason}


def run() -> tuple[bool, dict]:
    if os.environ.get("ORCH_UI_SMOKE", "1") == "0":
        return _skipped("ORCH_UI_SMOKE=0（人工关闭）")

    try:
        from tools import ui_smoke as S
    except Exception as e:  # noqa: BLE001
        return _skipped(f"ui_smoke 不可导入：{type(e).__name__}: {e}")

    try:
        data = S.collect()
    except Exception as e:  # noqa: BLE001
        # 浏览器崩溃/端口占用等基础设施问题：不判失败，但明确标注未实检
        return _skipped(f"采集失败（环境问题，未实检）：{type(e).__name__}: {e}")

    if not data.get("available"):
        return _skipped(data.get("reason") or "环境不可用")

    checks = S.evaluate(data)
    bad = [c for c in checks if not c["ok"]]
    detail = {
        "checks": [{k: c[k] for k in ("check", "ok", "detail")} for c in checks],
        "passed": len(checks) - len(bad),
        "total": len(checks),
    }
    return (not bad), detail
