# -*- coding: utf-8 -*-
"""commands.classify — orchestrator 命令：classify（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations


def run(argv):
    """classify --theme T3|--all [--steps base,cluster,detail,...] [--dry-run]
    主题底座强序重建（R8：base→cluster→match→detail→upper→clause_graph，hash 断点幂等）。"""
    import argparse  # noqa: PLC0415

    # P0-2（v2 §3.1.2）：sys.path 引导统一走 bootstrap（scripts/ 为非包目录，经 extra 注入）
    from bootstrap import bootstrap  # noqa: PLC0415
    bootstrap("regulatory_classifier",
              extra=("modules/regulatory_classifier/scripts",))

    from modules.regulatory_classifier.scripts import classify as _cl  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="主题底座强序重建（R8）")
    ap.add_argument("--theme", default="", help="单主题 T0..T10（默认 T1–T10）")
    ap.add_argument("--all", action="store_true", help="全部主题（含 T0 明细）")
    ap.add_argument("--steps", default="", help="子步白名单 base,cluster,match,detail,clause_graph,upper")
    ap.add_argument("--dry-run", action="store_true", help="仅列计划")
    ap.add_argument("--no-analysis", action="store_true",
                    help="跳过重建后自动刷新分析交付库（F-L01；默认自动刷新）")
    a = ap.parse_args(argv)
    themes = None
    if a.all:
        themes = sorted(_cl.THEME_MAP, key=lambda x: (len(x), x))
    elif a.theme:
        themes = [a.theme]
    steps = set(s.strip() for s in a.steps.split(",") if s.strip()) or None
    res = _cl.run(themes=themes, only_steps=steps, dry_run=a.dry_run)
    print(__import__("json").dumps(res, ensure_ascii=False, indent=2))
    if res.get("error"):
        return 1
    # F-L01（2026-09-12）：数据重建后自动刷新分析交付库（docs/reports/ 17 项，含 2026-09-14 纳管的关系类 2 项）。
    # 失败不阻断 classify（交付库可经 `cli.py analysis gen` 手动补跑）。
    if not a.dry_run and not a.no_analysis:
        try:
            bootstrap(include_tools=True)
            from gen_analysis_deliveries import main as _gen  # noqa: PLC0415
            _rc = _gen([])
            print(f"[classify] 分析交付库已自动刷新（rc={_rc}；--no-analysis 可跳过）")
        except Exception as _e:  # noqa: BLE001
            print(f"[classify] ⚠ 分析交付库自动刷新失败（不阻断；可手动 analysis gen）：{_e!r}")
    return 0
