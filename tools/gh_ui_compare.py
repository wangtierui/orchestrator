# -*- coding: utf-8 -*-
"""tools/gh_ui_compare.py — 响应式基线对比器：同一批宽度下，工作台 vs GitHub。

解决什么
--------
`gh_ui_overlays --viewports` 只采 GitHub 一端。要回答"我们窄屏是不是和 GitHub 一样"
必须**两端同宽度、同口径**量：控件在不在、宽度差多少、字号差多少。

口径
  · 宽度组：默认 1440/1012/768/544（GitHub Primer 断点）；
  · 每个宽度：`Emulation.setDeviceMetricsOverride` → 打开目标页 → 量映射到的选择器；
  · 输出：宽度 × 控件 的「GitHub 尺寸 / 工作台尺寸 / 差值与可见性是否一致」；
  · 映射不存在（如工作台没有顶栏快速搜索）就记 `N/A`，**不编造 0**。

用法：
  python -m tools.gh_ui_compare                       # 默认比 #laws 页
  python -m tools.gh_ui_compare --target "http://127.0.0.1:8765/#worklist" --slug worklist
  python -m tools.gh_ui_compare --widths 1440,1012
环境变量：CDP_PORT、CDP_NO_SPAWN=1（连 GitHub 那一端用；工作台用同一浏览器另开标签）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")

#: GitHub 控件名 → 工作台选择器（None = 我们没有对应控件）
DEFAULT_MAP = {
    "list-search": {"sel": "#lvfq, input[type=search]"},
    "density": {"sel": "[data-lvdensity]", "union": True},
    "filter-author": {"sel": ".lv-fbtn"},
    "tab-open": {"sel": None},
    "top-search": {"sel": None},
}

MEASURE = r"""(() => {
  const els = [...document.querySelectorAll(SEL)];
  if (!els.length) return {present: false};
  if (UNION && els.length > 1) {
    const rs = els.map(e => e.getBoundingClientRect());
    const l = Math.min(...rs.map(r => r.left)), tp = Math.min(...rs.map(r => r.top));
    const rt = Math.max(...rs.map(r => r.right)), bt = Math.max(...rs.map(r => r.bottom));
    return {present: true, visible: rt - l > 0, w: Math.round(rt - l), h: Math.round(bt - tp),
            x: Math.round(l), fontSize: getComputedStyle(els[0]).fontSize, display: 'union'};
  }
  const el = els[0];
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return {present: true, visible: r.width > 0 && r.height > 0,
          w: Math.round(r.width), h: Math.round(r.height),
          x: Math.round(r.left), fontSize: cs.fontSize, display: cs.display};
})()"""


def measure(s, spec: dict) -> dict:
    sel = (spec or {}).get("sel")
    if not sel:
        return {"mapped": False}
    js = (MEASURE.replace("SEL", json.dumps(sel.split(",")[0].strip()))
          .replace("UNION", "true" if spec.get("union") else "false"))
    v = s.eval(js)
    return v if isinstance(v, dict) else {"present": False}


def fmt(cell: dict) -> str:
    if cell.get("mapped") is False:
        return "N/A"
    if not cell.get("present"):
        return "缺"
    if not cell.get("visible"):
        return "隐藏"
    return f"{cell['w']}×{cell['h']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_compare")
    ap.add_argument("--base", default=os.path.join(BASE, "responsive.json"))
    ap.add_argument("--target", default="http://127.0.0.1:8765/#laws")
    ap.add_argument("--slug", default="laws")
    ap.add_argument("--widths", default="1440,1012,768,544")
    args = ap.parse_args(argv)

    widths = [int(x) for x in args.widths.split(",") if x.strip()]
    base = json.load(open(args.base, encoding="utf-8"))

    sys.path.insert(0, os.path.join(ROOT, "reports", "_tmp"))
    from cdp import Session

    s = Session()
    rows: dict[str, dict] = {}
    for w in widths:
        print(f"[compare] {w} …", flush=True)
        s.cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        s.cmd("Page.navigate", {"url": args.target})
        for _ in range(12):
            time.sleep(1.2)
            if s.eval("document.querySelectorAll('#view *').length") and \
                    (s.eval("document.querySelectorAll('#view *').length") or 0) > 40:
                break
        got = {name: measure(s, spec) for name, spec in DEFAULT_MAP.items()}
        rows[str(w)] = got

    out = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "target": args.target, "widths": rows, "map": DEFAULT_MAP}
    path = os.path.join(BASE, f"compare-{args.slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    # 汇总表
    lines = [f"# 响应式对比：GitHub vs 工作台（{args.slug}）\n",
             f"- 目标：`{args.target}`",
             f"- 基线：`{os.path.basename(args.base)}`",
             "- 口径：同宽度、同控件语义；`N/A` = 工作台没有对应控件\n",
             "| 控件 | " + " | ".join(str(w) for w in widths) + " |",
             "|---" * (len(widths) + 1) + "|"]
    for name in DEFAULT_MAP:
        cells = []
        for w in widths:
            gh = ((base.get("widths", {}).get(str(w)) or {}).get(name) or {})
            gh_txt = "—" if not gh.get("present") else (
                f"{gh.get('rect', [0, 0, 0, 0])[2]}×{gh.get('rect', [0, 0, 0, 0])[3]}"
                if gh.get("visible") else "隐藏")
            mine = rows[str(w)][name]
            cells.append(f"{gh_txt} → {fmt(mine)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    md = os.path.join(BASE, f"COMPARE-{args.slug}.md")
    open(md, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("写入", path, "|", md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
