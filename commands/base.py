# -*- coding: utf-8 -*-
"""commands.base — orchestrator 命令：base（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations


def run(argv):
    """base publish|query|search —— 双底座发布件构建与统一查询（Base Contract v1，F-K03/O09）。

    - publish [--base external|internal|all]：构建发布件（JSONL）+ SQLite/FTS5 索引
    - query   [--rfn/--docno/--theme/--source] [--internal] [--json]：精确查询
    - search  <text> [--kind records|clauses|policies] [--internal] [--limit N] [--json]
    """
    import argparse as _ap  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    # P0-2（v2 §3.1.2）：sys.path 引导统一走 bootstrap（原 3 处自注入收口）
    from bootstrap import bootstrap  # noqa: PLC0415
    bootstrap("all")
    ap = _ap.ArgumentParser(prog="orchestrator base")
    sub = ap.add_subparsers(dest="action", required=True)
    pp = sub.add_parser("publish", help="构建发布件 + 索引")
    pp.add_argument("--base", default="all", choices=["external", "internal", "all"])
    pq = sub.add_parser("query", help="精确查询发布件")
    pq.add_argument("--rfn", default="")
    pq.add_argument("--docno", default="")
    pq.add_argument("--theme", default="")
    pq.add_argument("--source", default="")
    pq.add_argument("--timeliness", default="")
    pq.add_argument("--chain", default="", help="同文号版本链（F-K08）")
    pq.add_argument("--view", default="", choices=["", "active"], help="视图：active=现行有效")
    pq.add_argument("--order", default="", choices=["", "date"], help="排序：date=按发布日期升序（时间线 F-L06）")
    pq.add_argument("--internal", action="store_true", help="查内部底座（默认外部）")
    pq.add_argument("--limit", type=int, default=50)
    pq.add_argument("--json", action="store_true")
    ps = sub.add_parser("search", help="全文检索（FTS5 trigram）")
    ps.add_argument("text")
    ps.add_argument("--kind", default="records", choices=["records", "clauses", "policies"])
    ps.add_argument("--internal", action="store_true")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.action == "publish":
        from base_publish import build_external as _be  # noqa: PLC0415
        from base_publish import build_fts as _bf  # noqa: PLC0415
        from base_publish import build_internal as _bi  # noqa: PLC0415
        out = {}
        if args.base in ("external", "all"):
            out["external"] = _be.build()["counts"]
        if args.base in ("internal", "all"):
            out["internal"] = _bi.build()["counts"]
        out["fts"] = {}
        if args.base in ("external", "all"):
            out["fts"]["external"] = _bf.build_external()
        if args.base in ("internal", "all"):
            out["fts"]["internal"] = _bf.build_internal()
        print(_json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    from base_api import (  # noqa: PLC0415
        query_external,
        query_internal,
        search_external,
        search_internal,
    )
    if args.action == "query":
        if args.chain:
            from base_api import version_chain  # noqa: PLC0415
            rows = version_chain(args.chain, limit=args.limit)
        elif args.view == "active":
            from base_api import view_active  # noqa: PLC0415
            rows = view_active(limit=args.limit)
        elif args.internal:
            rows = query_internal(theme=args.theme, limit=args.limit)
        else:
            rows = query_external(rfn=args.rfn, document_number=args.docno, theme=args.theme,
                                  source=args.source, timeliness_status=args.timeliness,
                                  limit=args.limit)
        if args.order == "date":
            # F-L06：时间线视图（按发布日期升序；空日期沉底）
            rows = sorted(rows, key=lambda r: (r.get("publish_date") or "9999"))
    else:
        if args.internal:
            rows = search_internal(args.text, limit=args.limit, kind=args.kind if args.kind != "records" else "policies")
        else:
            rows = search_external(args.text, limit=args.limit, kind=args.kind)
    if args.json:
        print(_json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"[base] 命中 {len(rows)} 条")
        for r in rows[:20]:
            key = r.get("rfn") or r.get("ipn") or r.get("record_id", "")
            print(f"  {key:<22} | {(r.get('title') or '')[:44]} | {(r.get('article_no') or r.get('publish_date') or '')}")
    return 0
