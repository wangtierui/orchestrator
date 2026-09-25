# -*- coding: utf-8 -*-
"""commands.internal — orchestrator 命令：internal（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import os

import paths

# 数据前置（2026-09-13 · CP-E04）：制度底座相对仓库根的路径。
# 缺数据时给出可执行指引，替代原先的裸 traceback——克隆副本上 `internal merged`
# 曾直接抛 FileNotFoundError 栈（对比 `draft` 有友好提示，错误面不一致）。
_IPB_INDEX = os.path.join("modules", "internal_policy_base", "data", "internal_policy_index.json")
_INGEST_HINT = "先运行 `python cli.py internal index --source-dir <制度目录>` 摄取制度"


def _require_ipb_index(sub: str) -> bool:
    """校验制度底座是否就绪；缺失则打印可执行指引并返回 False。"""
    p = os.path.join(paths.ROOT, _IPB_INDEX)
    if os.path.exists(p):
        return True
    print(f"[internal {sub}] 缺少制度底座文件：{p}")
    print(f"[internal {sub}] {_INGEST_HINT}"
          "（数据不入 git，异机需先按 data_migration_manifest.json 恢复或重建）。")
    return False


def run(argv):
    """internal index|align|merged|backfill|reocr|refine-identity — 内部制度摄取/对齐/词表（P6）。"""
    # P0-2：引导统一走 bootstrap；保留 `modules/` 以便 `from internal_policy_base.x import y`
    from bootstrap import bootstrap  # noqa: PLC0415
    bootstrap("all", extra=("modules",))
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
                        help="文本层质量不达标者重跑（有效汉字/缺字判据；幂等，一轮收敛）")
        ap.add_argument("--retry", action="store_true",
                        help="连同已提质尝试过者一并重跑（OCR 引擎升级后使用）")
        a = ap.parse_args(argv[1:])
        from internal_policy_base.extract import reocr_backfill  # noqa: PLC0415
        st = reocr_backfill(limit=(a.limit or None), min_cjk=a.min_cjk, force=a.force,
                            retry=a.retry)
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
        if not _require_ipb_index(sub):
            return 1
        import json

        from internal_policy_base.align import align_all
        s = align_all()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "merged":
        if not _require_ipb_index(sub):
            return 1
        import json

        from internal_policy_base.merged import build_merged_view
        s = build_merged_view()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if sub == "backfill":
        # R10/B4（2026-09-08）：backfill_clauses 收敛 CLI（原仅 python -c 手工调用）
        if not _require_ipb_index(sub):
            return 1
        import json

        from internal_policy_base.extract import backfill_clauses
        s = backfill_clauses()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"未知 internal 子命令: {sub}（可用: index, align, merged, backfill, "
          "reocr, refine-identity）")
    return 1


def _index_main_internal(argv):
    """复刻 indexer.main 的 argparse（cli 内联透传）。"""
    import argparse
    ap = argparse.ArgumentParser(description="internal index")
    ap.add_argument("--source-dir", default="", help="制度源目录")
    ap.add_argument("--enable-ocr", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-unindexed", action="store_true", dest="only_unindexed",
                    help="定向补摄取：仅处理原件库中未被索引引用的制度正文")
    args = ap.parse_args(argv)
    src = args.source_dir or __import__("os").environ.get("INTERNAL_POLICY_ROOT", "")
    if not src:
        print("需提供 --source-dir 或设置 INTERNAL_POLICY_ROOT 环境变量")
        return 1
    import json

    from internal_policy_base.indexer import ingest, unindexed_originals
    only = unindexed_originals() if args.only_unindexed else None
    if only is not None:
        print(f"[index] --only-unindexed：原件库中未被索引引用 {len(only)} 个")
    s = ingest(src, enable_ocr=args.enable_ocr, dry_run=args.dry_run, only_paths=only)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0
