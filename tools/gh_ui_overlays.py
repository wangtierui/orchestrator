# -*- coding: utf-8 -*-
"""tools/gh_ui_overlays.py — 补采「展开态」与「响应式断点」。

补的是 `gh_ui_states` 的两个缺口
--------------------------------
1. **展开态**：筛选面板（Author/Labels）、顶栏快速搜索对话框、头像菜单。
   这些节点在点开之前**根本不在 DOM 里**，静态抓取一个字节也拿不到；
   必须真实点击 → 读面板/选项/输入框 → 再关掉。
2. **响应式**：同一个控件在 1440 / 1012 / 768 / 544 四个宽度下的可见性与几何。
   GitHub 用 Primer 断点（544/768/1012/1280），超出断点后按钮会折叠进 "Filters"、
   密度开关会隐藏——这些只有改视口再量才知道，CSS 文本推不出来。

用法：
  python -m tools.gh_ui_overlays --overlays
  python -m tools.gh_ui_overlays --viewports 1440,1012,768,544
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "github-baseline")
REPO = "https://github.com/notepad-plus-plus/notepad-plus-plus"
ISSUES = f"{REPO}/issues"

#: 点开 → 抓 → 关掉
PANEL_JS = r"""(() => {
  const p = document.querySelector('.prc-SelectPanel-Overlay-tEbxh, [role="dialog"], .Overlay--size-auto');
  if (!p) return {missing: true};
  const cs = getComputedStyle(p);
  const inp = p.querySelector('input');
  const opts = [...p.querySelectorAll('[role="option"], li')].slice(0, 4).map(o => {
    const ocs = getComputedStyle(o);
    const r = o.getBoundingClientRect();
    return {text: (o.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 26),
            h: Math.round(r.height), bg: ocs.backgroundColor, color: ocs.color,
            radius: ocs.borderRadius, cursor: ocs.cursor,
            checkbox: !!o.querySelector('input[type=checkbox], [class*=Checkbox]')};
  });
  return {cls: String(p.className).slice(0, 60),
          rect: [Math.round(p.getBoundingClientRect().left), Math.round(p.getBoundingClientRect().top),
                 Math.round(p.getBoundingClientRect().width), Math.round(p.getBoundingClientRect().height)],
          radius: cs.borderRadius, border: cs.borderTopWidth + '/' + cs.borderTopColor,
          shadow: cs.boxShadow, bg: cs.backgroundColor, padding: cs.padding,
          input: inp ? {ph: inp.placeholder, h: Math.round(inp.getBoundingClientRect().height),
                        border: getComputedStyle(inp).borderTopWidth} : null,
          options: opts,
          html: p.outerHTML.replace(/\s+/g, ' ').slice(0, 2500)};
})()"""

MENU_JS = r"""(() => {
  const cands = [...document.querySelectorAll('[role="menu"], .dropdown-menu, [class*=Dropdown],'
    + ' [class*=dropdown], [class*=Menu], [class*=menu]')]
    .filter(e => { const r = e.getBoundingClientRect();
      return r.width > 150 && r.height > 60 && r.top > 0; });
  const m = cands[0];
  if (!m) return {missing: true};
  const cs = getComputedStyle(m);
  const items = [...m.querySelectorAll('[role="menuitem"], li > a, li > button, a, button')].slice(0, 6)
    .map(i => ({text: (i.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 22),
                h: Math.round(i.getBoundingClientRect().height)}));
  return {cls: String(m.className).slice(0, 50), radius: cs.borderRadius,
          shadow: cs.boxShadow, border: cs.borderTopWidth + '/' + cs.borderTopColor,
          width: Math.round(m.getBoundingClientRect().width),
          bg: cs.backgroundColor, items: items};
})()"""

QUICKSEARCH_JS = r"""(() => {
  const all = [...document.querySelectorAll('[role="dialog"]')];
  const d = all.find(x => x.querySelector('input')) || all[0];
  if (!d) return {missing: true};
  const cs = getComputedStyle(d);
  const inp = d.querySelector('input');
  const rows = [...d.querySelectorAll('[role="option"], li')].slice(0, 4)
    .map(r => ({text: (r.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24),
                h: Math.round(r.getBoundingClientRect().height)}));
  return {cls: String(d.className).slice(0, 50), radius: cs.borderRadius,
          shadow: cs.boxShadow, width: Math.round(d.getBoundingClientRect().width),
          input: inp ? {ph: inp.placeholder, h: Math.round(inp.getBoundingClientRect().height)} : null,
          rows: rows};
})()"""


def _mouse(s, kind: str, x: int, y: int) -> None:
    s.cmd("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})


def _click(s, expr: str) -> bool:
    pos = s.eval(f"(() => {{ const e = ({expr}); if (!e) return null;"
                 " e.scrollIntoView({block: 'center'});"
                 " const r = e.getBoundingClientRect();"
                 " return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; })()")
    if not isinstance(pos, list) or len(pos) != 2:
        return False
    _mouse(s, "mouseMoved", pos[0], pos[1])
    _mouse(s, "mousePressed", pos[0], pos[1])
    _mouse(s, "mouseReleased", pos[0], pos[1])
    return True


def _close(s) -> None:
    s.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
    s.eval("document.body.click()")
    time.sleep(0.6)


def _opt_hover(s) -> dict:
    """悬停第一个选项，返回底色变化。"""
    pos = s.eval("(() => { const o = document.querySelector("
                 "'.prc-SelectPanel-Overlay-tEbxh [role=\"option\"]');"
                 " if (!o) return null; const r = o.getBoundingClientRect();"
                 " return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; })()")
    if not isinstance(pos, list):
        return {}
    before = s.eval("(() => { const o = document.querySelector("
                    "'.prc-SelectPanel-Overlay-tEbxh [role=\"option\"]');"
                    " return o ? getComputedStyle(o).backgroundColor : null; })()")
    _mouse(s, "mouseMoved", pos[0], pos[1])
    time.sleep(0.3)
    after = s.eval("(() => { const o = document.querySelector("
                   "'.prc-SelectPanel-Overlay-tEbxh [role=\"option\"]');"
                   " return o ? getComputedStyle(o).backgroundColor : null; })()")
    return {"bg_before": before, "bg_hover": after}


def run_overlays(s) -> dict:
    out: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "panels": {}, "menus": {}}

    s.cmd("Page.navigate", {"url": ISSUES})
    time.sleep(7)
    for name, expr in (("author", "document.querySelector('button[aria-label=\"Filter by author\"]')"),
                       ("labels", "document.querySelector('button[aria-label=\"Filter by labels\"]')")):
        print(f"[overlay] {name} …", flush=True)
        if not _click(s, expr):
            out["panels"][name] = {"missing": True, "reason": "trigger 未找到"}
            continue
        time.sleep(1.3)
        panel = s.eval(PANEL_JS)
        expanded = s.eval("(() => { const e = document.querySelector('button[aria-expanded=\"true\"]');"
                          " return e ? (e.textContent || '').trim().slice(0, 16) : null; })()")
        hover = _opt_hover(s)
        out["panels"][name] = {"expanded_by": expanded,
                               "panel": panel if isinstance(panel, dict) else {"raw": str(panel)[:100]},
                               "option_hover": hover}
        _close(s)

    # 顶栏快速搜索
    print("[overlay] quick-search …", flush=True)
    s.cmd("Page.navigate", {"url": REPO})
    time.sleep(7)
    opened = _click(s, "[...document.querySelectorAll('header button')]"
                       ".find(e => /quick search/i.test(e.getAttribute('aria-label') || ''))")
    time.sleep(1.5)
    out["menu_quicksearch"] = {"opened": opened,
                               "body": s.eval(QUICKSEARCH_JS) if opened else {"skipped": True}}
    _close(s)

    # 头像菜单
    print("[overlay] avatar-menu …", flush=True)
    opened2 = _click(s, "[...document.querySelectorAll('header button')]"
                        ".find(b => b.querySelector('img'))")
    time.sleep(1.2)
    out["menus"]["avatar"] = {"opened": opened2,
                              "body": s.eval(MENU_JS) if opened2 else {"skipped": True}}
    _close(s)
    return out


#: 响应式：这些控件在窄屏会被折叠/隐藏
RESP_ITEMS = {
    "top-search": "[...document.querySelectorAll('header button')]"
                  ".find(e => /quick search/i.test(e.getAttribute('aria-label') || ''))",
    "filter-author": "document.querySelector('button[aria-label=\"Filter by author\"]')",
    "density": "document.querySelector('ul[aria-label*=\"display density\"]')",
    "list-search": "document.querySelector('#issues-index-filter-input')",
    "tab-open": "[...document.querySelectorAll('a,button')]"
                ".find(e => /^Open/.test((e.textContent || '').trim()))",
}

RESP_JS = r"""(() => {
  const el = (EXPR);
  if (!el) return {present: false};
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return {present: true, visible: r.width > 0 && r.height > 0,
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
          display: cs.display, fontSize: cs.fontSize};
})()"""


def run_viewports(s, widths: list[int]) -> dict:
    out: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "widths": {}}
    for w in widths:
        print(f"[viewport] {w} …", flush=True)
        s.cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        s.cmd("Page.navigate", {"url": ISSUES})
        time.sleep(5)
        row = {}
        for name, expr in RESP_ITEMS.items():
            v = s.eval(RESP_JS.replace("EXPR", f"({expr})"))
            row[name] = v if isinstance(v, dict) else {"raw": str(v)[:60]}
        out["widths"][str(w)] = row
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_overlays")
    ap.add_argument("--overlays", action="store_true", help="采面板/对话框/菜单的展开态")
    ap.add_argument("--viewports", default="", help="逗号分隔的宽度，如 1440,1012,768,544")
    args = ap.parse_args(argv)
    if not args.overlays and not args.viewports:
        args.overlays = True
        args.viewports = "1440,1012,768,544"

    from tools.cdp import Session

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    if args.overlays:
        data = run_overlays(s)
        p = os.path.join(OUT, "overlays.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print("写入", p)

    if args.viewports:
        widths = [int(x) for x in args.viewports.split(",") if x.strip()]
        data2 = run_viewports(s, widths)
        p2 = os.path.join(OUT, "responsive.json")
        with open(p2, "w", encoding="utf-8") as fh:
            json.dump(data2, fh, ensure_ascii=False, indent=1)
        print("写入", p2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
