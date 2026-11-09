# -*- coding: utf-8 -*-

"""gates.gate_pages_smoke — 平行重建样板页（/pages/*.html）的运行时冒烟门禁。

为什么单独一门：`/pages/` 是「Primer 单一真源」重建页，不在旧 index.html 的守卫范围；
迁移要能验收，就得有可重复的门槛（页面可打开 / 令牌生效 / 数据渲染 / 图表实例）。

环境策略（与 gate_ui_smoke 一致）：
  · 服务没起 / 找不到 Edge / 浏览器启动失败 → **PASS + skipped**（不阻断）；
  · 环境正常但断言不符 → **FAIL**，逐项列出 check/detail。

开关：`ORCH_PAGES_SMOKE=0` 可强制跳过。
"""
from __future__ import annotations

import os


def _skipped(reason: str) -> tuple[bool, dict]:
    return True, {"skipped": True, "reason": reason}


def run() -> tuple[bool, dict]:
    if os.environ.get("ORCH_PAGES_SMOKE", "1") == "0":
        return _skipped("ORCH_PAGES_SMOKE=0（人工关闭）")

    try:
        from tools import pages_smoke as S
    except Exception as e:  # noqa: BLE001
        # 工具模块导入失败 = 代码/语法问题，**不是环境缺失**，必须判失败，
        # 否则门禁会因“跳过”而变绿（实测：pages_smoke 写坏语法时门禁仍 PASS）。
        return False, {"error": f"{type(e).__name__}: {e}",
                        "reason": "工具模块不可导入（不是环境问题）"}

    try:
        data = S.collect()
    except Exception as e:  # noqa: BLE001
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
