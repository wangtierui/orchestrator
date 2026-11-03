# -*- coding: utf-8 -*-
"""tools/gh_ui_matrix.py — 按页面映射做批量对比，并量图表栅格间距。

映射（用户指定，2026-11-03）
---------------------------
  工作台                ↔  GitHub 参考
  feedback/search/rules/laws/worklist/regstats  ↔  /pulls
  release               ↔  /commits/master
  overview/metrics      ↔  /graphs/contributors
  （security/advisories/new 作为"表单作业类"参考，单独采）

两种测量
  --matrix   逐对跑全元素分类对比（复用 gh_ui_compare 的抓取与统计）
  --spacing  图表栅格：相邻图表的水平/垂直间距、卡片内边距、边框、period 控件尺寸
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")
GH = "https://github.com/notepad-plus-plus/notepad-plus-plus"

PAIRS = [
    ("feedback", f"{GH}/pulls", "http://127.0.0.1:8765/#feedback"),
    ("search", f"{GH}/pulls", "http://127.0.0.1:8765/#search"),
    ("rules", f"{GH}/pulls", "http://127.0.0.1:8765/#rules"),
    ("laws", f"{GH}/pulls", "http://127.0.0.1:8765/#laws"),
    ("worklist", f"{GH}/pulls", "http://127.0.0.1:8765/#worklist"),
    ("regstats", f"{GH}/pulls", "http://127.0.0.1:8765/#regstats"),
    ("release", f"{GH}/commits/master", "http://127.0.0.1:8765/#release"),
    ("overview", f"{GH}/graphs/contributors", "http://127.0.0.1:8765/#overview"),
    ("metrics", f"{GH}/graphs/contributors", "http://127.0.0.1:8765/#metrics"),
]

SPACING_JS = """(() => {
  const boxes = [...document.querySelectorAll(SEL)]
    .map(e => e.getBoundingClientRect())
    .filter(r => r.width > 180 && r.height > 60)
    .sort((a, b) => (a.top - b.top) || (a.left - b.left))
    .map(r => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]);
  const v = [], h = [];
  for (let i = 1; i < boxes.length; i++) {
    const a = boxes[i - 1], b = boxes[i];
    if (Math.abs(a[0] - b[0]) < 24) v.push(b[1] - (a[1] + a[3]));
    if (Math.abs(a[1] - b[1]) < 24) h.push(b[0] - (a[0] + a[2]));
  }
  const el = document.querySelector(SEL);
  const cs = el ? getComputedStyle(el) : null;
  const period = [...document.querySelectorAll('button,summary,select')]
    .map(e => [e.textContent.trim().slice(0, 20), Math.round(e.getBoundingClientRect().width),
               Math.round(e.getBoundingClientRect().height)])
    .find(x => /period|month|year/i.test(x[0])) || null;
  return {n: boxes.length, vgaps: v, hgaps: h,
          border: cs ? cs.borderTopWidth + '/' + cs.borderTopColor : null,
          radius: cs ? cs.borderRadius : null, padding: cs ? cs.padding : null,
          period: period};
})()"""


def med(xs: list[int]) -> str:
    return str(round(st.median(xs))) if xs else "—"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_matrix")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--spacing", action="store_true")
    ap.add_argument("--width", type=int, default=1440)
    args = ap.parse_args(argv)
    if not args.matrix and not args.spacing:
        args.matrix = args.spacing = True

    from tools import gh_ui_compare as C
    from tools.cdp import Session

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": args.width, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    out: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "width": args.width,
                 "pairs": [], "spacing": {}}

    if args.matrix:
        for name, gh_url, mine_url in PAIRS:
            print(f"[matrix] {name} …", flush=True)
            gh = C.measure_page(s, gh_url, args.width, "main")
            mine = C.measure_page(s, mine_url, args.width, "#view")
            gc, mc = C.categorize(gh), C.categorize(mine)
            row = {"name": name, "gh": gh_url, "target": mine_url,
                   "gh_n": len(gh), "target_n": len(mine), "cats": {}}
            worst = []
            for cat in C.CATS:
                a, b = C.stats(gc[cat], 80), C.stats(mc[cat], 80)
                if not a["n"] and not b["n"]:
                    continue
                if not a["n"] or not b["n"]:
                    verdict, delta = "该侧无此类", None
                else:
                    delta = (b["w_med"] - a["w_med"]) / a["w_med"]
                    verdict = "一致" if abs(delta) <= 0.12 else ("偏窄" if delta < 0 else "偏宽")
                row["cats"][cat] = {"gh": a, "mine": b, "verdict": verdict,
                                    "delta": round(delta, 3) if delta is not None else None}
                if delta is not None:
                    worst.append((abs(delta), [cat, verdict, b["w_med"], a["w_med"]]))
            row["worst"] = [x[1] for x in sorted(worst, key=lambda z: z[0], reverse=True)[:3]]
            out["pairs"].append(row)
            print("    → " + ", ".join(f"{w[0]} {w[1]}（{w[3]}→{w[2]}）" for w in row["worst"]),
                  flush=True)

    if args.spacing:
        for label, url, sel in (
            ("gh-contributors", f"{GH}/graphs/contributors",
             "[class*=ChartCardContainer], .Box, .card"),
            ("wb-overview", "http://127.0.0.1:8765/#overview", ".card"),
            ("wb-metrics", "http://127.0.0.1:8765/#metrics", ".card"),
        ):
            print(f"[spacing] {label} …", flush=True)
            s.cmd("Page.navigate", {"url": url})
            time.sleep(6)
            v = s.eval(SPACING_JS.replace("SEL", json.dumps(sel)))
            out["spacing"][label] = v if isinstance(v, dict) else {"raw": str(v)[:80]}

    with open(os.path.join(BASE, "matrix.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    L = [f"# 页面对照矩阵（{args.width}px）\n",
         "映射：feedback/search/rules/laws/worklist/regstats ↔ `/pulls`；"
         "release ↔ `/commits/master`；overview/metrics ↔ `/graphs/contributors`\n",
         "| 工作台页 | GitHub | 元素数(GH/我) | 差异最大的三类 |",
         "|---|---|---:|---|"]
    for r in out["pairs"]:
        worst = "；".join(f"{w[0]} {w[1]}（GH {w[3]} → 我 {w[2]}）" for w in r["worst"]) or "—"
        L.append(f"| `{r['name']}` | `{r['gh'].split('/')[-1]}` | {r['gh_n']}/{r['target_n']} | {worst} |")

    if out["spacing"]:
        L += ["", "## 图表栅格间距\n", "| 页面 | 卡片数 | 垂直间距(中位) | 水平间距(中位) | 边框 | 圆角 | period |",
              "|---|---:|---:|---:|---|---|---|"]
        for k, v in out["spacing"].items():
            L.append(f"| {k} | {v.get('n')} | {med(v.get('vgaps') or [])} | {med(v.get('hgaps') or [])} | "
                     f"{v.get('border')} | {v.get('radius')} | {v.get('period')} |")

    md = os.path.join(BASE, "MATRIX.md")
    open(md, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("写入", md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
