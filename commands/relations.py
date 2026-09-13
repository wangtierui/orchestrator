# -*- coding: utf-8 -*-
"""commands.relations — orchestrator 命令：relations（依据/废止关系，R-F01）

子命令
------
  gen     全量抽取并落盘三类关系产物（透传 tools/extract_relations.py 参数）
  status  查看产物生成元信息、三类关系计数与两级解析率
  show    按实体（RFN/IPN）查询其作为源/目标的关系

设计纪律：本文件只做**编排与展示**；抽取在 `std_lib/common_lib/relations.py`（唯一实现），
产物读写统一经 `interfaces/relations_api.py`（唯一读取入口）。
"""
from __future__ import annotations

import json
import os
import sys

import paths


def _api():
    sys.path.insert(0, paths.ROOT)
    from interfaces import relations_api  # noqa: PLC0415
    return relations_api


def _gen(argv) -> int:
    """全量抽取（透传到 tools/extract_relations.py，保持单一实现）。"""
    sys.path.insert(0, os.path.join(paths.ROOT, "tools"))
    import argparse  # noqa: PLC0415

    from extract_relations import run  # noqa: PLC0415

    ap = argparse.ArgumentParser(prog="orchestrator relations gen")
    ap.add_argument("--source", action="append", default=None)
    ap.add_argument("--no-internal", action="store_true")
    ap.add_argument("--with-attachments", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    run(sources=a.source, limit=a.limit, dry_run=a.dry_run,
        with_internal=not a.no_internal, with_attachments=a.with_attachments, report=a.report)
    return 0


def _status(argv) -> int:
    api = _api()
    s = api.stat()
    if not s:
        print(f"[relations] 产物缺失：{api.INDEX_PATH}")
        print("[relations] 先运行 `python cli.py relations gen` 生成。")
        return 1
    print(json.dumps(api.summary(), ensure_ascii=False, indent=2))
    cross = len(api.load_cross_basis())
    print(f"  类别 3 纯依据边（cross_basis.jsonl）：{cross} 条")
    if argv and argv[0] == "--samples":
        for r in api.load("cross")[:8]:
            print(f"   {r['src_ref']} → {r['dst_ref'] or r['dst_key'] or '(未解析)'} "
                  f"| {r['dst_name'][:44]} | {r['matched_by']}")
    return 0


def _show(argv) -> int:
    if not argv:
        print("用法: orchestrator relations show <RFN-xxx|IPN-xxx>")
        return 1
    api = _api()
    ref = argv[0]
    as_src, as_dst = api.by_src(ref), api.by_dst(ref)
    print(f"[relations] {ref}：作为源 {len(as_src)} 条 / 作为目标 {len(as_dst)} 条")
    for r in as_src[:20]:
        print(f"  [出/{r['relation']:6s}] → {r['dst_ref'] or r['dst_key'] or '(未解析)':<22s}"
              f" {r['dst_name'][:40]} ({r['matched_by']})")
    for r in as_dst[:20]:
        print(f"  [入/{r['relation']:6s}] ← {r['src_ref'] or r['src_name'][:20]:<22s}"
              f" {r['src_name'][:40]} ({r['matched_by']})")
    return 0


def run(argv) -> int:
    """relations {gen|status|show} — 依据/废止关系（R-F01）。"""
    if not argv:
        print("用法: orchestrator relations {gen|status|show}")
        return 1
    sub, rest = argv[0], argv[1:]
    if sub == "gen":
        return _gen(rest)
    if sub == "status":
        return _status(rest)
    if sub == "show":
        return _show(rest)
    print(f"未知子命令: {sub}（可用: gen/status/show）")
    return 1
