# -*- coding: utf-8 -*-
"""tools/gh_ui_styles.py — 采集「完整计算样式 + 祖先链」（补上只抓几何的缺口）。

为什么需要
----------
旧基线每个元素只记 8 个属性（rect/字号/几处颜色）。复刻一个盒子需要的是
padding / border / line-height / font / box-sizing / gap / 祖先背景 —— 缺一项就会
"数值对齐、观感不像"。这个工具对目标元素把 **全部 computed style** 拉下来，
并记录祖先链（GitHub 的底色常常落在父容器上，只看元素本身会误判"无变化"）。

用法：
  python -m tools.gh_ui_styles                     # GitHub issues
  python -m tools.gh_ui_styles --target "http://127.0.0.1:8765/#worklist" --slug wb
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")

GH = "https://github.com/notepad-plus-plus/notepad-plus-plus"

GH_TARGETS = [
    ("search-box", "#issues-index-filter-input"),
    ("search-wrap", "#issues-index-filter-input"),
    ("toolbar", "nav[aria-label='Issues']"),
    ("filter-btn", "button[aria-label*='Filter by author'], button[aria-label*='author']"),
    ("headrow", "[role='row'], .Box-header"),
    ("row", "li[role='listitem'], [data-testid='issue-row']"),
    ("density", "ul[aria-label*='density']"),
    ("pagination", "nav[aria-label*='agination']"),
]

WB_TARGETS = [
    ("search-box", ".lv-fbox input"),
    ("search-wrap", ".lv-fbox"),
    ("toolbar", ".lv-headbar"),
    ("filter-btn", ".lv-fbtn"),
    ("headrow", ".lv-headrow"),
    ("row", ".lv-row"),
    ("density", ".density"),
    ("pagination", "[class*='pager'], [class*='page']"),
]

FULL = """(() => {
  const out = {};
  for (const [name, sel] of TARGETS) {
    const e = document.querySelector(sel);
    if (!e) { out[name] = {missing: sel}; continue; }
    const cs = getComputedStyle(e);
    const style = {};
    for (let i = 0; i < cs.length; i++) {
      const p = cs[i];
      style[p] = cs.getPropertyValue(p);
    }
    const chain = [];
    let p = e;
    for (let d = 0; d < 5 && p && p !== document.documentElement; d++) {
      const c = getComputedStyle(p);
      const r = p.getBoundingClientRect();
      chain.push({
        tag: p.tagName, cls: String(p.className || "").slice(0, 60),
        rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
        bg: c.backgroundColor, border: c.borderTopWidth + " " + c.borderTopStyle + " " + c.borderTopColor,
        radius: c.borderRadius, shadow: c.boxShadow, padding: c.padding,
      });
      p = p.parentElement;
    }
    out[name] = {sel, style, chain};
  }
  return out;
})()"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_styles")
    ap.add_argument("--target", default=f"{GH}/issues")
    ap.add_argument("--slug", default="gh-issues-styles")
    ap.add_argument("--width", type=int, default=1440)
    args = ap.parse_args(argv)

    from tools.cdp import Session

    is_wb = "127.0.0.1" in args.target or "localhost" in args.target
    targets = WB_TARGETS if is_wb else GH_TARGETS

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": args.width, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.navigate", {"url": args.target})
    time.sleep(7)

    js = FULL.replace("TARGETS", json.dumps(targets, ensure_ascii=False))
    data = s.eval(js)
    if not isinstance(data, dict):
        try:
            data = json.loads(data or "{}")
        except Exception:  # noqa: BLE001
            data = {"raw": str(data)[:500]}

    path = os.path.join(BASE, f"styles-{args.slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"target": args.target, "width": args.width,
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "elements": data},
                  fh, ensure_ascii=False, indent=1)

    print(f"采集 {len(data)} 个元素（每元素 ~340 条属性 + 5 层祖先）→ {path}")
    for k, v in data.items():
        if v.get("missing"):
            print(f"  {k:12s} 缺（{v['missing']}）")
            continue
        st = v["style"]
        print(f"  {k:12s} h={st.get('height')} pad={st.get('padding')} "
              f"border={st.get('border-top-width')} radius={st.get('border-radius')} "
              f"bg={st.get('background-color')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
