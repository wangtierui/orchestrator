# -*- coding: utf-8 -*-
"""tools/gh_ui_full.py — 按「语义角色」批量采集 GitHub 元素的**完整计算样式 + 祖先链**。

为什么需要它
------------
旧基线每个控件只记 8 个属性（rect / font-size / 几个颜色 / 圆角 / 边框宽）。
要复刻一个盒子，需要的是整条链：padding + border + line-height + font-family +
box-sizing + gap + **祖先背景**（很多底色落在父容器上，只看元素本身会误判"无变化"）。

本工具按角色（搜索框/按钮/链接/表头/行/分隔线/徽标/分页/标题/输入/下拉/图片）
各取若干代表元素，每元素采集：
  · 全部 computed style（约 300+ 属性）
  · 祖先链最多 6 层（每层：tag/class/rect/背景/边框/内距/字号）
  · 文本度量（Range 实测文字宽 → 用于验证「按钮宽 = 文字 + padding×2 + border×2」）
并做去重（同角色同尺寸同内距只留一个），避免 200 行列表刷屏。

用法：
    python -m tools.gh_ui_full --url "https://github.com/o/r/issues" --slug issues
    python -m tools.gh_ui_full --url ... --slug ... --width 1440 --max 6
产物：reports/github-baseline/full-<slug>.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "reports", "github-baseline")

#: 角色 → 候选选择器（按优先级，取可见元素）
ROLES = {
    "search": ["input[type=search]", "input[name=q]", "#issues-index-filter-input",
               "[data-target*='search'] input", "input[placeholder*='Search']"],
    "input": ["input[type=text]", "input:not([type])", "textarea"],
    "button": ["button", "summary", "[role=button]"],
    "link": ["a[href]"],
    "table_header": ["[role=columnheader]", "th", ".Box-header", "[class*='header'] th"],
    "row": ["[role=listitem]", "li[role=listitem]", "[data-testid='issue-row']",
            "table tbody tr", "[class*='ListItem']"],
    "divider": ["hr", "[role=separator]"],
    "badge": ["[class*='Label']", "[class*='label']", ".IssueLabel", "[data-testid='label']"],
    "pagination": ["nav[aria-label*='agination']", "[class*='pagination']", "[class*='Pagination']"],
    "heading": ["h1", "h2", "h3"],
    "select": ["select", "[role=combobox]"],
    "avatar": ["img.avatar", "img[class*='avatar']"],
}

JS = r"""(() => {
  const ROLES = __ROLES__;
  const MAX = __MAX__;

  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && cs.visibility !== "hidden" && cs.display !== "none";
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left), y: Math.round(r.top),
             w: Math.round(r.width), h: Math.round(r.height) };
  };
  const textW = (el) => {
    try {
      const r = document.createRange();
      r.selectNodeContents(el);
      const b = r.getBoundingClientRect();
      return Math.round(b.width * 10) / 10;
    } catch (e) { return null; }
  };

  /* 全部 computed style（键值对，保留长手属性） */
  const fullStyle = (el) => {
    const cs = getComputedStyle(el);
    const o = {};
    for (let i = 0; i < cs.length; i++) {
      const p = cs[i];
      o[p] = cs.getPropertyValue(p);
    }
    return o;
  };

  /* 祖先链：背景/边框/内距/字号常落在父级 */
  const chain = (el, depth) => {
    const out = [];
    let p = el.parentElement;
    for (let i = 0; i < depth && p && p !== document.documentElement; i++) {
      const cs = getComputedStyle(p);
      out.push({
        tag: p.tagName, cls: String(p.className || "").slice(0, 50),
        box: box(p), bg: cs.backgroundColor,
        border: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].join("/"),
        borderColor: cs.borderTopColor, radius: cs.borderRadius,
        padding: cs.padding, font: cs.fontSize + "/" + cs.lineHeight,
      });
      p = p.parentElement;
    }
    return out;
  };

  const out = { roles: {}, counts: {} };
  for (const [role, sels] of Object.entries(ROLES)) {
    const seen = new Set();
    const picked = [];
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) {
        if (!vis(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        const cs = getComputedStyle(el);
        const key = [Math.round(r.width), Math.round(r.height), cs.fontSize,
                     cs.padding, cs.borderTopWidth].join("|");
        if (seen.has(key)) continue;
        seen.add(key);
        picked.push({
          selector: sel, tag: el.tagName,
          cls: String(el.className || "").slice(0, 60),
          text: (el.innerText || el.value || el.getAttribute("aria-label") || "")
                     .replace(/\s+/g, " ").trim().slice(0, 40),
          box: box(el),
          textWidth: el.children.length === 0 ? textW(el) : null,
          style: fullStyle(el),
          chain: chain(el, 6),
        });
        if (picked.length >= MAX) break;
      }
      if (picked.length >= MAX) break;
    }
    if (picked.length) {
      out.roles[role] = picked;
      out.counts[role] = document.querySelectorAll(sels.join(",")).length;
    }
  }
  out.url = location.href;
  out.viewport = [window.innerWidth, window.innerHeight];
  out.title = document.title.slice(0, 60);
  return JSON.stringify(out);
})()"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_full")
    ap.add_argument("--url", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--wait", type=float, default=9.0)
    args = ap.parse_args(argv)

    from tools.cdp import Session  # 延迟导入：避免无浏览器环境时报错

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.navigate", {"url": args.url})
    time.sleep(args.wait)

    js = JS.replace("__ROLES__", json.dumps(ROLES, ensure_ascii=False)).replace("__MAX__", str(args.max))
    raw = s.eval(js)
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"full-{args.slug}.json")
    data["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["slug"] = args.slug
    open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=1))

    print(f"页面: {data.get('title')} | {data.get('url')}")
    print(f"视口: {data.get('viewport')}")
    for role, items in (data.get("roles") or {}).items():
        b = items[0]["box"]
        print(f"  {role:13s} 候选 {data['counts'].get(role, 0):>4} · 采集 {len(items)} · "
              f"首例 {b['w']}×{b['h']} · {items[0]['tag']}.{items[0]['cls'][:24]}")
    print("→", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
