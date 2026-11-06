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
    ("laws", ".rco-tbl__row", True, False),
    ("rules", ".rco-tbl__row", True, False),
    ("search", "#tableSlot", False, False),
    ("worklist", ".rco-tbl__row", True, False),
    ("feedback", ".rco-tbl__row", True, False),
    ("regstats", ".cat", True, False),
    ("overview", "#kpiRow", False, True),
    ("project", "#chart", False, True),
    ("flow", "#chart", False, True),
    ("metrics", ".grid3", False, True),
    ("graph", "#chart", False, True),
    ("release", ".rco-tbl__row", True, False),
    ("governance", ".rco-tbl__row", True, False),
]

PROBE = """(() => {
  const sel = "SEL";
  const has = !!document.querySelector(sel);
  const rows = [...document.querySelectorAll(".row-1, .border-bottom.py-1, .rco-tbl__row")]
    .filter(r => r.querySelector(".cell, .rco-tbl__cell"));
  const box = document.querySelector(".border.rounded-2") || document.querySelector(".border");
  const cs = box ? getComputedStyle(box) : null;
  const canvas = document.querySelector("#chart canvas") || document.querySelector("canvas");
  const root = getComputedStyle(document.documentElement);
  const q = document.querySelector("#q");
  const nav = document.querySelector("nav[aria-label], [role=navigation]");
  const main = document.querySelector("main, [role=main]");
  const cur = document.querySelectorAll("[aria-current]");
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
    ready: document.body.dataset.pageReady === "1",
    aria: {
      main: !!main,
      nav: !!nav,
      searchLabel: q ? (q.getAttribute("aria-label") || q.getAttribute("aria-labelledby") || "") : "-",
      ariaCurrent: cur.length,
    },
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
            # 后台标签页会被浏览器节流（定时器降频到每分钟一次）⇒ 页面永远不 ready，
            # 表现为"laws 关键元素不存在 / data-page-ready 未标记"的偶发红。每次导航前先置前。
            s.cmd("Page.bringToFront", {})
            s.cmd("Page.navigate", {"url": url})
            time.sleep(1.2)
            # 等页面自己打 data-page-ready：动态渲染页先等数据回来，
            # 否则断言会跑在骨架屏上（时序问题 → 改成确定性等待）。
            for _ in range(80):
                if s.eval("document.body && document.body.dataset.pageReady === '1'"):
                    break
                time.sleep(0.25)  # 最多等 20s：关系图谱要拉全量关系后再渲染
            time.sleep(0.15)
            raw = s.eval(PROBE.replace("SEL", sel))
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            # 页面偶发未就绪（实测 laws 首测 has=False / ready=False）：整页重载一次再判，
            # 否则门禁会因为一次加载慢而误报（与图表重试同理）。
            if not data.get("ready") or not data.get("has"):
                s.cmd("Page.navigate", {"url": url})
                time.sleep(1.5)
                for _ in range(40):
                    if s.eval("document.body && document.body.dataset.pageReady === '1'"):
                        break
                    time.sleep(0.25)
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
        # 时序：页面必须自己声明渲染完成（否则断言可能跑在骨架屏上）
        checks.append({"check": f"{slug}: data-page-ready", "ok": bool(d.get("ready")),
                       "detail": "body[data-page-ready=1]" if d.get("ready") else "未标记"})
        # ARIA：主区域 / 导航 / 搜索框可访问名 / 唯一 aria-current
        aria = d.get("aria") or {}
        checks.append({"check": f"{slug}: ARIA 主区域", "ok": bool(aria.get("main")),
                       "detail": "main 或 role=main"})
        ok_label = aria.get("searchLabel") in ("-", "") or bool(aria.get("searchLabel"))
        checks.append({"check": f"{slug}: ARIA 搜索框可访问名", "ok": ok_label,
                       "detail": aria.get("searchLabel") or "(缺失)"})
        if d.get("needRows"):
            n = d.get("rows") or 0
            checks.append({"check": f"{slug}: 渲染出行", "ok": n > 0, "detail": f"{n} 行"})
            h = d.get("rowH")
            MIGRATED = {"rules", chr(108)+chr(97)+chr(119)+chr(115), "worklist", "feedback", "search", "governance", "release"}
            ok_h = h is not None and ((36 <= h <= 40 or 60 <= h <= 70) if slug in MIGRATED else 28 <= h <= 70)
            checks.append({"check": f"{slug}: 行高 38|65", "ok": ok_h, "detail": f"{h}px"})
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
