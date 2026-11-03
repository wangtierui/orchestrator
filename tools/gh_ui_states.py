# -*- coding: utf-8 -*-
"""tools/gh_ui_states.py — 采集 GitHub 控件的**状态差分**（默认 / 悬停 / 聚焦 / 按下 / 展开）。

为什么必须单独一个工具
----------------------
`gh_ui_harvest` 抓的是"页面静态那一刻"：DOM 与元素语义都在，但
**悬停变什么颜色、按下有没有位移、聚焦是 2px 还是 1px、菜单展开后阴影多重**
只存在于 `:hover / :active / :focus / [aria-expanded=true]`，静态 DOM 里没有。
这里用 CDP 真实派发鼠标事件，逐状态读 `getComputedStyle`，把**差值**存下来。

测量口径（每个目标 × 4 态）
  default → hover(mouseMoved) → focus(.focus()) → active(mousePressed 未释放)
  同时读元素**本身**与最近 3 层祖先里"有背景/边框/阴影"的那一层（视觉宿主），
  因为 GitHub 的 hover 底色常落在父容器而不是 `<input>` 上。

用法：
  python -m tools.gh_ui_states [--only issues]
环境变量：CDP_PORT、CDP_NO_SPAWN=1（指向已登录且带调试端口的 Edge）
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "github-baseline")
REPO = "https://github.com/notepad-plus-plus/notepad-plus-plus"

PROPS = ["color", "backgroundColor", "borderTopColor", "borderTopWidth", "boxShadow",
         "outlineColor", "outlineWidth", "outlineStyle", "textDecorationLine",
         "opacity", "transform", "cursor"]

#: 元素定位表达式（用文本/aria 查找，避免脆弱的属性选择器）
EXPR = {
    "top-search": "[...document.querySelectorAll('header button, header a')]"
                  ".find(e => /quick search|type . to search/i.test("
                  "  (e.getAttribute('aria-label') || '') + ' ' + (e.textContent || '')))",
    "tab-issues": "[...document.querySelectorAll('a')].find(e => e.getAttribute('href','').endsWith('/issues'))",
    "btn-new-issue": "document.querySelector('a[href$=\"/issues/new\"]')"
                     " || [...document.querySelectorAll('a,button')]"
                     ".find(e => /^New issue$/.test((e.textContent||'').trim()))",
    "list-search": "document.querySelector('#issues-index-filter-input')",
    "density-btn": "document.querySelector('ul[aria-label*=\"display density\"] button')",
    "filter-author": "document.querySelector('button[aria-label=\"Filter by author\"]')",
    "tab-open": "[...document.querySelectorAll('a,button,[role=\"tab\"]')]"
                ".find(e => /^Open/.test((e.textContent||'').trim()))",
    "tab-closed": "[...document.querySelectorAll('a,button,[role=\"tab\"]')]"
                  ".find(e => /^Closed/.test((e.textContent||'').trim()))",
    "row-title": "document.querySelector('a[href*=\"/issues/\"]')",
    "pagination": "document.querySelector('a[aria-label^=\"Page \"], .pagination a')",
}

PAGES = {
    "repo": ["top-search", "tab-issues"],
    "issues": ["list-search", "density-btn", "filter-author", "tab-open", "tab-closed",
               "row-title", "pagination"],
}

READ = """(() => {
  let el = null;
  try { el = (EXPR); } catch (e) { return {missing: true, error: String(e).slice(0, 80)}; }
  if (!el) return {missing: true};
  el.scrollIntoView({block: 'center'});
  const pick = (e) => { const cs = getComputedStyle(e); const o = {};
    for (const p of PROPS) o[p] = cs[p]; return o; };
  let host = el, found = el;
  for (let i = 0; i < 3 && host; i++) {
    const cs = getComputedStyle(host);
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' || cs.boxShadow !== 'none'
        || parseFloat(cs.borderTopWidth) > 0) { found = host; break; }
    host = host.parentElement;
  }
  const r = el.getBoundingClientRect();
  return {self: pick(el), host: pick(found),
          hostTag: found.tagName, hostCls: String(found.className).slice(0, 50),
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
          tag: el.tagName, cls: String(el.className).slice(0, 60)};
})()"""


def _read(s, expr: str) -> dict:
    js = READ.replace("EXPR", f"({expr})").replace("PROPS", json.dumps(PROPS))
    v = s.eval(js)
    return v if isinstance(v, dict) else json.loads(v or "{}")


def _diff(base: dict, cur: dict) -> dict:
    out = {}
    for side in ("self", "host"):
        b, c = (base.get(side) or {}), (cur.get(side) or {})
        for k in PROPS:
            if b.get(k) != c.get(k):
                out[f"{side}.{k}"] = c.get(k)
    return out


def _mouse(s, kind: str, x: int, y: int) -> None:
    s.cmd("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                       "button": "left", "clickCount": 1})


def capture(s, name: str, expr: str) -> dict:
    base = _read(s, expr)
    if base.get("missing") or "rect" not in base:
        return {"name": name, "missing": True}
    x = base["rect"][0] + base["rect"][2] // 2
    y = base["rect"][1] + base["rect"][3] // 2
    out = {"name": name, "tag": base.get("tag"), "host": f"{base.get('hostTag')}.{base.get('hostCls')}",
           "rect": base["rect"], "default": base.get("self"), "host_default": base.get("host")}

    _mouse(s, "mouseMoved", x, y)
    time.sleep(0.25)
    out["hover"] = _diff(base, _read(s, expr))

    s.eval(f"(() => {{ const e = ({expr}); e && e.focus && e.focus(); }})()")
    time.sleep(0.2)
    out["focus"] = _diff(base, _read(s, expr))

    _mouse(s, "mouseMoved", x, y)
    _mouse(s, "mousePressed", x, y)
    time.sleep(0.2)
    out["active"] = _diff(base, _read(s, expr))
    _mouse(s, "mouseReleased", x, y)
    time.sleep(0.1)
    return out


def capture_panel(s) -> dict:
    """Author 面板：展开前后 aria-expanded + 面板与选项样式。"""
    trig = EXPR["filter-author"]
    before = s.eval(f"(() => {{ const e = ({trig}); return e && e.getAttribute('aria-expanded'); }})()")
    rect = s.eval(f"(() => {{ const e = ({trig}); if (!e) return null;"
                  " e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect();"
                  " return [Math.round(r.left + r.width/2), Math.round(r.top + r.height/2)]; })()")
    if isinstance(rect, list) and len(rect) == 2:
        _mouse(s, "mouseMoved", rect[0], rect[1])
        _mouse(s, "mousePressed", rect[0], rect[1])
        _mouse(s, "mouseReleased", rect[0], rect[1])
    time.sleep(1.4)
    panel = s.eval(r"""(() => {
      const p = document.querySelector('.prc-SelectPanel-Overlay-tEbxh, [role="dialog"]');
      if (!p) return {missing: true};
      const cs = getComputedStyle(p);
      const opt = p.querySelector('[role="option"]');
      const ocs = opt ? getComputedStyle(opt) : null;
      const inp = p.querySelector('input');
      return {radius: cs.borderRadius, shadow: cs.boxShadow,
              border: cs.borderTopWidth + '/' + cs.borderTopColor,
              width: Math.round(p.getBoundingClientRect().width),
              input: inp ? {ph: inp.placeholder,
                            border: getComputedStyle(inp).borderTopWidth} : null,
              option: ocs ? {h: Math.round(opt.getBoundingClientRect().height),
                             bg: ocs.backgroundColor, radius: ocs.borderRadius,
                             fontSize: ocs.fontSize, cursor: ocs.cursor} : null};
    })()""")
    after = s.eval(f"(() => {{ const e = ({trig}); return e && e.getAttribute('aria-expanded'); }})()")
    s.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
    s.eval("document.body.click()")
    time.sleep(0.5)
    return {"expanded_before": before, "expanded_after": after,
            "panel": panel if isinstance(panel, dict) else json.loads(panel or "{}")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_states", description="采集控件状态差分")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)

    from tools.cdp import Session

    os.makedirs(OUT, exist_ok=True)
    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    result: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pages": {}}
    for page, names in PAGES.items():
        if args.only and args.only != page:
            continue
        print(f"[states] {page} …", flush=True)
        s.cmd("Page.navigate", {"url": f"{REPO}/{'' if page == 'repo' else page}"})
        time.sleep(6)
        for _ in range(6):
            if (s.eval("document.querySelectorAll('*').length") or 0) > 1200:
                break
            time.sleep(2)
        rows = [capture(s, n, EXPR[n]) for n in names]
        result["pages"][page] = rows
        print("    → " + ", ".join(f"{r['name']}{'✗' if r.get('missing') else '✓'}" for r in rows), flush=True)
        if page == "issues":
            result["panel_author"] = capture_panel(s)

    path = os.path.join(OUT, "states.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("写入", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
