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


def _cmd_source(argv):
    """source list | source add --id <new_id>（R15：yaml 唯一事实源 + collector 路由消费）。"""
    if not argv:
        print("用法: orchestrator source {list|add}")
        return 1
    action = argv[0]
    if action == "list":
        try:
            from config.loader import (  # noqa: PLC0415
                active_source_ids,
                collector_module,
                collector_path,
                load_sources,
            )
        except Exception as e:  # pragma: no cover
            print(f"[source] config.loader 不可用（PyYAML 未装？）: {e}")
            return 2
        srcs = load_sources(refresh=True)
        from config.enums import SOURCE_SET  # noqa: PLC0415
        for sid, cfg in srcs.items():
            enabled = cfg.get("enabled", True)
            flag = "ON " if enabled else "OFF"
            line = f"  [{flag}] {sid:12s} {cfg.get('note', '')}"
            if enabled and "." not in sid and sid != "internal":
                mod = collector_module(sid)
                ok = "✓" if collector_path(sid) else "✗缺模块"
                line += f"  | collector={mod} {ok}"
            print(line)
        print(f"  [..] enums.SOURCE_SET={sorted(SOURCE_SET)} | yaml active={active_source_ids()}")
        return 0
    if action == "add":
        import argparse  # noqa: PLC0415
        ap = argparse.ArgumentParser(description="source add checklist（R15 新增源步骤）")
        ap.add_argument("--id", required=True, help="新源标识（如 flk）")
        a = ap.parse_args(argv[1:])
        print(f"[source add] 登记新源 {a.id!r} 的清单（sources.yaml 唯一事实源）：")
        print("  1. sources.yaml external_sources 追加条目：")
        print(f"       - id: {a.id}")
        print("         enabled: false          # 先停用登记，待 collector/清洗验证后置 true")
        print("         collector: collectors.<{id}_collector|{id}_ingest>   # 与 collectors/ 拍平命名对齐")
        print(f"         clean_project: {a.id}")
        print("         note: …")
        print("         disabled_reasons: [待采集实现验证]")
        print("  2. config/enums.py SOURCE_SET 加值（受控变更；assert_enum_bindings 断言条数随动）")
        print("  3. 提供 collectors 模块并跑：python -m py_compile + nfra_validate_cache 式只读冒烟")
        print("  4. python cli.py gates（gate_sources_config 校验 collector 模块/clean_project/enums 一致）")
        return 0
    print(f"未知 source 子命令: {action}（可用: list, add）")
    return 1


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
    if sub == "merged":
        import json

        from internal_policy_base.merged import build_merged_view
        s = build_merged_view()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"未知 internal 子命令: {sub}（可用: index, align, merged）")
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


def _cmd_classify(argv):
    """classify --theme T3|--all [--steps base,cluster,detail,...] [--dry-run]
    主题底座强序重建（R8：base→cluster→match→detail→upper→clause_graph，hash 断点幂等）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(paths.ROOT, "modules", "regulatory_classifier", "scripts"))
    sys.path.insert(0, os.path.join(paths.ROOT, "modules", "regulatory_classifier"))
    import argparse  # noqa: PLC0415

    from modules.regulatory_classifier.scripts import classify as _cl  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="主题底座强序重建（R8）")
    ap.add_argument("--theme", default="", help="单主题 T0..T10（默认 T1–T10）")
    ap.add_argument("--all", action="store_true", help="全部主题（含 T0 明细）")
    ap.add_argument("--steps", default="", help="子步白名单 base,cluster,match,detail,clause_graph,upper")
    ap.add_argument("--dry-run", action="store_true", help="仅列计划")
    a = ap.parse_args(argv)
    themes = None
    if a.all:
        themes = sorted(_cl.THEME_MAP, key=lambda x: (len(x), x))
    elif a.theme:
        themes = [a.theme]
    steps = set(s.strip() for s in a.steps.split(",") if s.strip()) or None
    res = _cl.run(themes=themes, only_steps=steps, dry_run=a.dry_run)
    print(__import__("json").dumps(res, ensure_ascii=False, indent=2))
    return 1 if res.get("error") else 0


COMMANDS = {
    "gates": _cmd_gates,
    "source": _cmd_source,
    "internal": _cmd_internal,
    "classify": _cmd_classify,
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
    p_source = sub.add_parser("source", help="源目录（config/sources.yaml 唯一事实源，R15）")
    p_source.add_argument("action", choices=["list", "add"], help="list 列出源与 collector 路由 | add 新增源 checklist")
    p_int = sub.add_parser("internal", help="内部制度摄取/对齐/引用视图（P6/P7）")
    p_int.add_argument("sub", choices=["index", "align", "merged"],
                       help="index 摄取 | align 主题对齐 | merged 制度×RFN 引用视图")
    sub.add_parser("classify", help="主题底座强序重建（R8，--theme/--all/--steps/--dry-run）")
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
