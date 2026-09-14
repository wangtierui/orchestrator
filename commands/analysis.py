# -*- coding: utf-8 -*-
"""commands.analysis — orchestrator 命令：analysis（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import paths


def run(argv):
    """analysis gen|status —— 规划 2.1 五级分析交付库（F-L01，2026-09-12）。

    - gen    [--out <dir>] [--dry]：生成 **17 份**交付物（docs/reports/ + _manifest.json）
                                   （15 项 + 2026-09-14 追加的关系类 2 项：2.1.2.4/2.1.2.5）
    - status                  ：列交付库现状（manifest 概览 + 文件缺失检查）
    """
    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415
    action = argv[0] if argv else ""
    outdir = _os.path.join(paths.ROOT, "docs", "reports")
    if action == "gen":
        _sys.path.insert(0, _os.path.join(paths.ROOT, "tools"))
        from gen_analysis_deliveries import main as _gen  # noqa: PLC0415
        return _gen(argv[1:])
    if action == "status":
        mpath = _os.path.join(outdir, "_manifest.json")
        if not _os.path.exists(mpath):
            print("[analysis] 交付库未生成（先跑: orchestrator analysis gen）")
            return 1
        m = _json.load(open(mpath, encoding="utf-8"))
        miss = [it for it in m.get("items", [])
                if not _os.path.exists(_os.path.join(outdir, it["file"]))]
        print(f"[analysis] 交付库 {m.get('count')} 项 | 生成于 {m.get('generated_at')} | 目录 {outdir}")
        for it in m.get("items", []):
            print(f"  {it['item']:9s} {it['lines']:5d} 行  {it['file']}")
        if miss:
            print(f"[analysis] ⚠ 缺失 {len(miss)} 个文件: {[x['file'] for x in miss]}")
            return 1
        print("[analysis] 全部交付物在位")
        return 0
    print("用法: orchestrator analysis {gen [--out dir] [--dry] | status}")
    return 1
