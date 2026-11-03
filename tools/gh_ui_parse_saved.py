# -*- coding: utf-8 -*-
"""tools/gh_ui_parse_saved.py — 解析"手动另存"的 GitHub 页面，产出与在线采集同构的清单。

为什么需要这条路（不是偷懒）：
  在线采集依赖 CDP + 已经登录的浏览器实例；当登录态无法在本机建立时
  （企业 SSO / 二次验证 / 登录在另一台机器），唯一不碰凭据又能拿到
  "登录后界面"的办法，就是让用户在**自己的浏览器**里 Ctrl+S 存一份 DOM，
  再由本工具离线解析成同一套字段（元素/语义/结构），供工作台复用。

与在线版的差别（会写进产物 `note`，不假装等价）：
  · 没有计算样式（颜色/间距/圆角）——那需要渲染引擎；
  · 只反映保存那一刻的静态 DOM，弹层/展开态若未在保存前打开则不存在。

用法：
  python -m tools.gh_ui_parse_saved                 # 解析 reports/github-baseline/manual/*.html
  python -m tools.gh_ui_parse_saved --dir some/dir
"""
from __future__ import annotations

import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "reports", "github-baseline", "manual")

#: 与在线采集保持一致的交互元素面
INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "summary"}


def _text(el, limit: int = 90) -> str:
    return " ".join((el.get_text() or "").split())[:limit]


def parse_one(path: str) -> dict:
    from bs4 import BeautifulSoup

    with open(path, encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    soup = BeautifulSoup(html, "lxml")

    items = []
    for el in soup.find_all(True):
        tag = el.name.lower()
        role = el.get("role")
        if tag not in INTERACTIVE_TAGS and not role and not el.has_attr("tabindex"):
            continue
        items.append({
            "tag": tag.upper(),
            "role": role or "",
            "text": _text(el),
            "aria": el.get("aria-label") or "",
            "title": el.get("title") or "",
            "pressed": el.get("aria-pressed") or "",
            "expanded": el.get("aria-expanded") or "",
            "checked": el.get("aria-checked") or "",
            "testid": el.get("data-testid") or "",
            "type": el.get("type") or "",
            "href": (el.get("href") or "")[:200],
            "cls": " ".join(el.get("class") or [])[:70],
            "disabled": el.has_attr("disabled"),
        })
        if len(items) >= 1400:
            break

    heads = [[h.name.upper(), _text(h, 80)] for h in soup.find_all(["h1", "h2", "h3", "h4"])][:60]
    tables = []
    for t in soup.find_all("table")[:6]:
        ths = [_text(x, 24) for x in t.find_all("th")]
        rows = []
        for tr in t.find_all("tr")[1:4]:
            rows.append([_text(td, 30) for td in tr.find_all(["td", "th"])])
        tables.append({"headers": ths, "rows": rows})

    marks = []
    for tag in ("header", "nav", "main", "aside", "footer"):
        e = soup.find(tag)
        if e:
            marks.append([tag, (e.get("class") or [""])[0][:40]])

    return {
        "source_file": os.path.basename(path),
        "title": _text(soup.title) if soup.title else "",
        "user_login": (soup.find("meta", attrs={"name": "user-login"}) or {}).get("content", ""),
        "counts": {"els": len(soup.find_all(True)), "interactive": len(items)},
        "landmarks": marks,
        "headings": heads,
        "interactive": items,
        "tables": tables,
        "note": "离线解析：仅静态 DOM，无计算样式；保存前未打开的弹层不在此清单内。",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gh_ui_parse_saved", description="解析手动保存的 GitHub 页面")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.dir, "*.htm*")))
    if not files:
        print(f"[parse] 没有可解析的 HTML：{args.dir}")
        print("[parse] 用法：在自己已登录的浏览器里 Ctrl+S → 选「网页，完整」→ 存到该目录")
        return 1

    done = []
    for f in files:
        try:
            d = parse_one(f)
            out = os.path.join(args.dir, os.path.splitext(os.path.basename(f))[0] + ".json")
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
            done.append((os.path.basename(f), d["counts"]["interactive"], d["title"][:40]))
            print(f"[parse] {os.path.basename(f)} → 交互 {d['counts']['interactive']}")
        except Exception as e:  # noqa: BLE001 单文件失败不阻断
            print(f"[parse] ✗ {os.path.basename(f)}: {type(e).__name__}: {e}")
    print(f"[parse] 完成 {len(done)}/{len(files)}，输出在同目录 *.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
