# -*- coding: utf-8 -*-
"""tools/ui_css_audit.py — 「单一真源」审计：找出仍在自写视觉的 .lv-* 规则。

判据（视觉属性）：border* / background* / color / box-shadow / border-radius /
                   font* / fill / stroke
布局属性（允许自写）：display / grid-* / flex* / width / height / min-* / max-* /
                   margin / padding / gap / position / top / left / overflow

输出：visual-debt.json（按属性统计）+ 控制台前 15 条最"重"的规则。
用法：python -m tools.ui_css_audit
"""
from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")
OUT = os.path.join(ROOT, "reports", "github-baseline", "visual-debt.json")

VISUAL = re.compile(
    r"^(border(-[\w-]+)?|background(-[\w-]+)?|color|box-shadow|outline(-[\w-]+)?|"
    r"font(-[\w-]+)?|fill|stroke|text-decoration[\w-]*|border-radius)$")
LAYOUT_OK = re.compile(r"^(display|grid(-[\w-]+)?|flex(-[\w-]+)?|gap|row-gap|column-gap|"
                       r"width|height|min-[\w-]+|max-[\w-]+|margin(-[\w-]+)?|padding(-[\w-]+)?|"
                       r"position|top|left|right|bottom|overflow(-[\w-]+)?|z-index|order|"
                       r"align-[\w-]+|justify-[\w-]+|place-[\w-]+|inset(-[\w-]+)?|"
                       r"grid-template[\w-]*|grid-auto[\w-]*|grid-column[\w-]*|grid-row[\w-]*)$")


def main() -> int:
    t = open(HTML, encoding="utf-8").read()
    m = re.search(r"<style>(.*?)</style>", t, re.S)
    css = m.group(1) if m else ""
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    rules = []
    for mm in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        sel = mm.group(1).strip().replace("\n", " ")
        decls = [d.strip() for d in mm.group(2).split(";") if ":" in d]
        vis = []
        for d in decls:
            prop = d.split(":", 1)[0].strip().lower()
            if VISUAL.match(prop) and not LAYOUT_OK.match(prop):
                vis.append(prop)
        if vis and ".lv-" in sel:
            rules.append({"sel": sel[:110], "visual": sorted(set(vis)), "n": len(set(vis))})

    by_prop: dict[str, int] = {}
    for r in rules:
        for p in r["visual"]:
            by_prop[p] = by_prop.get(p, 0) + 1

    data = {"rules": len(rules), "by_prop": dict(sorted(by_prop.items(), key=lambda x: -x[1])),
            "top": sorted(rules, key=lambda r: -r["n"])[:40]}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    print(f"含视觉声明的 .lv-* 规则：{len(rules)} 条")
    print("按属性：", ", ".join(f"{k}×{v}" for k, v in list(data["by_prop"].items())[:10]))
    print("\n最重的 12 条（应迁到 Primer 或删掉）：")
    for r in data["top"][:12]:
        print(f"  [{r['n']:>2}] {r['sel'][:72]:<74} {','.join(r['visual'])[:50]}")
    print("\n写入", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
