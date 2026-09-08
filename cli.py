# -*- coding: utf-8 -*-
"""
cli.py — orchestrator 统一入口（v1 §5 / 专项⑤代码固化）

用法（P0 阶段可用）：
  python cli.py --help
  python cli.py gates                       # 运行交付门禁
  python cli.py source list                 # 列出源（config/sources.yaml 派生）
  python cli.py ping                        # 骨架自检

P1 起扩展子命令：collect/clean/index/retrieval/classify/report/internal/draft/timeliness/pipeline。
设计纪律：
  - 每阶段对应独立实现模块（后续在 commands/ 或 modules 内），本文件只做路由 + 退出码聚合；
  - 幂等/签名断点在阶段实现内；禁止在 cli.py 写业务逻辑。
"""
from __future__ import annotations

import argparse
import sys

import paths


def _cmd_gates(argv):
    from gates import GatesRunner
    ok, results = GatesRunner().run()
    for r in results:
        flag = "[OK] " if r["passed"] else "[FAIL]"
        print(f"  {flag}  {r['desc']}  {r.get('detail')}")
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1


def _cmd_source_list(argv):
    try:
        from config.loader import load_sources
    except Exception as e:
        print(f"[source] config.loader 不可用（PyYAML 未装？）: {e}")
        return 2
    srcs = load_sources(refresh=True)
    for sid, cfg in srcs.items():
        enabled = "ON " if cfg.get("enabled", True) else "OFF"
        note = cfg.get("note", "")
        print(f"  [{enabled}] {sid:12s} {note}")
    return 0


def _cmd_ping(argv):
    print(f"REG_ORCH_ROOT = {paths.ROOT}")
    print("P0 骨架 OK：paths / config / interfaces / gates / std_lib 已就位")
    return 0


def _cmd_internal(argv):
    """internal index|align [--source-dir ...] — 内部制度摄取/对齐（P6）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(paths.ROOT, "modules"))
    if not argv:
        print("用法: orchestrator internal {index|align} [--source-dir DIR] [--dry-run]")
        return 1
    sub = argv[0]
    if sub == "index":
        # 透传剩余参数（--source-dir/--enable-ocr/--dry-run）
        return _index_main_internal(argv[1:])
    if sub == "align":
        import json

        from internal_policy_base.align import align_all
        s = align_all()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"未知 internal 子命令: {sub}（可用: index, align）")
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


COMMANDS = {
    "gates": _cmd_gates,
    "source": _cmd_source_list,
    "internal": _cmd_internal,
    "ping": _cmd_ping,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator",
        description="regulatory_compliance_orchestrator 统一编排入口（P0 骨架）",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("gates", help="运行交付门禁（ALL_GATES）")
    sub.add_parser("ping", help="骨架自检")
    p_source = sub.add_parser("source", help="源目录（config/sources.yaml）")
    p_source.add_argument("action", choices=["list"], help="list 列出源")
    p_int = sub.add_parser("internal", help="内部制度摄取/对齐（P6）")
    p_int.add_argument("sub", choices=["index", "align"], help="index 摄取 | align 主题对齐")
    return p


def main(argv=None) -> int:
    # Windows 控制台默认 GBK：强制 stdout UTF-8 防 UnicodeEncodeError（含 ↔ 等符号）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(build_parser().format_help())
        return 1
    # 兼容 "orchestrator source list" / "orchestrator gates"
    cmd = argv[0]
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"未知命令: {cmd}（可用: {sorted(COMMANDS)}）")
        return 1
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
