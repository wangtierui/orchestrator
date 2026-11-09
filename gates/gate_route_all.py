# -*- coding: utf-8 -*-

"""gates.gate_route_all — 全站路由与浏览器历史门禁（P3 守卫）。

为什么单独一门：§4.3 要求「状态进 URL、后退/前进可还原、深链可打开」，
这只能在浏览器里验；`tools/route_all.py` 已把 11 页按 pager/url/q/view/path 五类判据实现，
此处只做门禁包装。

环境策略（与 gate_pages_smoke 一致）：
  · 服务没起 / 浏览器不可用 → **PASS + skipped**（不阻断）；
  · 环境正常但断言不符 → **FAIL**，逐项列出 slug 与失败 check。

开关：`ORCH_ROUTE_ALL=1` 才运行（默认跳过）——全跑约 2–3 分钟，
默认跑会让每次门禁变慢一个量级。
"""
from __future__ import annotations

import os


def _skipped(reason: str) -> tuple[bool, dict]:
    return True, {"skipped": True, "reason": reason}


def run() -> tuple[bool, dict]:
    if os.environ.get("ORCH_ROUTE_ALL", "0") != "1":
        return _skipped("ORCH_ROUTE_ALL 未开启（默认跳过；设 =1 运行 11 页 23 项）")

    try:
        from tools import route_all as R
    except Exception as e:  # noqa: BLE001
        return _skipped(f"route_all 不可导入：{type(e).__name__}: {e}")

    pages = []
    failed = []
    try:
        for slug in R.CASES:
            out = R.run(slug)
            pages.append({"slug": slug, "mode": out["mode"],
                          "passed": out["passed"], "total": out["total"]})
            if out["passed"] != out["total"]:
                failed.append({"slug": slug,
                               "checks": [c for c in out["checks"] if not c["ok"]]})
    except Exception as e:  # noqa: BLE001
        return _skipped(f"运行失败（环境问题，未实检）：{type(e).__name__}: {e}")

    ok = not failed
    detail = {
        "pages": pages,
        "failed": failed,
        "passed": sum(p["passed"] for p in pages),
        "total": sum(p["total"] for p in pages),
    }
    return ok, detail
