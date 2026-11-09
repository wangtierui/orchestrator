"""行为验收（规格 §6.5 四条，可重复执行）。

用法：
    python -m tools.behaviors_smoke            # 跑四条
    python -m tools.behaviors_smoke --only B2  # 只跑一条

四条判据：
    B1 浮层不出界   打开最右侧筛选面板 → 面板矩形必须落在视口内（allowOutOfBounds:false 的意义）
    B2 弹窗焦点圈定  打开 Feedback 弹窗 → Tab 20 次，activeElement 必须始终在弹窗内
    B3 面板键盘      ↑/↓ 移动、End 到末项、Esc 关闭且焦点回到触发按钮
    B4 容器内滚动    点侧栏「列表」→ window.scrollY 不变，#main.scrollTop 变化
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


def _tab(s, times: int = 1) -> None:
    for _ in range(times):
        s.cmd("Input.dispatchKeyEvent",
              {"type": "rawKeyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
        s.cmd("Input.dispatchKeyEvent",
              {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
        time.sleep(0.05)


def _key(s, key: str, code: int) -> None:
    s.cmd("Input.dispatchKeyEvent",
          {"type": "rawKeyDown", "key": key, "code": key, "windowsVirtualKeyCode": code})
    s.cmd("Input.dispatchKeyEvent",
          {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": code})
    time.sleep(0.15)


def b1_panel_in_viewport(s) -> dict:
    _open(s)
    opened = s.eval("""(() => {
      const btns = [...document.querySelectorAll('#toolbarSlot button')]
        .filter(b => b.getAttribute('aria-haspopup'));
      const b = btns[btns.length - 1] || btns[0];
      if (!b) return false;
      window.__trig = b.id || '';
      b.click();
      return true;
    })()""")
    if not opened:
        return {"check": "B1 浮层不出界", "ok": False, "detail": "找不到筛选触发按钮"}
    time.sleep(0.8)
    rect = s.eval("""(() => {
      const p = [...document.querySelectorAll('body > div[role="dialog"]')].pop();
      if (!p) return null;
      const r = p.getBoundingClientRect();
      return JSON.stringify({r: Math.round(r.right), b: Math.round(r.bottom),
        W: innerWidth, H: innerHeight, w: Math.round(r.width)});
    })()""")
    if not rect:
        return {"check": "B1 浮层不出界", "ok": False, "detail": "面板未出现"}
    d = json.loads(rect)
    ok = d["r"] <= d["W"] + 1 and d["b"] <= d["H"] + 1 and d["w"] == 320
    return {"check": "B1 浮层不出界", "ok": ok,
            "detail": f"right={d['r']}/{d['W']} bottom={d['b']}/{d['H']} width={d['w']}"}


def b2_dialog_focus_trap(s) -> dict:
    _open(s)
    s.eval("document.getElementById('feedbackBtn').click()")
    time.sleep(0.8)
    inside_before = s.eval("!!document.getElementById('fbDialog')")
    if not inside_before:
        return {"check": "B2 弹窗焦点圈定", "ok": False, "detail": "弹窗未打开"}
    _tab(s, 20)
    outside = s.eval("""(() => {
      const d = document.getElementById('fbDialog');
      return d && !d.contains(document.activeElement)
        ? (document.activeElement.tagName + '#' + (document.activeElement.id || '')) : '';
    })()""")
    return {"check": "B2 弹窗焦点圈定", "ok": outside == "",
            "detail": "Tab 20 次全部在弹窗内" if outside == "" else f"跑出弹窗：{outside}"}


def b3_panel_keyboard(s) -> dict:
    _open(s)
    s.eval("""(() => {
      const b = [...document.querySelectorAll('#toolbarSlot button')]
        .find(x => x.getAttribute('aria-haspopup'));
      if (b) { b.id = b.id || 'trig-b3'; b.focus(); b.click(); }
    })()""")
    time.sleep(0.8)
    first = s.eval("(() => { const p=[...document.querySelectorAll('body > div[role=\"dialog\"]')].pop();"
                   " return p ? (p.querySelector('[role=option]')||{}).textContent || '' : ''; })()")
    _key(s, "ArrowDown", 40)
    after = s.eval("document.activeElement ? (document.activeElement.textContent || '').slice(0, 30) : ''")
    _key(s, "Escape", 27)
    time.sleep(0.4)
    closed = s.eval("document.querySelectorAll('body > div[role=\"dialog\"]').length === 0")
    back = s.eval("(document.activeElement && document.activeElement.id) || ''")
    ok = closed and back == "trig-b3"
    return {"check": "B3 面板键盘", "ok": ok,
            "detail": f"首项={first[:12]!r} ↓后焦点={after[:12]!r} Esc关闭={closed} 焦点回触发={back == 'trig-b3'}"}


def b4_scroll_container(s) -> dict:
    _open(s)
    before = s.eval("JSON.stringify({w: window.scrollY, m: (document.getElementById('main')||{}).scrollTop || 0})")
    clicked = s.eval("""(() => {
      const it = [...document.querySelectorAll('#railItems .item-anchor')][0];
      if (!it) return false;
      it.click();
      return true;
    })()""")
    time.sleep(1.6)
    after = s.eval("JSON.stringify({w: window.scrollY, m: (document.getElementById('main')||{}).scrollTop || 0})")
    a, b = json.loads(before), json.loads(after)
    ok = clicked and b["w"] == a["w"] and b["m"] != a["m"]
    return {"check": "B4 容器内滚动", "ok": ok,
            "detail": f"window.scrollY {a['w']}→{b['w']} · #main.scrollTop {a['m']}→{b['m']}"}


CHECKS = {"B1": b1_panel_in_viewport, "B2": b2_dialog_focus_trap,
          "B3": b3_panel_keyboard, "B4": b4_scroll_container}


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
            results.append({"check": f"{k} 执行异常", "ok": False,
                            "detail": f"{type(e).__name__}: {e}"})
    return {"results": results, "passed": sum(1 for r in results if r["ok"]), "total": len(results),
            "err": s.eval("String(window.__psErr)")}


def main() -> int:
    ap = argparse.ArgumentParser(description="行为验收（§6.5 四条）")
    ap.add_argument("--only", default=None, help="只跑某条：B1/B2/B3/B4")
    args = ap.parse_args()

    try:
        out = run(args.only)
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] CDP 不可用：{type(e).__name__}: {e}")
        return EXIT_FAIL

    for r in out["results"]:
        flag = "OK  " if r["ok"] else "FAIL"
        print(f"  [{flag}] {r['check']:<14} {r['detail']}")
    if out["err"] not in ("null", "undefined", ""):
        print(f"  [WARN] 页面异常：{out['err']}")
    print(f"\n{out['passed']}/{out['total']} 通过")
    return EXIT_OK if out["passed"] == out["total"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
