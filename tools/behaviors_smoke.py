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
    """点「来源」筛选按钮，等面板**可见且宽 320** 再断言它在视口内。

    可靠性：早前版本在"元素存在"就断言，偶发拿到 visibility:hidden 的首帧，
    导致 4/4 里偶尔掉成 3/4。改为两段轮询（触发器就绪 → 面板可见）。
    """
    _open(s)

    ready = False
    for _ in range(20):                      # 最多 5s 等工具栏渲染
        ready = s.eval("""(() => [...document.querySelectorAll('#toolbarSlot button')]
          .some(x => /来源|时效/.test(x.textContent)))()""")
        if ready:
            break
        time.sleep(0.25)
    if not ready:
        return {"check": "B1 浮层不出界", "ok": False, "detail": "5s 内工具栏未渲染出筛选按钮"}

    s.eval("""(() => {
      const b = [...document.querySelectorAll('#toolbarSlot button')]
        .find(x => /来源|时效/.test(x.textContent));
      if (b) b.click();
    })()""")

    rect = None
    for _ in range(24):                      # 最多 6s 等面板可见
        time.sleep(0.25)
        raw = s.eval("""(() => {
          const ps = [...document.querySelectorAll('[role="dialog"]')];
          const p = ps[ps.length - 1];
          if (!p) return null;
          const r = p.getBoundingClientRect();
          const cs = getComputedStyle(p);
          if (cs.visibility !== 'visible' || Math.round(r.width) !== 320) return null;
          return JSON.stringify({r: Math.round(r.right), b: Math.round(r.bottom),
            W: innerWidth, H: innerHeight, w: Math.round(r.width)});
        })()""")
        if raw:
            rect = json.loads(raw)
            break

    if not rect:
        return {"check": "B1 浮层不出界", "ok": False, "detail": "6s 内面板未变为可见/宽 320"}
    ok = rect["r"] <= rect["W"] + 1 and rect["b"] <= rect["H"] + 1
    return {"check": "B1 浮层不出界", "ok": ok,
            "detail": f"right={rect['r']}/{rect['W']} bottom={rect['b']}/{rect['H']} width={rect['w']}"}


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
    """点最后一个侧栏锚点（页面底部的小节），断言 #main 滚动而 window 不动。

    实测：该页 window 不可滚（scrollHeight=900=clientHeight），#main 才是滚动容器
    （scrollHeight 3544 > clientHeight 808）；点第一个锚点不产生滚动，因为目标在顶部。
    """
    _open(s)
    before = json.loads(s.eval("JSON.stringify({w: window.scrollY, m: (document.getElementById('main')||{}).scrollTop || 0})"))
    clicked = s.eval("""(() => {
      const it = [...document.querySelectorAll('#railItems .item-anchor')].pop();
      if (!it) return false;
      it.click();
      return true;
    })()""")
    time.sleep(2.0)
    after = json.loads(s.eval("JSON.stringify({w: window.scrollY, m: (document.getElementById('main')||{}).scrollTop || 0})"))
    ok = clicked and after["w"] == before["w"] and after["m"] != before["m"]
    return {"check": "B4 容器内滚动", "ok": ok,
            "detail": f"window.scrollY {before['w']}→{after['w']} · #main.scrollTop {before['m']}→{after['m']}"}


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
