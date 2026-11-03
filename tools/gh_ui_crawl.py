# -*- coding: utf-8 -*-
"""tools/gh_ui_crawl.py — 从仓库首页出发，把"点得进去的页面"逐个采集。

为什么需要它
------------
前几批按**人工给的 URL 清单**采（17 个列表页 + 详情页），必然漏掉"从首页能不能点到"
这一维度：`/tree/master/PowerEditor`、`/commits/master`、`/branches`、`/tags` …
这些页面没人报过名字，但都是首页上的入口。这里改成**以入口为种子**的 BFS。

口径（不追求把 2.9k issue 全爬下来）
  · 同源 + 同仓库前缀；
  · 跳过：`/issues/<n>`、`/pull/<n>`、`/blob/…`、`/raw/…`、`/archive/…`、
          `/commits/<sha>`、`/wiki/<page>`、`/settings/…`（这些是"数据页"，不是"界面页"）；
  · 去重按 path（忽略 ?query 与尾斜杠）；
  · 默认 depth=1、cap=30，避免无限扩张。

用法：
  python -m tools.gh_ui_crawl                    # depth 1, cap 30
  python -m tools.gh_ui_crawl --depth 2 --cap 60
环境变量：CDP_PORT、CDP_NO_SPAWN=1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "reports", "github-baseline")
REPO = "https://github.com/notepad-plus-plus/notepad-plus-plus"
HOST = "github.com"
PREFIX = "/notepad-plus-plus/notepad-plus-plus"

SKIP = re.compile(
    r"/(issues/\d+|pull/\d+|blob/|raw/|archive/|commits/[0-9a-f]{7,}|wiki/[^/]+|"
    r"settings/|compare/|releases/tag/|tree/[^/]+/[^/]+/.+)")
KEEP_HINT = re.compile(
    r"^/notepad-plus-plus/notepad-plus-plus(/tree/[^/]+(/[^/]+)?|/commits/[^/]+|/branches|"
    r"/tags|/releases|/contributors|/labels|/milestones|/discussions|/community|"
    r"/activity|/pulse|/graphs/[^/]+|/network[^/]*|/forks|/actions[^/]*|/security[^/]*|"
    r"/projects|/agents|/custom-properties|/issues|/pulls|/wiki)?$")


def norm(url: str) -> str:
    p = urlparse(url)
    if p.netloc and p.netloc != HOST:
        return ""
    path = p.path.rstrip("/") or "/"
    return path


def slug_for(path: str) -> str:
    if path == PREFIX:
        return "crawl-repo"
    tail = path[len(PREFIX):].strip("/")
    return "crawl-" + re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_crawl")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--cap", type=int, default=30)
    ap.add_argument("--seed", default=REPO)
    args = ap.parse_args(argv)

    from tools import gh_ui_harvest as H

    s = H._session()
    s.cmd("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(args.seed, 0)]
    rows: list[dict] = []
    while queue and len(rows) < args.cap:
        url, depth = queue.pop(0)
        path = norm(url)
        if not path or path in seen or not path.startswith(PREFIX):
            continue
        seen.add(path)
        full = f"https://{HOST}{path}"
        slug = slug_for(path)
        print(f"[crawl d{depth}] {path[:60]}", flush=True)
        try:
            d = H.harvest_page(s, slug, full, save_html=True)
            rows.append({"slug": slug, "path": path, "url": full, "depth": depth,
                         "title": d.get("title", ""),
                         "interactive": d.get("counts", {}).get("interactive", 0)})
            print(f"    → {str(d.get('title',''))[:44]} | 交互 {d.get('counts',{}).get('interactive',0)}",
                  flush=True)
            links = d.get("same_repo_links") or []
        except Exception as e:  # noqa: BLE001
            rows.append({"slug": slug, "path": path, "depth": depth, "error": f"{type(e).__name__}: {e}"})
            print(f"    ✗ {e}", flush=True)
            links = []

        if depth < args.depth:
            for link in links:
                p = norm(link if link.startswith("http") else f"https://{HOST}{link}")
                if not p or p in seen:
                    continue
                if SKIP.search(p) or not KEEP_HINT.match(p):
                    continue
                queue.append((f"https://{HOST}{p}", depth + 1))

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "crawl-index.json"), "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "seed": args.seed, "depth": args.depth, "cap": args.cap,
                   "pages": rows}, fh, ensure_ascii=False, indent=1)
    print(f"[crawl] 完成 {len(rows)} 页 → crawl-index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
