# -*- coding: utf-8 -*-
"""cli.py — orchestrator 统一入口（薄壳，2026-09-13 审查 P3）。

设计纪律（本文件仅三件事）：
  - 命令注册表 COMMANDS（映射 commands/<name>.run；业务实现全部在 commands/ 包）；
  - argparse 蓝图 build_parser（帮助文本/子命令参数声明）；
  - 分发 main（UTF-8 强置、-h 处理、未知命令提示）。
命令实现（12 个）见 commands/：gates/governance/source/internal/classify/timeliness/draft/rfn/
base/analysis/relations/ping。

⚠️ 阶段 0 澄清（2026-09-18）：`build_parser()` **仅用于 `-h/--help` 文本**，
实际分发走 `COMMANDS` 注册表（`main()` 直接 `handler(argv[1:])`，不经 argparse 校验）。
故子命令 `choices` 与 handler 不一致时**不会拒绝执行**，只会让帮助文本失真
（此前 source diff / internal reocr|refine-identity / timeliness sync|summary 即此情形，
已于本次对齐）。
"""
from __future__ import annotations

import argparse
import os
import sys

import paths
from commands import analysis as _m_analysis
from commands import base as _m_base
from commands import classify as _m_classify
from commands import draft as _m_draft
from commands import gates as _m_gates
from commands import governance as _m_governance
from commands import internal as _m_internal
from commands import ping as _m_ping
from commands import relations as _m_relations
from commands import rfn as _m_rfn
from commands import source as _m_source
from commands import timeliness as _m_timeliness

COMMANDS = {
    "gates": _m_gates.run,
    "governance": _m_governance.run,
    "source": _m_source.run,
    "internal": _m_internal.run,
    "classify": _m_classify.run,
    "timeliness": _m_timeliness.run,
    "draft": _m_draft.run,
    "rfn": _m_rfn.run,
    "base": _m_base.run,
    "analysis": _m_analysis.run,
    "relations": _m_relations.run,
    "ping": _m_ping.run,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator",
        description="regulatory_compliance_orchestrator 统一编排入口（P0 骨架）",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("gates", help="运行交付门禁（ALL_GATES）")
    p_gov = sub.add_parser("governance", help="治理库（阶段 1；水位/审计/原件注册）")
    p_gov.add_argument("sub", choices=["init", "status", "watermarks", "edges",
                                       "audit", "artifacts", "gates",
                                       "sync", "verify", "export"],
                       help="init 建库建表 | status 概览 | watermarks 产物水位 | "
                            "edges 依赖边（ok/stale/unregistered）| audit 审计日志 | "
                            "artifacts 原件注册 | gates 门禁历史 | "
                            "sync 元数据投影（阶段 2，--apply 写库）| verify 比对断言 | "
                            "export 导出文本快照")
    sub.add_parser("ping", help="骨架自检")
    p_source = sub.add_parser("source", help="源目录（config/sources.yaml 唯一事实源，R15）")
    p_source.add_argument("action", choices=["list", "add", "diff"],
                          help="list 列出源与 collector 路由 | add 新增源 checklist | "
                               "diff 快照变更监听（--record 追加基线，F-O02）")
    p_int = sub.add_parser("internal", help="内部制度摄取/对齐/引用视图（P6/P7）")
    p_int.add_argument("sub", choices=["index", "align", "merged", "backfill", "reocr", "refine-identity"],
                       help="index 摄取 | align 主题对齐 | merged 制度×RFN 引用视图 | "
                            "backfill 条文回补(R10) | reocr OCR 存量回填 | refine-identity 身份纠正")
    sub.add_parser("classify", help="主题底座强序重建（R8，--theme/--all/--steps/--dry-run）")
    p_tl = sub.add_parser("timeliness", help="时效核验（R13 三态：success/partial/unavailable）")
    p_tl.add_argument("action", choices=["verify", "sync", "summary"],
                      help="verify 效力缺失核验（透传 --source/--dry-run/--probe/--workers/--token-file）| "
                           "sync 变更台账→归属表时效同步(F-C03) | summary 读最新 verify_summary_*.json")
    p_draft = sub.add_parser("draft", help="条款级对照素材端到端编排（P8：merged_view × R21 clauses）")
    p_draft.add_argument("--ipn", default="", help="单制度 IPN-xxx（默认全部）")
    sub.add_parser("rfn", help="RFN 登记/查询（registry 唯一写口，F-C01；register/lookup）")
    sub.add_parser("base", help="双底座发布件构建与统一查询（Base Contract v1；publish/query/search）")
    sub.add_parser("analysis", help="规划 2.1 五级分析交付库（F-L01；gen/status）")
    sub.add_parser("relations", help="依据/废止关系（R-F01；gen/status/show）——三类关系唯一事实源")
    return p


def _fail_not_source_tree(cmd: str) -> int:
    """非源码树安装（业务实现不在 modules/）→ 显式失败并给出可执行指引。

    背景（2026-09-13 克隆可移植性检视 · CP-A02/A03）：本仓 `modules/` 不随 wheel 分发，
    运行时依赖 `paths.ROOT` 定位源码树（commands 以 sys.path 注入业务模块）。
    此前该情形会抛出 `ModuleNotFoundError: No module named 'rfn'` /
    `FileNotFoundError: config/sources.yaml` 等晦涩错误，掩盖"安装方式不对"这一真实原因。
    返回码 4 = 环境不满足（区别于 0 成功 / 1 用法错误 / gates 的 1 门禁失败）。
    """
    print(f"错误：未找到业务代码目录 modules/（paths.ROOT={paths.ROOT}），命令 `{cmd}` 无法执行。")
    print("本仓为**源码树编排工程**，支持的运行方式：")
    print('  1) 可编辑安装（推荐）：pip install -e ".[dev]"，随后执行 orchestrator <cmd>')
    print("  2) 免安装：在仓库根目录直接执行 python cli.py <cmd>")
    print("  3) 容器：按 .devcontainer 启动（数据与 OCR 引擎仍需在宿主准备）")
    return 4


def main(argv=None) -> int:
    # Windows 控制台默认 GBK：强制 stdout UTF-8 防 UnicodeEncodeError（含 ↔ 等符号）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    # A-11（2026-09-12）：-h/--help/help 显式处理（原仅无参打印，`cli.py --help` 报"未知命令"）。
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(build_parser().format_help())
        return 0 if argv else 1
    # 兼容 "orchestrator source list" / "orchestrator gates"
    cmd = argv[0]
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"未知命令: {cmd}（可用: {sorted(COMMANDS)}）")
        return 1
    # 源码树校验（2026-09-13）：ping 为骨架自检，允许在缺 modules/ 时继续（用于诊断）。
    if cmd != "ping" and not os.path.isdir(paths.MODULES_DIR):
        return _fail_not_source_tree(cmd)
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
