# -*- coding: utf-8 -*-
"""check_llm_wiki_upstream.py — llm_wiki 上游版本监测（2026-09-12）

职责：查询 GitHub Releases（nashsu/llm_wiki）最新版本，与本地登记版本对比，
提示"是否有新版本 → 是否需按适配契约核对"。

设计（对应"上游更新是否会同步适配本项目"）：
  - 本项目与 llm_wiki 的耦合面**只有两处**：①`raw/sources/` 文件协议（md+frontmatter，
    由 tools/sync_wiki_sources.py 单向喂入）②可选本地 HTTP API（127.0.0.1:19828）；
  - 上游**小版本更新通常零适配**（文件协议稳定）；大版本变更时按
    `reports/llm_wiki接入适配契约_20260912.md` 的核对清单人工确认 3 个适配点；
  - 本工具提供"发现新版本"的信号（建议并入月度巡检，见运行手册）。

用法：
  python tools/check_llm_wiki_upstream.py            # 查询并对比（需网络）
  python tools/check_llm_wiki_upstream.py --record   # 查询后把当前最新版写入状态文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(_ROOT, "reports", "llm_wiki_upstream_state.json")
API = "https://api.github.com/repos/nashsu/llm_wiki/releases/latest"


def _fetch_latest() -> dict:
    req = urllib.request.Request(API, headers={
        "User-Agent": "regulatory-orchestrator/llm_wiki-upstream-check",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="llm_wiki 上游版本监测")
    ap.add_argument("--record", action="store_true", help="把当前最新版登记为本地基线")
    args = ap.parse_args()

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except ValueError:
            state = {}
    known = state.get("known_latest") or ""

    try:
        rel = _fetch_latest()
    except Exception as e:  # noqa: BLE001
        print(f"[upstream] 查询失败（网络）：{e!r}")
        return 2
    latest = (rel.get("tag_name") or "").lstrip("v")
    pub = rel.get("published_at") or ""
    wins = [a.get("name") for a in (rel.get("assets") or [])
            if a.get("name", "").lower().endswith((".exe", "-portable.zip", ".msi"))]
    print(f"[upstream] llm_wiki 最新: v{latest}（{pub}）")
    print(f"[upstream] Windows 资产: {wins or '（未列出）'}")
    if not known:
        print("[upstream] 本地无基线——可用 --record 登记当前版本为基线")
    elif latest == known:
        print(f"[upstream] 与本地基线一致（v{known}）——无需动作")
    else:
        print(f"[upstream] **发现新版本**：基线 v{known} → 最新 v{latest}")
        print("[upstream] 请按 reports/llm_wiki接入适配契约_20260912.md 的核对清单"
              "确认 3 个适配点（文件协议/HTTP API/安装资产名）")
    if args.record:
        state.update({"known_latest": latest, "published_at": pub,
                      "windows_assets": wins,
                      "checked_at": __import__("datetime").datetime.now()
                      .strftime("%Y-%m-%d %H:%M:%S")})
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
        print(f"[upstream] 基线已登记 → {STATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
