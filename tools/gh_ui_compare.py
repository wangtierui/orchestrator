# -*- coding: utf-8 -*-
"""tools/gh_ui_compare.py — 响应式/全元素对比器（GitHub 基线 vs 工作台）。

为什么重写
----------
第一版只比 5 个"我手工映射"的控件（搜索框/密度/筛选/页签/顶栏），
用户指出"未覆盖全元素"——手工映射必然漏，而且漏的正是没对上的那些。

这一版改成 **全元素 + 分类聚合**：
  · 两边都用同一段 JS 抓**所有可见交互元素**（a/button/input/select/textarea/
    summary/[role]/[tabindex]），记录 tag/role/type/aria/text/尺寸/字号/是否可见；
  · 按语义分成 8 类（搜索框、文本输入、按钮、图标按钮、链接、页签、复选、下拉）；
  · 每类比 **数量 / 中位宽高 / 字号 / 过窄(<24px)数量**，而不是逐个找对应；
  · 另做一层"文本可配对"的逐元素比较（同名文本的元素比尺寸），作为补充。

两种模式
  --mode full   全元素分类聚合（默认）
  --mode pair   旧的 5 控件精确配对（保留做回归）

用法：
  python -m tools.gh_ui_compare --gh "https://github.com/.../issues" --target "http://127.0.0.1:8765/#worklist" --slug issues-vs-worklist
  python -m tools.gh_ui_compare --mode pair
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")

#: 抓"所有可见交互元素"
EXTRACT = r"""(() => {
  const sel = 'a,button,input,select,textarea,summary,[role],[tabindex]';
  const out = [];
  const seen = new Set();
  for (const e of document.querySelectorAll(sel)) {
    if (seen.has(e)) continue;
    seen.add(e);
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    if (r.width === 0 && r.height === 0) continue;
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const text = (e.textContent || '').replace(/\s+/g, ' ').trim();
    out.push({
      tag: e.tagName,
      role: e.getAttribute('role') || '',
      type: (e.getAttribute('type') || '').toLowerCase(),
      aria: (e.getAttribute('aria-label') || '').slice(0, 40),
      ph: (e.getAttribute('placeholder') || '').slice(0, 40),
      cls: String(e.className || '').slice(0, 46),
      text: text.slice(0, 30),
      hasIcon: !!e.querySelector('svg, img'),
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.left), y: Math.round(r.top),
      fs: Math.round(parseFloat(cs.fontSize) * 10) / 10,
      pad: cs.padding,
    });
    if (out.length >= 1600) break;
  }
  return out;
})()"""

CATS = {
    "搜索框": lambda e: e["tag"] == "INPUT" and (e["type"] == "search"
                                          or "search" in (e["aria"] + e["ph"]).lower()
                                          or "搜索" in (e["aria"] + e["ph"])),
    "文本输入": lambda e: e["tag"] in ("INPUT", "TEXTAREA") and e["type"] not in ("search", "checkbox", "radio"),
    "按钮": lambda e: e["tag"] == "BUTTON" and bool(e["text"]),
    "图标按钮": lambda e: e["tag"] == "BUTTON" and not e["text"],
    "链接": lambda e: e["tag"] == "A",
    "页签": lambda e: e["role"] == "tab",
    "复选": lambda e: e["type"] in ("checkbox", "radio")
                       or e["role"] == "checkbox" or "Checkbox" in e["cls"],
    "下拉": lambda e: e["tag"] == "SELECT",
}


def categorize(els: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in CATS}
    for e in els:
        for name, rule in CATS.items():
            try:
                if rule(e):
                    out[name].append(e)
                    break
            except Exception:  # noqa: BLE001
                continue
    return out


def stats(rows: list[dict], sample: int = 0) -> dict:
    if sample:
        rows = rows[:sample]
    if not rows:
        return {"n": 0}
    ws = [r["w"] for r in rows]
    hs = [r["h"] for r in rows]
    fs = [r["fs"] for r in rows if r["fs"]]
    return {"n": len(rows), "w_med": round(st.median(ws)), "h_med": round(st.median(hs)),
            "w_max": max(ws), "fs_med": round(st.median(fs), 1) if fs else None,
            "narrow": sum(1 for w in ws if w < 24)}


def extract(s) -> list[dict]:
    v = s.eval(EXTRACT)
    return v if isinstance(v, list) else json.loads(v or "[]")


def measure_page(s, url: str, width: int, wait_sel: str) -> list[dict]:
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    s.cmd("Page.navigate", {"url": url})
    for _ in range(14):
        time.sleep(1.0)
        if s.eval(f"!!document.querySelector({json.dumps(wait_sel)})"):
            break
    time.sleep(0.8)
    return extract(s)


def md_table(rows: list[list[str]], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def run_full(args) -> int:
    widths = [int(x) for x in args.widths.split(",") if x.strip()]
    sys.path.insert(0, os.path.join(ROOT, "reports", "_tmp"))
    from cdp import Session

    s = Session()
    data: dict = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "gh": args.gh, "target": args.target, "widths": {}}

    for w in widths:
        print(f"[full] {w} …", flush=True)
        gh = measure_page(s, args.gh, w, args.gh_wait)
        mine = measure_page(s, args.target, w, args.target_wait)
        data["widths"][str(w)] = {"gh": categorize(gh), "target": categorize(mine),
                                  "gh_all": len(gh), "target_all": len(mine)}

    path = os.path.join(BASE, f"full-{args.slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    L = [f"# 全元素对比：GitHub vs 工作台（{args.slug}）\n",
         f"- GitHub：`{args.gh}`",
         f"- 工作台：`{args.target}`",
         f"- 宽度：{widths}",
         "- 口径：所有可见交互元素，按语义分 8 类；每类比 数量/中位宽/中位高/字号/过窄数",
         "- `N/A` = 该侧该类为 0（**功能缺口**，不是尺寸差）\n"]

    for w in widths:
        d = data["widths"][str(w)]
        L.append(f"## {w}px（GitHub {d['gh_all']} 个 / 工作台 {d['target_all']} 个）\n")
        rows = []
        for cat in CATS:
            a, b = stats(d["gh"][cat], args.sample), stats(d["target"][cat], args.sample)
            if a["n"] == 0 and b["n"] == 0:
                continue
            fmt = lambda x, k: ("N/A" if x["n"] == 0 else str(x.get(k)))  # noqa: E731
            if a["n"] == 0 and b["n"] > 0:
                verdict = "**GH 无此类**"
            elif b["n"] == 0:
                verdict = "**缺口**"
            else:
                delta = (b["w_med"] - a["w_med"]) / a["w_med"]
                verdict = "一致" if abs(delta) <= 0.12 else ("偏窄" if delta < 0 else "偏宽")
            rows.append([cat, fmt(a, "n"), fmt(b, "n"),
                         f"{fmt(a, 'w_med')} / {fmt(b, 'w_med')}",
                         f"{fmt(a, 'h_med')} / {fmt(b, 'h_med')}",
                         f"{fmt(a, 'fs_med')} / {fmt(b, 'fs_med')}", verdict])
        L += md_table(rows, ["类别", "GH 数量", "我方数量", "中位宽", "中位高", "字号", "判定"])
        L.append("")

    # ---- 文本可配对的逐元素差异（只列差异显著的） ----
    w0 = str(widths[0])
    gh_all = []
    mine_all = []
    for cat, rows in data["widths"][w0]["gh"].items():
        gh_all += rows
    for cat, rows in data["widths"][w0]["target"].items():
        mine_all += rows
    gh_by_text = {}
    for e in gh_all:
        if e["text"]:
            gh_by_text.setdefault(e["text"], e)
    diffs = []
    for e in mine_all:
        g = gh_by_text.get(e["text"])
        if not g:
            continue
        dw = e["w"] - g["w"]
        dh = e["h"] - g["h"]
        if abs(dw) >= 12 or abs(dh) >= 6:
            diffs.append([e["text"][:16], f"{g['w']}×{g['h']}", f"{e['w']}×{e['h']}",
                          f"{dw:+d}", f"{dh:+d}"])
    L.append("## 同名元素的尺寸差（差 ≥12px 宽 或 ≥6px 高）\n")
    L += md_table(diffs[:25] or [["—", "无同名元素可比", "", "", ""]],
                  ["元素文本", "GitHub", "工作台", "Δ宽", "Δ高"])

    md = os.path.join(BASE, f"FULL-{args.slug}.md")
    open(md, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print("\n".join(L)[:2000])
    print("写入", path, "|", md)
    return 0


def run_pair(args) -> int:
    """旧模式：5 个精确映射控件。"""
    widths = [int(x) for x in args.widths.split(",") if x.strip()]
    base = json.load(open(os.path.join(BASE, "responsive.json"), encoding="utf-8"))
    sys.path.insert(0, os.path.join(ROOT, "reports", "_tmp"))
    from cdp import Session

    s = Session()
    print("[pair] widths:", widths)
    rows = {}
    for w in widths:
        s.cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        s.cmd("Page.navigate", {"url": args.target})
        time.sleep(3.0)
        rows[str(w)] = s.eval("(() => { const e = document.querySelector('#lvfq');"
                              " if (!e) return null; const r = e.getBoundingClientRect();"
                              " return {w: Math.round(r.width), h: Math.round(r.height)}; })()")
    print(json.dumps({"base": list(base.get("widths", {})), "mine": rows}, ensure_ascii=False)[:400])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_compare")
    ap.add_argument("--mode", choices=("full", "pair"), default="full")
    ap.add_argument("--gh", default="https://github.com/notepad-plus-plus/notepad-plus-plus/issues")
    ap.add_argument("--gh-wait", default="#issues-index-filter-input")
    ap.add_argument("--target", default="http://127.0.0.1:8765/#worklist")
    ap.add_argument("--target-wait", default=".lv-gtable, #view .card")
    ap.add_argument("--slug", default="issues-vs-worklist")
    ap.add_argument("--widths", default="1440,1012,768,544")
    ap.add_argument("--sample", type=int, default=80,
                    help="每类只取文档顺序前 N 个（两边都是），避免行数差异压倒结论")
    args = ap.parse_args(argv)
    return run_full(args) if args.mode == "full" else run_pair(args)


if __name__ == "__main__":
    raise SystemExit(main())
