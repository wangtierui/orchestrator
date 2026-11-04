# -*- coding: utf-8 -*-
"""tools/gh_ui_visual.py — 视觉回归：同视口截图 GitHub 与工作台，算像素差。

为什么需要它
------------
此前的对比器只比**几何**（元素数量、中位宽高）。它看不见颜色、边框、阴影、层次，
所以"数值一致"不等于"看起来一致"——三层蓝框、底色差一阶、分隔线多一根，全靠肉眼发现。

这个工具给出唯一客观指标：**同宽度下两张截图的像素差异率**，并输出差异带
（每 80px 一条横带的变化比例），用来定位"哪一段最不像"。

用法：
  python -m tools.gh_ui_visual --gh issues --target "http://127.0.0.1:8765/#worklist" --slug issues-vs-worklist
  python -m tools.gh_ui_visual --width 1440 --height 900
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "reports", "github-baseline")
OUT = os.path.join(BASE, "visual")

GH = "https://github.com/notepad-plus-plus/notepad-plus-plus"
PAGES = {
    "issues": f"{GH}/issues",
    "pulls": f"{GH}/pulls",
    "commits": f"{GH}/commits/master",
    "repo": GH,
}


def _shot(s, path: str) -> str:
    r = s.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    data = base64.b64decode(r["data"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _diff(a: str, b: str, out: str, band: int = 80) -> dict:
    from PIL import Image, ImageChops

    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    w = min(ia.width, ib.width)
    h = min(ia.height, ib.height)
    ia = ia.crop((0, 0, w, h))
    ib = ib.crop((0, 0, w, h))

    d = ImageChops.difference(ia, ib).convert("L")
    # 阈值 24：忽略抗锯齿/字体渲染的微差
    mask = d.point(lambda v: 255 if v > 24 else 0)
    hist = mask.histogram()
    changed = sum(hist[1:])
    total = w * h

    bands = []
    for top in range(0, h, band):
        box = (0, top, w, min(h, top + band))
        sub = mask.crop(box)
        c = sum(sub.histogram()[1:])
        bands.append({"top": top, "ratio": round(c / (w * (box[3] - box[1])), 4)})

    mask.save(out)
    return {"w": w, "h": h, "changed": changed, "total": total,
            "ratio": round(changed / total, 4),
            "bands": sorted(bands, key=lambda b: -b["ratio"])[:8]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_visual")
    ap.add_argument("--gh", default="issues", help="issues/pulls/commits/repo 或完整 URL")
    ap.add_argument("--target", default="http://127.0.0.1:8765/#worklist")
    ap.add_argument("--slug", default="")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args(argv)

    from tools.cdp import Session

    gh_url = PAGES.get(args.gh, args.gh)
    slug = args.slug or f"{args.gh}-vs-target"
    os.makedirs(OUT, exist_ok=True)

    s = Session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False})

    print(f"[visual] GitHub {gh_url} @{args.width}", flush=True)
    s.cmd("Page.navigate", {"url": gh_url})
    time.sleep(7)
    p_gh = _shot(s, os.path.join(OUT, f"{slug}-gh.png"))

    print(f"[visual] 工作台 {args.target} @{args.width}", flush=True)
    s.cmd("Page.navigate", {"url": args.target})
    time.sleep(7)
    p_wb = _shot(s, os.path.join(OUT, f"{slug}-wb.png"))

    res = _diff(p_gh, p_wb, os.path.join(OUT, f"{slug}-diff.png"))
    res.update({"gh": gh_url, "target": args.target, "width": args.width,
                "height": args.height, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})

    path = os.path.join(OUT, f"{slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)

    print(f"像素差异率: {res['ratio'] * 100:.2f}%  ({res['changed']:,}/{res['total']:,})")
    print("差异最大的横带（top, 比例）:")
    for b in res["bands"][:5]:
        print(f"   y={b['top']:>4}  {b['ratio'] * 100:.1f}%")
    print("产物:", p_gh, p_wb, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
