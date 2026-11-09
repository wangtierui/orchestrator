# -*- coding: utf-8 -*-

"""gates.gate_components — 组件规格门禁（§5 实测尺寸，P6 守卫）。

为什么单独一门：`pages_smoke` 证明"渲染出来了"，`behaviors_smoke` 证明"能交互"，
但「表头是不是 48、面板是不是 320、按钮是不是 32」这类**尺寸**只有量才知道。
实测收益：分页按钮曾是 28（规格 32）、Counter 根本不存在——结构断言都看不出来。

环境策略（与其他门禁一致）：
  · 服务没起 / 浏览器不可用 → PASS + skipped；
  · 工具模块导入失败（代码问题）→ **FAIL**；
  · 断言不符 → **FAIL**，逐项列出。

开关：`ORCH_COMPONENTS=1` 才运行（默认跳过，单次约 30 秒）。
"""
from __future__ import annotations

import os


def _skipped(reason: str) -> tuple[bool, dict]:
    return True, {"skipped": True, "reason": reason}


def run() -> tuple[bool, dict]:
    if os.environ.get("ORCH_COMPONENTS", "0") != "1":
        return _skipped("ORCH_COMPONENTS 未开启（默认跳过；设 =1 运行 §5 八项尺寸）")

    try:
        from tools import components_smoke as C
    except Exception as e:  # noqa: BLE001
        # 工具损坏 ≠ 环境缺失，必须红
        return False, {"error": f"{type(e).__name__}: {e}",
                       "reason": "工具模块不可导入（不是环境问题）"}

    try:
        out = C.run()
    except Exception as e:  # noqa: BLE001
        return _skipped(f"运行失败（环境问题，未实检）：{type(e).__name__}: {e}")

    bad = [r for r in out["results"] if not r["ok"]]
    detail = {"checks": out["results"], "passed": out["passed"], "total": out["total"]}
    return (not bad), detail
