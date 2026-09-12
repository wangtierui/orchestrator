# -*- coding: utf-8 -*-
"""commands.internal — orchestrator 命令：internal（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import sys

import paths


def run(argv):
    """internal index|align [--source-dir ...] — 内部制度摄取/对齐（P6）。"""
    import os
    sys.path.insert(0, os.path.join(paths.ROOT, "modules"))
    if not argv:
        print("用法: orchestrator internal {index|align} [--source-dir DIR] [--dry-run]")
        return 1
    sub = argv[0]
    if sub == "index":
        # 透传剩余参数（--source-dir/--enable-ocr/--dry-run）
        return _index_main_internal(argv[1:])
    if sub == "reocr":
        # OCR 存量回填（2026-09-12）：对 text_chars==0 的扫描件重提取（质量闸门把关）
        import argparse as _ap  # noqa: PLC0415
        ap = _ap.ArgumentParser(prog="orchestrator internal reocr")
        ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个（0=全部）")
        ap.add_argument("--min-cjk", type=int, default=20, dest="min_cjk",
                        help="质量闸门：识别文本最少汉字数（默认 20）")
        ap.add_argument("--force", action="store_true",
                        help="扫描件全量重跑（引擎升级提质重建；fitz 首页<30字判定）")
        a = ap.parse_args(argv[1:])
        from internal_policy_base.extract import reocr_backfill  # noqa: PLC0415
        st = reocr_backfill(limit=(a.limit or None), min_cjk=a.min_cjk, force=a.force)
        import json as _json  # noqa: PLC0415
        print(_json.dumps({k: v for k, v in st.items() if k != "details"},
                          ensure_ascii=False, indent=2))
        for d in st.get("details", []):
            print("  ", _json.dumps(d, ensure_ascii=False))
        return 0
    if sub == "refine-identity":
        # 存量制度身份纠正（2026-09-12）：文号/标题以正文为准（内容权威）
        import argparse as _ap  # noqa: PLC0415
        ap = _ap.ArgumentParser(prog="orchestrator internal refine-identity")
        ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个（0=全部）")
        a = ap.parse_args(argv[1:])
        from internal_policy_base.indexer import refine_identity_backfill  # noqa: PLC0415
        st = refine_identity_backfill(limit=(a.limit or None))
        import json as _json  # noqa: PLC0415
        print(_json.dumps({k: v for k, v in st.items() if k != "details"},
                          ensure_ascii=False, indent=2))
        for d in st.get("details", [])[:15]:
            print("  ", _json.dumps(d, ensure_ascii=False))
        return 0
    if sub == "align":
        import json

        from internal_policy_base.align import align_all
        s = align_all()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "merged":
        import json

        from internal_policy_base.merged import build_merged_view
        s = build_merged_view()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "backfill":
        # R10/B4（2026-09-08）：backfill_clauses 收敛 CLI（原仅 python -c 手工调用）
        import json

        from internal_policy_base.extract import backfill_clauses
        s = backfill_clauses()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"未知 internal 子命令: {sub}（可用: index, align, merged, backfill）")
    return 1


def _index_main_internal(argv):
    """复刻 indexer.main 的 argparse（cli 内联透传）。"""
    import argparse
    ap = argparse.ArgumentParser(description="internal index")
    ap.add_argument("--source-dir", default="", help="制度源目录")
    ap.add_argument("--enable-ocr", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    src = args.source_dir or __import__("os").environ.get("INTERNAL_POLICY_ROOT", "")
    if not src:
        print("需提供 --source-dir 或设置 INTERNAL_POLICY_ROOT 环境变量")
        return 1
    import json

    from internal_policy_base.indexer import ingest
    s = ingest(src, enable_ocr=args.enable_ocr, dry_run=args.dry_run)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0
