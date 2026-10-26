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

from config.exitcodes import ExitCode


def _api():
    from bootstrap import bootstrap

    bootstrap()
    from interfaces import relations_api

    return relations_api


def _gen(argv) -> int:
    """全量抽取 → **落盘 → 投影入库**（一条命令完成）。

    为什么把 sync 并进来（2026-10-26 用户反馈）
    -------------------------------------------
    以前要跑两条：`relations gen`（写 jsonl）+ `governance sync --apply`（投影进库），
    而**确认台读库** ⇒ 只跑第一条会看到旧关系（实测：jsonl 4509 条、界面仍 5281 条）。
    "生成成功"必须等于"界面可见"，所以投影是这条命令的**收尾步骤**，不是可选后续动作。

    `--no-sync`：只写文件、不投影（调试/批量重抽时用；链上 `governance:sync` 会统一投影）。
    `--dry-run` 隐含不等价于 `--no-sync`：dry-run 本身不落盘，投影会读到**旧**文件 ⇒ 一律跳过。
    """
    from bootstrap import bootstrap

    bootstrap(include_tools=True)
    import argparse

    from extract_relations import run
    from governance_sync import sync_relations

    ap = argparse.ArgumentParser(prog="orchestrator relations gen")
    ap.add_argument("--source", action="append", default=None)
    ap.add_argument("--no-internal", action="store_true")
    ap.add_argument("--with-attachments", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--no-sync", action="store_true",
                    help="只写 jsonl，不投影进治理库（默认会投影，供确认台立即可见）")
    a = ap.parse_args(argv)
    run(
        sources=a.source,
        limit=a.limit,
        dry_run=a.dry_run,
        with_internal=not a.no_internal,
        with_attachments=a.with_attachments,
        report=a.report,
    )
    if a.dry_run or a.no_sync:
        print("[relations] 已跳过投影（%s）"
              % ("dry-run" if a.dry_run else "--no-sync"))
        return ExitCode.OK

    out = sync_relations(apply=True)
    print(f"[relations] 已投影进治理库：{out['db_rows']} 行"
          f"（源 {out['src_rows']} 行）"
          f"{'，摘要一致' if out['ok'] else '，**摘要不一致**'}")
    for n in out.get("notes") or []:
        print(f"  注：{n}")
    if not out["available"]:
        print("[relations][warn] 治理库不可用 ⇒ 确认台仍读不到新关系"
              "（先 `python cli.py governance init`）")
        return ExitCode.DATA
    return ExitCode.OK if out["ok"] else ExitCode.FAIL


def _sync(argv) -> int:
    """只投影（不重抽取）：jsonl 已在、库过期时用。"""
    from bootstrap import bootstrap

    bootstrap(include_tools=True)
    import argparse

    from governance_sync import sync_relations

    ap = argparse.ArgumentParser(prog="orchestrator relations sync")
    ap.add_argument("--check", action="store_true", help="只比对，不写库")
    a = ap.parse_args(argv)
    out = sync_relations(apply=not a.check)
    print(f"[relations] {'比对' if a.check else '投影'}完成："
          f"库 {out['db_rows']} 行 / 源 {out['src_rows']} 行 ⇒ "
          f"{'一致' if out['ok'] else '**分叉**'}")
    if not out["available"]:
        return ExitCode.DATA
    return ExitCode.OK if out["ok"] else ExitCode.FAIL


def _status(argv) -> int:
    api = _api()
    s = api.stat()
    if not s:
        print(f"[relations] 产物缺失：{api.INDEX_PATH}")
        print("[relations] 先运行 `python cli.py relations gen` 生成。")
        return ExitCode.FAIL
    print(json.dumps(api.summary(), ensure_ascii=False, indent=2))
    cross = len(api.load_cross_basis())
    print(f"  类别 3 纯依据边（cross_basis.jsonl）：{cross} 条")
    if argv and argv[0] == "--samples":
        for r in api.load("cross")[:8]:
            print(
                f"   {r['src_ref']} → {r['dst_ref'] or r['dst_key'] or '(未解析)'} "
                f"| {r['dst_name'][:44]} | {r['matched_by']}"
            )
    return ExitCode.OK


def _show(argv) -> int:
    if not argv:
        print("用法: orchestrator relations show <RFN-xxx|IPN-xxx>")
        return ExitCode.FAIL
    api = _api()
    ref = argv[0]
    as_src, as_dst = api.by_src(ref), api.by_dst(ref)
    print(f"[relations] {ref}：作为源 {len(as_src)} 条 / 作为目标 {len(as_dst)} 条")
    for r in as_src[:20]:
        print(
            f"  [出/{r['relation']:6s}] → {r['dst_ref'] or r['dst_key'] or '(未解析)':<22s}"
            f" {r['dst_name'][:40]} ({r['matched_by']})"
        )
    for r in as_dst[:20]:
        print(
            f"  [入/{r['relation']:6s}] ← {r['src_ref'] or r['src_name'][:20]:<22s}"
            f" {r['src_name'][:40]} ({r['matched_by']})"
        )
    return ExitCode.OK


def run(argv) -> int:
    """relations {gen|sync|status|show} — 依据/废止关系（R-F01）。

    `gen` 已内含投影（jsonl → 治理库），等价于旧的两条命令：
        relations gen  ==  extract_relations  +  governance sync（仅 relation 表）
    """
    if not argv:
        print("用法: orchestrator relations {gen|sync|status|show}")
        return ExitCode.FAIL
    sub, rest = argv[0], argv[1:]
    if sub == "gen":
        return _gen(rest)
    if sub == "sync":
        return _sync(rest)
    if sub == "status":
        return _status(rest)
    if sub == "show":
        return _show(rest)
    print(f"未知子命令: {sub}（可用: gen/sync/status/show）")
    return ExitCode.FAIL
