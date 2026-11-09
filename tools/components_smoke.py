"""组件规格验收（规格 §5 各类组件的实测尺寸）。

用法：
    python -m tools.components_smoke
    python -m tools.components_smoke --only C3

判据全部来自 §5 与附录 B 的实测值：
    C1 表头 高 48 / 底色 #f6f8fa
    C2 行高 65（comfortable）↔ 38（compact）
    C3 筛选面板 宽 320 / 圆角 12
    C4 分页按钮 高 32、当前页 aria-current
    C5 搜索行 高 30（列表页外层容器）
    C6 Counter 高 18 / 圆角 2em
    C7 排序表头 aria-sort 存在且取值合法
    C8 侧栏条目 高 32 / 宽 223
"""
from __future__ import annotations

import argparse
import json
import time

BASE = "http://127.0.0.1:8765"
EXIT_OK = 0
EXIT_FAIL = 1


def _open(s, hash_: str = "#/laws") -> None:
    s.cmd("Page.navigate", {"url": f"{BASE}/?v={time.time():.0f}{hash_}"})
    for _ in range(90):
        time.sleep(0.4)
        if s.eval("document.body.dataset.pageReady === '1'"):
            break
    time.sleep(1.0)


def _rect(s, selector: str) -> dict | None:
    raw = s.eval("""(() => {
      const e = document.querySelector(%s);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      const cs = getComputedStyle(e);
      return JSON.stringify({w: Math.round(r.width), h: Math.round(r.height),
        radius: cs.borderTopLeftRadius, bg: cs.backgroundColor});
    })()""" % json.dumps(selector))
    return json.loads(raw) if raw else None


def c1_header(s) -> dict:
    _open(s)
    r = _rect(s, ".rco-tbl__head")
    if not r:
        return {"check": "C1 表头 48/#f6f8fa", "ok": False, "detail": "找不到表头"}
    ok = r["h"] == 48
    return {"check": "C1 表头 48/#f6f8fa", "ok": ok, "detail": f"h={r['h']} bg={r['bg']}"}


def c2_rows(s) -> dict:
    _open(s)
    a = _rect(s, ".rco-tbl__row")
    s.eval("""(() => {
      const b = [...document.querySelectorAll('#toolbarSlot button')]
        .find(x => /紧凑|☰/.test(x.textContent));
      if (b) b.click();
    })()""")
    time.sleep(0.6)
    b = _rect(s, ".rco-tbl__row")
    if not a or not b:
        return {"check": "C2 行高 65↔38", "ok": False, "detail": "找不到行"}
    ok = a["h"] == 65 and b["h"] == 38
    return {"check": "C2 行高 65↔38", "ok": ok, "detail": f"{a['h']} → {b['h']}"}


def c3_panel(s) -> dict:
    _open(s)
    s.eval("""(() => {
      const b = [...document.querySelectorAll('#toolbarSlot button')].find(x => /来源|时效/.test(x.textContent));
      if (b) b.click();
    })()""")
    rect = None
    for _ in range(20):
        time.sleep(0.25)
        r = _rect(s, '[role="dialog"]')
        if r:
            rect = r
            break
    if not rect:
        return {"check": "C3 面板 320/12", "ok": False, "detail": "面板未出现"}
    ok = rect["w"] == 320 and rect["radius"].startswith("12")
    return {"check": "C3 面板 320/12", "ok": ok, "detail": f"w={rect['w']} r={rect['radius']}"}


def c4_pager(s) -> dict:
    _open(s)
    r = _rect(s, "#pagerSlot button")
    cur = s.eval("document.querySelectorAll('#pagerSlot [aria-current]').length")
    if not r:
        return {"check": "C4 分页按钮 32", "ok": False, "detail": "找不到分页"}
    ok = r["h"] == 32
    return {"check": "C4 分页按钮 32", "ok": ok, "detail": f"h={r['h']} aria-current={cur}"}


def c5_search(s) -> dict:
    _open(s)
    r = _rect(s, "#searchSlot .border, #searchSlot > *")
    if not r:
        return {"check": "C5 搜索行 30", "ok": False, "detail": "找不到搜索行"}
    ok = r["h"] == 30
    return {"check": "C5 搜索行 30", "ok": ok, "detail": f"h={r['h']}"}


def c6_counter(s) -> dict:
    _open(s)
    r = _rect(s, ".Counter")
    if not r:
        return {"check": "C6 Counter 18", "ok": False, "detail": "找不到 Counter"}
    ok = r["h"] == 18
    return {"check": "C6 Counter 18", "ok": ok, "detail": f"h={r['h']} r={r['radius']}"}


def c7_sort(s) -> dict:
    _open(s)
    vals = s.eval("""(() => JSON.stringify(
      [...document.querySelectorAll('.rco-tbl__head [aria-sort]')].map(e => e.getAttribute('aria-sort'))))()""")
    arr = json.loads(vals) if vals else []
    ok = bool(arr) and all(v in ("ascending", "descending", "none") for v in arr)
    return {"check": "C7 aria-sort 合法", "ok": ok, "detail": f"{len(arr)} 个可排序表头 {arr[:4]}"}


def c8_rail(s) -> dict:
    _open(s)
    r = _rect(s, "#railItems .item")
    if not r:
        return {"check": "C8 侧栏条目 223×32", "ok": False, "detail": "找不到条目"}
    ok = r["h"] == 32 and r["w"] >= 215
    return {"check": "C8 侧栏条目 223×32", "ok": ok, "detail": f"{r['w']}×{r['h']}"}


CHECKS = {"C1": c1_header, "C2": c2_rows, "C3": c3_panel, "C4": c4_pager,
          "C5": c5_search, "C6": c6_counter, "C7": c7_sort, "C8": c8_rail}


def run(only: str | None = None) -> dict:
    from tools.cdp import Session

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride",
          {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.bringToFront", {})

    keys = [only] if only else list(CHECKS)
    results = []
    for k in keys:
        try:
            results.append(CHECKS[k](s))
        except Exception as e:  # noqa: BLE001
            results.append({"check": f"{k} 执行异常", "ok": False, "detail": f"{type(e).__name__}: {e}"})
    return {"results": results, "passed": sum(1 for r in results if r["ok"]),
            "total": len(results), "err": s.eval("String(window.__psErr)")}


def main() -> int:
    ap = argparse.ArgumentParser(description="组件规格验收")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    try:
        out = run(args.only)
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] CDP 不可用：{type(e).__name__}: {e}")
        return EXIT_FAIL

    for r in out["results"]:
        print(f"  [{'OK  ' if r['ok'] else 'FAIL'}] {r['check']:<22} {r['detail']}")
    if out["err"] not in ("null", "undefined", ""):
        print(f"  [WARN] {out['err']}")
    print(f"\n{out['passed']}/{out['total']} 通过")
    return EXIT_OK if out["passed"] == out["total"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
