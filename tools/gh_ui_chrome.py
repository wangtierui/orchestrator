# -*- coding: utf-8 -*-
"""tools/gh_ui_chrome.py — 顶栏与左侧边栏的对照测量（GitHub vs 工作台）。

为什么单列一个工具
------------------
之前的对比器只比"列表内的元素"，顶栏（logo/搜索/头像/页签）与左侧边栏
（分区标题/筛选项/选中态）从没量过；而这两块恰恰是最先被看到的界面语言。

量什么（每侧 × 每个宽度）
  顶栏：高度、logo 尺寸、搜索入口、头像、页签数量与高度、右侧图标按钮尺寸
  侧栏：宽度、条目高度、条目内边距、分区标题字号、选中态（底色/左边框/字重）
宽度：1440 / 1012 / 768（分辨率差异会改变顶栏是否折叠、侧栏是否收成抽屉）

用法：
  python -m tools.gh_ui_chrome
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")

GH_JS = """(() => {
  const R = (e) => e ? e.getBoundingClientRect() : null;
  const px = (n) => Math.round(n);
  const header = document.querySelector('header, .AppHeader');
  const hr = R(header);
  const logo = document.querySelector('header a[href="/"], .AppHeader-logo, header svg.octicon-mark-github');
  const search = [...document.querySelectorAll('header button')]
    .find(e => /quick search/i.test(e.getAttribute('aria-label') || ''));
  const avatar = [...document.querySelectorAll('header button')].find(b => b.querySelector('img'));
  const tabs = [...document.querySelectorAll('nav a, [role="tab"]')]
    .filter(e => { const r = R(e); return r && r.height > 20 && r.top < 400; }).slice(0, 8);
  const side = document.querySelector('[class*=sidebar], aside');
  const sr = R(side);
  const items = [...document.querySelectorAll('aside a, [class*=sidebar] a')].slice(0, 6);
  return {
    header: hr ? [px(hr.height), px(hr.width)] : null,
    logo: logo ? [px(R(logo).width), px(R(logo).height)] : null,
    search: search ? [px(R(search).width), px(R(search).height)] : null,
    avatar: avatar ? [px(R(avatar).width), px(R(avatar).height)] : null,
    tabs: tabs.map(t => [px(R(t).width), px(R(t).height)]),
    tabN: tabs.length,
    side: sr ? [px(sr.width), px(sr.height)] : null,
    sideItem: items.length ? [px(R(items[0]).width), px(R(items[0]).height)] : null,
  };
})()"""

WB_JS = """(() => {
  const R = (e) => e ? e.getBoundingClientRect() : null;
  const px = (n) => Math.round(n);
  const header = document.querySelector('.topbar, header, #top');
  const hr = R(header);
  const brand = document.querySelector('.brand, .topbar .logo, .topbar a');
  const search = document.querySelector('#gsBtn, .topbar input, .topbar .search');
  const avatar = document.querySelector('.topbar .avatar, #userBox');
  const rail = document.querySelector('#rail');
  const rr = R(rail);
  const items = [...document.querySelectorAll('#rail .sideitem')].slice(0, 6);
  const first = items[0] ? getComputedStyle(items[0]) : null;
  const on = document.querySelector('#rail .sideitem.on');
  const ocs = on ? getComputedStyle(on) : null;
  return {
    header: hr ? [px(hr.height), px(hr.width)] : null,
    brand: brand ? [px(R(brand).width), px(R(brand).height)] : null,
    search: search ? [px(R(search).width), px(R(search).height)] : null,
    avatar: avatar ? [px(R(avatar).width), px(R(avatar).height)] : null,
    rail: rr ? [px(rr.width), px(rr.height)] : null,
    item: items.length ? [px(R(items[0]).width), px(R(items[0]).height)] : null,
    itemPad: first ? first.padding : null,
    itemFs: first ? first.fontSize : null,
    on: ocs ? {bg: ocs.backgroundColor, weight: ocs.fontWeight, borderLeft: ocs.borderLeftWidth} : null,
    railHidden: rail ? getComputedStyle(rail).display === 'none' || rr.width === 0 : null,
  };
})()"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_chrome")
    ap.add_argument("--widths", default="1440,1012,768")
    args = ap.parse_args(argv)
    widths = [int(x) for x in args.widths.split(",") if x.strip()]

    sys_path = os.path.join(ROOT, "reports", "_tmp")
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from tools.cdp import Session

    s = Session()
    out: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "widths": {}}
    for w in widths:
        s.cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        print(f"[chrome] {w} …", flush=True)
        s.cmd("Page.navigate", {"url": "https://github.com/notepad-plus-plus/notepad-plus-plus/issues"})
        time.sleep(6)
        gh = s.eval(GH_JS)
        s.cmd("Page.navigate", {"url": "http://127.0.0.1:8765/#worklist"})
        time.sleep(4)
        wb = s.eval(WB_JS)
        out["widths"][str(w)] = {"gh": gh, "wb": wb}

    with open(os.path.join(BASE, "chrome.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    L = ["# 顶栏与左侧边栏对照\n", "| 项 | 宽度 | GitHub | 工作台 |", "|---|---|---|---|"]
    for w in widths:
        d = out["widths"][str(w)]
        gh, wb = d["gh"], d["wb"]
        pairs = [("顶栏高", gh.get("header"), wb.get("header")),
                 ("Logo/品牌", gh.get("logo"), wb.get("brand")),
                 ("搜索入口", gh.get("search"), wb.get("search")),
                 ("头像", gh.get("avatar"), wb.get("avatar")),
                 ("侧栏宽", gh.get("side"), wb.get("rail")),
                 ("侧栏条目", gh.get("sideItem"), wb.get("item"))]
        for name, a, b in pairs:
            L.append(f"| {name} | {w} | {a} | {b} |")
    md = os.path.join(BASE, "CHROME.md")
    open(md, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("写入", md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
