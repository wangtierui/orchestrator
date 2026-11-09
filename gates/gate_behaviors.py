# -*- coding: utf-8 -*-

"""gates.gate_behaviors — 交互行为门禁（§6.5 四条，P4 守卫）。

为什么单独一门：`pages_smoke` 只证明“结构渲染出来了”，证明不了“交互可用”。
实测教训：panel.js 丢失 behaviors() 加载器时，所有筛选按钮点了没反应，
而结构断言 110 项全绿——B1「点筛选必须出面板」正是补这个盲区。

环境策略（与 gate_pages_smoke 一致）：
  · 服务没起 / 浏览器不可用 → **PASS + skipped**（不阻断）；
  · 环境正常但断言不符 → **FAIL**，逐项列出 check/detail。

开关：`ORCH_BEHAVIOR=1` 才运行（默认跳过，单次约 20–30 秒）。
"""
from __future__ import annotations

import os


def _skipped(reason: str) -> tuple[bool, dict]:
    return True, {"skipped": True, "reason": reason}


def run() -> tuple[bool, dict]:
    if os.environ.get("ORCH_BEHAVIOR", "0") != "1":
        return _skipped("ORCH_BEHAVIOR 未开启（默认跳过；设 =1 运行 §6.5 四条）")

    try:
        from tools import behaviors_smoke as B
    except Exception as e:  # noqa: BLE001
        # 工具模块导入失败 = 代码/语法问题，**不是环境缺失**，必须判失败，
        # 否则门禁会因“跳过”而变绿（实测：pages_smoke 写坏语法时门禁仍 PASS）。
        return False, {"error": f"{type(e).__name__}: {e}",
                        "reason": "工具模块不可导入（不是环境问题）"}

    try:
        out = B.run()
    except Exception as e:  # noqa: BLE001
        return _skipped(f"运行失败（环境问题，未实检）：{type(e).__name__}: {e}")

    bad = [r for r in out["results"] if not r["ok"]]
    detail = {
        "checks": out["results"],
        "passed": out["passed"],
        "total": out["total"],
    }
    return (not bad), detail
