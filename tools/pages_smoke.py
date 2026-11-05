# -*- coding: utf-8 -*-
"""tools/pages_smoke.py — 平行重建样板页（/pages/*.html）的运行时冒烟。

为什么单独一个工具
------------------
`/pages/` 下是「Primer 单一真源」重建的 9 个页面，它们不在旧 `index.html` 的
守卫范围里。迁移要能验收，就得有一条**可重复的门槛**：每次改动后确认
① 页面能打开、无 JS 异常；② 令牌真的生效（边框 1px #d1d9e0、圆角 6px）；
③ 数据渲染出来（行数/分类数）；④ 图表实例存在且尺寸非零。

判定口径
--------
只断言「结构不变量」，不比对像素细节（那交给 gh_ui_visual / gh_ui_compare）。
环境不可用（服务没起 / 没有浏览器）时 `available=False`，由调用方决定是否放过。

用法：
    python -m tools.pages_smoke            # 人读，0=全过
    python -m tools.pages_smoke --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import time

PAGES = [
    # slug, 关键选择器, 需要行高检查, 需要图表
    ("laws", ".border-bottom.py-1", True, False),
    ("rules", ".border-bottom.py-1", True, False),
    ("search", "#q", False, False),
    ("worklist", ".row-1", True, False),
    ("feedback", ".row-1", True, False),
    ("regstats", ".cat", True, False),
    ("overview", ".grid3", False, True),
    ("project", "#chart", False, True),
    ("flow", "#chart", False, True),
    ("metrics", ".grid3", False, True),
    ("graph", "#chart", False, True),
    ("release", ".row-1", True, False),
    ("governance", ".row-1", True, False),
]

PROBE = """(() => {
  const sel = "SEL";
  const has = !!document.querySelector(sel);
  const rows = [...document.querySelectorAll(".row-1, .border-bottom.py-1")]
    .filter(r => r.querySelector(".cell"));
  const box = document.querySelector(".border.rounded-2") || document.querySelector(".border");
  const cs = box ? getComputedStyle(box) : null;
  const canvas = document.querySelector("#chart canvas") || document.querySelector("canvas");
  const root = getComputedStyle(document.documentElement);
  return JSON.stringify({
    has,
    rows: rows.length,
    rowH: rows.length ? Math.round(rows[0].getBoundingClientRect().height) : null,
    border: cs ? cs.borderTopWidth : null,
    borderColor: cs ? cs.borderTopColor : null,
    radius: cs ? cs.borderRadius : null,
    token: root.getPropertyValue("--borderColor-default").trim(),
    echarts: window.echarts ? window.echarts.version : null,
    canvas: canvas ? [canvas.width, canvas.height] : null,
    err: window.__psErr || null,
  });
})()"""


def collect() -> dict:
    """打开 9 个样板页并采集不变量。"""
    try:
        import os

        # 允许用环境变量覆盖；未设置时默认连用户桌面那个带 9334 的实例，
        # 且禁止 cdp 自己 spawn（否则会连到一个看不见的 headless，实测永远是空页）。
        os.environ.setdefault("CDP_PORT", "9334")
        os.environ.setdefault("CDP_NO_SPAWN", "1")
        from tools.cdp import Session

        s = Session()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"CDP 不可用：{type(e).__name__}: {e}"}

    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})

    out: dict[str, dict] = {}
    for slug, sel, need_rows, need_chart in PAGES:
        url = f"http://127.0.0.1:8765/pages/{slug}.html?v={time.time():.0f}"
        try:
            s.eval("window.__psErr = null;")
            s.cmd("Page.navigate", {"url": url})
            time.sleep(6)
            raw = s.eval(PROBE.replace("SEL", sel))
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            # 图表是异步渲染的：首次探测拿不到 canvas 时重试（最多 3 次 × 2s），
            # 否则门禁会因为一次渲染慢而误报（实测偶发 graph 页 canvas=None）。
            for _ in range(3):
                if not need_chart or (data.get("canvas") or [0, 0])[0] > 100:
                    break
                time.sleep(2)
                raw = s.eval(PROBE.replace("SEL", sel))
                data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as e:  # noqa: BLE001
            data = {"error": f"{type(e).__name__}: {e}"}
        data.update({"needRows": need_rows, "needChart": need_chart})
        out[slug] = data
    return {"available": True, "pages": out}


def evaluate(data: dict) -> list[dict]:
    """把采集值变成断言列表。"""
    checks: list[dict] = []
    for slug, d in (data.get("pages") or {}).items():
        if d.get("error"):
            checks.append({"check": f"{slug}: 可打开", "ok": False, "detail": d["error"]})
            continue
        checks.append({"check": f"{slug}: 关键元素存在", "ok": bool(d.get("has")),
                       "detail": d.get("has")})
        if d.get("needRows"):
            n = d.get("rows") or 0
            checks.append({"check": f"{slug}: 渲染出行", "ok": n > 0, "detail": f"{n} 行"})
            h = d.get("rowH")
            ok_h = h is not None and 28 <= h <= 40
            checks.append({"check": f"{slug}: 行高 28–40", "ok": ok_h, "detail": f"{h}px"})
        if d.get("token") != "#d1d9e0":
            checks.append({"check": f"{slug}: 令牌 --borderColor-default",
                           "ok": False, "detail": d.get("token") or "(空)"})
        else:
            checks.append({"check": f"{slug}: 令牌 --borderColor-default",
                           "ok": True, "detail": "#d1d9e0"})
        if d.get("border") not in (None, "0px"):
            ok_b = d.get("border") == "1px" and str(d.get("borderColor")) == "rgb(209, 217, 224)"
            checks.append({"check": f"{slug}: 边框 1px #d1d9e0", "ok": ok_b,
                           "detail": f"{d.get('border')} / {d.get('borderColor')}"})
        if d.get("needChart"):
            ok_c = d.get("echarts") == "6.1.0" and (d.get("canvas") or [0, 0])[0] > 100
            checks.append({"check": f"{slug}: ECharts 6.1.0 已渲染", "ok": ok_c,
                           "detail": f"v{d.get('echarts')} canvas={d.get('canvas')}"})
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pages_smoke")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-unavailable", action="store_true")
    args = ap.parse_args(argv)

    data = collect()
    if not data.get("available"):
        print(f"[pages_smoke] 环境不可用：{data.get('reason')}")
        return 0 if args.skip_unavailable else 1

    checks = evaluate(data)
    bad = [c for c in checks if not c["ok"]]
    if args.json:
        print(json.dumps({"passed": len(checks) - len(bad), "total": len(checks),
                          "checks": checks}, ensure_ascii=False, indent=1))
    else:
        for c in checks:
            print(f"  [{'OK  ' if c['ok'] else 'FAIL'}] {c['check']}  {c['detail']}")
        print(f"\n{len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
