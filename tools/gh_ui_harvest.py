# -*- coding: utf-8 -*-
"""tools/gh_ui_harvest.py — 采集 GitHub 页面 UI 基线（供工作台复用）。

为什么单独一个工具（而不是一次性脚本）：
  用户在上一轮反复要求"与 GitHub 保持一致"，而口头描述会漂移（"图标在左/在右"、
  "单选有没有勾选框"），必须留下**可复核的本地证据**：DOM + 每个交互元素的语义 +
  弹出层结构 + 计算后的样式。以后改 UI 直接查这份基线，而不是重新猜。

采什么（三层，缺一层就会漏）：
  ① 页面层：整页 HTML（渲染后）+ 地标/标题/表格/计数；
  ② 交互层：所有可见的 a/button/input/select/textarea/summary/[role]/[tabindex]，
     带 aria-label / aria-pressed / aria-expanded / title / data-testid / 尺寸；
  ③ 弹出层：只在点击后才存在的东西（表头筛选面板、密度开关、排序菜单…），
     逐个点开 → 抓 outerHTML + 计算样式 → 关掉。

输出（默认 reports/github-baseline/）：
  <slug>.html      整页 DOM（*.html 已 gitignore：体积大、且是第三方内容）
  <slug>.json      页面清单（地标/标题/交互/表格/同仓链接）
  components.json  弹出层与关键控件的结构与样式
  index.json       总索引（17 页的统计与抓取时间）
  README.md        人类可读摘要（含"采不到什么"的边界说明）

用法：
  python -m tools.gh_ui_harvest                 # 全量 17 页
  python -m tools.gh_ui_harvest --only issues   # 只抓 slug 含 issues 的页
  python -m tools.gh_ui_harvest --no-html       # 只抓清单，不存整页 HTML
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "github-baseline")
REPO = "https://github.com/notepad-plus-plus/notepad-plus-plus"

PAGES = [
    ("repo", REPO),
    ("issues", f"{REPO}/issues"),
    ("pulls", f"{REPO}/pulls"),
    ("agents", f"{REPO}/agents"),
    ("actions", f"{REPO}/actions"),
    ("projects", f"{REPO}/projects"),
    ("wiki", f"{REPO}/wiki"),
    ("security", f"{REPO}/security"),
    ("pulse", f"{REPO}/pulse"),
    ("graphs-contributors", f"{REPO}/graphs/contributors"),
    ("graphs-commit-activity", f"{REPO}/graphs/commit-activity"),
    ("graphs-code-frequency", f"{REPO}/graphs/code-frequency"),
    ("network-dependencies", f"{REPO}/network/dependencies"),
    ("network", f"{REPO}/network"),
    ("forks", f"{REPO}/forks"),
    ("actions-metrics-usage", f"{REPO}/actions/metrics/usage"),
    ("actions-metrics-performance", f"{REPO}/actions/metrics/performance"),
]

#: 交互元素清单（一次取全，前端 JS 单次往返）
JS_INVENTORY = r"""JSON.stringify((() => {
  const vis = (e) => { const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0; };
  const sel = 'a,button,input,select,textarea,summary,[role],[tabindex]';
  const seen = new Set();
  const items = [];
  for (const e of document.querySelectorAll(sel)) {
    if (seen.has(e) || !vis(e)) continue;
    seen.add(e);
    const r = e.getBoundingClientRect();
    if (items.length >= 1400) break;
    items.push({
      tag: e.tagName, role: e.getAttribute('role') || '',
      text: (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90),
      aria: e.getAttribute('aria-label') || '', title: e.title || '',
      pressed: e.getAttribute('aria-pressed') || '',
      expanded: e.getAttribute('aria-expanded') || '',
      checked: e.getAttribute('aria-checked') || '',
      testid: e.getAttribute('data-testid') || '',
      type: e.getAttribute('type') || '', href: (e.getAttribute('href') || '').slice(0, 200),
      cls: String(e.className || '').slice(0, 70),
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      disabled: !!e.disabled,
    });
  }
  const heads = [...document.querySelectorAll('h1,h2,h3,h4')]
    .map(h => [h.tagName, (h.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80)]).slice(0, 60);
  const marks = ['header', 'nav', 'main', 'aside', 'footer'].map(t => {
    const e = document.querySelector(t); if (!e) return null;
    const r = e.getBoundingClientRect();
    return [t, Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  }).filter(Boolean);
  const tables = [...document.querySelectorAll('table')].slice(0, 6).map(t => {
    const hs = [...t.querySelectorAll('th')].map(x => (x.textContent || '').trim().slice(0, 24));
    const rows = [...t.querySelectorAll('tbody tr')].slice(0, 3)
      .map(tr => [...tr.children].map(td => (td.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30)));
    return {headers: hs, rows: rows};
  });
  const links = [...new Set([...document.querySelectorAll('a[href]')]
    .map(a => a.getAttribute('href'))
    .filter(h => h && h.startsWith('/notepad-plus-plus/notepad-plus-plus')))].slice(0, 400);
  return {
    title: document.title, url: location.href,
    user_login: (document.querySelector('meta[name="user-login"]') || {}).content || '',
    ready: document.readyState,
    counts: {els: document.querySelectorAll('*').length,
             interactive: items.length, links: links.length},
    landmarks: marks, headings: heads, interactive: items, tables: tables, same_repo_links: links,
    density: (() => {
      const ul = document.querySelector('ul[aria-label$="display density"]');
      if (!ul) return null;
      return {rect: [Math.round(ul.getBoundingClientRect().left), Math.round(ul.getBoundingClientRect().width)],
              items: [...ul.querySelectorAll('li')].map(li => {
                const b = li.querySelector('button'); const sv = b && b.querySelector('svg');
                const tip = li.querySelector('[data-component="Tooltip"]');
                return {pressed: b ? b.getAttribute('aria-pressed') : null,
                        icon: sv ? (sv.getAttribute('class') || '').split(' ')
                          .find(c => c.startsWith('octicon-')) : '',
                        tip: tip ? tip.textContent.trim() : ''};
              })};
    })(),
  };
})())"""

#: 弹出层抓取（点开 → 抓 → 关掉）
PANEL_JS = r"""JSON.stringify((() => {
  const p = document.querySelector('.prc-SelectPanel-Overlay-tEbxh, [role="dialog"], .Popover');
  if (!p) return {none: true};
  const cs = getComputedStyle(p);
  const inp = p.querySelector('input');
  const rows = [...p.querySelectorAll('[role="option"]')].slice(0, 4).map(r => ({
    text: (r.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 26),
    checkbox: !!r.querySelector('input[type=checkbox], [class*=Checkbox]'),
    role: r.getAttribute('role'),
    checked: r.getAttribute('aria-selected') || r.getAttribute('aria-checked')}));
  return {cls: String(p.className).slice(0, 60),
          rect: [Math.round(p.getBoundingClientRect().left), Math.round(p.getBoundingClientRect().top),
                 Math.round(p.getBoundingClientRect().width)],
          radius: cs.borderRadius, border: cs.borderTopWidth + '/' + cs.borderTopColor,
          shadow: cs.boxShadow.slice(0, 90),
          input: inp ? {ph: inp.placeholder, left: Math.round(inp.getBoundingClientRect().left),
                        iconOnLeft: (() => { const b = inp.closest('div');
                          if (!b) return null;
                          const sv = b.querySelector('svg');
                          return sv ? sv.getBoundingClientRect().left < inp.getBoundingClientRect().left : null; })()}
                      : null,
          optionChecked: rows.some(r => r.checkbox),
          rows: rows,
          html: p.outerHTML.replace(/\s+/g, ' ').slice(0, 5000)};
})())"""

CLICK_JS = "document.querySelector('%s')?.click()"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")


def _session():
    sys.path.insert(0, os.path.join(ROOT, "reports", "_tmp"))
    from cdp import Session

    return Session()


def _wait_ready(s, timeout: float = 30.0) -> int:
    """等页面稳定：元素数连续两次一致（GitHub 是 React 渐进渲染）。"""
    last, stable, t0 = -1, 0, time.time()
    while time.time() - t0 < timeout:
        try:
            n = s.eval("document.querySelectorAll('*').length") or 0
        except Exception:  # noqa: BLE001 导航中
            n = 0
        if isinstance(n, int) and n == last and n > 200:
            stable += 1
            if stable >= 2:
                return n
        else:
            stable = 0
        last = n if isinstance(n, int) else last
        time.sleep(1.2)
    return last if isinstance(last, int) else 0


def harvest_page(s, slug: str, url: str, *, save_html: bool) -> dict:
    s.cmd("Page.navigate", {"url": url})
    time.sleep(2.0)
    n = _wait_ready(s)
    data = json.loads(s.eval(JS_INVENTORY) or "{}")
    data["fetched_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    data["elements_seen"] = n
    path = os.path.join(OUT, f"{slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    if save_html:
        html = s.eval("document.documentElement.outerHTML") or ""
        with open(os.path.join(OUT, f"{slug}.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
    return data


def harvest_components(s) -> dict:
    """只对 issues/pulls 这类"点了才出现"的控件做结构化采集。"""
    out: dict = {"fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}

    # 密度开关（列表表头右侧：Comfortable / Compact）
    out["density"] = json.loads(s.eval(
        "JSON.stringify((() => {"
        "  const ul = document.querySelector('ul[aria-label$=\"display density\"]');"
        "  if (!ul) return null;"
        "  const r = ul.getBoundingClientRect();"
        "  return {rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],"
        "    cls: String(ul.className).slice(0, 50),"
        "    items: [...ul.querySelectorAll('li')].map(li => {"
        "      const b = li.querySelector('button');"
        "      const sv = b && b.querySelector('svg');"
        "      const tip = li.querySelector('[data-component=\"Tooltip\"]');"
        "      return {pressed: b && b.getAttribute('aria-pressed'),"
        "        icon: sv ? (sv.getAttribute('class')||'').split(' ').find(c => c.startsWith('octicon-')) : '',"
        "        tip: tip ? tip.textContent.trim() : '',"
        "        size: b ? [Math.round(b.getBoundingClientRect().width), Math.round(b.getBoundingClientRect().height)] : null};"
        "    }),"
        "    html: ul.outerHTML.replace(/\\s+/g, ' ').slice(0, 3000)};"
        "})())") or "null")

    # 表头筛选面板：Author（单选）/ Labels（多选）/ Sort
    out["panels"] = {}
    for name, sel in (("author", '[data-testid="authors-anchor-button"]'),
                      ("labels", '[data-testid="labels-anchor-button"]'),
                      ("sort", '[data-testid="sort-anchor-button"]')):
        if not s.eval(f"!!document.querySelector('{sel}')"):
            out["panels"][name] = {"missing": True}
            continue
        s.eval(CLICK_JS % sel)
        time.sleep(1.2)
        out["panels"][name] = json.loads(s.eval(PANEL_JS) or "{}")
        s.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        s.eval("document.body.click()")
        time.sleep(0.7)

    # 搜索框（列表上方）
    out["search"] = json.loads(s.eval(
        "JSON.stringify((() => {"
        "  const i = document.querySelector('#issues-index-filter-input, input[placeholder*=\"Search\"]');"
        "  if (!i) return null;"
        "  const box = i.closest('div[class*=leftRounded]') || i.parentElement.parentElement;"
        "  i.focus();"
        "  const cs = getComputedStyle(box), ic = getComputedStyle(i);"
        "  const r = i.getBoundingClientRect(), br = box.getBoundingClientRect();"
        "  return {rect: [Math.round(r.left), Math.round(r.width), Math.round(r.height)],"
        "    box: [Math.round(br.left), Math.round(br.right)],"
        "    input_bg: ic.backgroundColor, box_border: cs.borderTopWidth + '/' + cs.borderTopColor,"
        "    focus_outline: cs.outlineWidth + '/' + cs.outlineStyle + '/' + cs.outlineColor,"
        "    focus_shadow: cs.boxShadow, radius: cs.borderRadius,"
        "    html: box.outerHTML.replace(/\\s+/g, ' ').slice(0, 3000)};"
        "})())") or "null")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_harvest", description="采集 GitHub UI 基线")
    ap.add_argument("--only", default="", help="只抓 slug 含该子串的页")
    ap.add_argument("--no-html", action="store_true", help="不保存整页 HTML（只存清单）")
    args = ap.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    s = _session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    index: list[dict] = []
    for slug, url in PAGES:
        if args.only and args.only not in slug:
            continue
        print(f"[harvest] {slug} …", flush=True)
        try:
            d = harvest_page(s, slugify(slug), url, save_html=not args.no_html)
            index.append({"slug": slugify(slug), "url": url, "title": d.get("title", ""),
                          "elements": d.get("elements_seen", 0),
                          "interactive": d.get("counts", {}).get("interactive", 0),
                          "links": d.get("counts", {}).get("links", 0)})
            print(f"    → {d.get('title', '')[:52]} | 交互 {d.get('counts', {}).get('interactive', 0)}", flush=True)
        except Exception as e:  # noqa: BLE001 单页失败不阻断其余
            index.append({"slug": slugify(slug), "url": url, "error": f"{type(e).__name__}: {e}"})
            print(f"    ✗ {type(e).__name__}: {e}", flush=True)

    # 弹出层只在 issues 采（结构同源于 Primer SelectPanel，pulls 复用同一套）
    if not args.only or args.only == "issues":
        print("[harvest] components …", flush=True)
        try:
            s.cmd("Page.navigate", {"url": f"{REPO}/issues"})
            time.sleep(3)
            _wait_ready(s)
            comp = harvest_components(s)
            with open(os.path.join(OUT, "components.json"), "w", encoding="utf-8") as fh:
                json.dump(comp, fh, ensure_ascii=False, indent=1)
            print("    → components.json 已写入", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ components 失败：{type(e).__name__}: {e}", flush=True)

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                   "viewport": "1440x1000", "pages": index}, fh, ensure_ascii=False, indent=1)
    print(f"[harvest] 完成：{len(index)} 页 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
