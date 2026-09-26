# -*- coding: utf-8 -*-
"""commands.governance — orchestrator 命令：governance（阶段 1 治理库，2026-09-18）

用法：
  orchestrator governance init                    建库建表（幂等，含投影表 schema 升级）
  orchestrator governance status                  概览（表计数 + 水位一致性 + 最近运行）
  orchestrator governance watermarks [--json]    产物水位全量
  orchestrator governance edges [--json]          水位展开的依赖边（ok/stale/unregistered）
  orchestrator governance audit [--limit N] [--target T] [--key K]
  orchestrator governance artifacts [--kind pdf] [--limit N]
  orchestrator governance gates [--run RUN-xxx] [--limit N]
  orchestrator governance sync [--check] [--export]   元数据投影（阶段 2；默认仅比对）
  orchestrator governance verify                 比对断言（= sync --check）
  orchestrator governance export [--out DIR]     导出治理库文本快照 + manifest

设计：本命令是治理库的观测面。**写路径唯一实现** =
`std_lib/common_lib/governance_store.py`（表级唯一写方，见方案 §4.3）；
元数据四表的投影唯一入口 = `tools/governance_sync.py`（本命令 `sync` 委托之）。
"""

from __future__ import annotations

import json as _json

from config.exitcodes import ExitCode


def _flag(argv, name, default=None):
    """极简取值：`--name value`（避免为只读观测命令引入 argparse 噪声）。"""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def run(argv):
    from interfaces import governance_api as g

    sub = argv[0] if argv else "status"
    if sub in ("sync", "verify"):
        # 阶段 2：事实源 → 治理库元数据投影（唯一实现 tools/governance_sync.py）。
        # `verify` = `sync --check`；`sync` 默认也**只比对**（写库须显式 --apply），
        # 避免误手执行把库推成与文件不一致的状态。
        from bootstrap import bootstrap  # noqa: PLC0415

        bootstrap(include_tools=True)
        import governance_sync as _sync  # noqa: PLC0415

        argv2 = list(argv[1:])
        if sub == "verify" and "--check" not in argv2 and "--apply" not in argv2:
            argv2.append("--check")
        return _sync.main(argv2)
    if sub == "export":
        from std_lib.common_lib import governance_store as gs

        out_dir = _flag(argv, "--out", "") or __import__("os").path.join(
            __import__("os").path.dirname(
                __import__("os").path.dirname(__import__("os").path.abspath(__file__))
            ),
            "exports",
        )
        out = gs.export_snapshot(out_dir)
        if not out.get("enabled"):
            print("[governance] 治理库未启用（先 `governance init` 并跑一次 sync --apply）")
            return ExitCode.FAIL
        print(_json.dumps(out, ensure_ascii=False, indent=1))
        return ExitCode.OK
    if sub == "init":
        from std_lib.common_lib import governance_store as gs

        p = gs.init_db()
        print(f"[governance] 治理库已就绪：{p}")
        print(f"[governance] 表：{', '.join(gs.TABLES)}（schema_version={gs.SCHEMA_VERSION}）")
        print(
            "[governance] 说明：位于仓根 data/（数据不入 git）；写路径唯一实现 = "
            "std_lib/common_lib/governance_store.py"
        )
        return ExitCode.OK
    if sub == "status":
        snap = g.status()
        print(_json.dumps(snap, ensure_ascii=False, indent=2))
        return 0 if snap.get("enabled") else 1
    if sub == "watermarks":
        rows = g.watermarks()
        if "--json" in argv:
            print(_json.dumps(rows, ensure_ascii=False, indent=2))
            return ExitCode.OK
        if not rows:
            print("[governance] 无水位记录（治理库未启用或尚未运行刷新链）")
            return ExitCode.FAIL
        print(f"{'artifact_key':<24}{'version':<20}{'records':>9}  produced_by")
        for r in rows:
            print(
                f"{r['artifact_key']:<24}{r['version']:<20}"
                f"{(r.get('record_count') if r.get('record_count') is not None else -1):>9}"
                f"  {r.get('produced_by', '')}"
            )
        return ExitCode.OK
    if sub == "edges":
        rows = g.edges()
        if "--json" in argv:
            print(_json.dumps(rows, ensure_ascii=False, indent=2))
            return ExitCode.OK
        if not rows:
            print("[governance] 无依赖边（水位登记尚未覆盖任何 inputs 声明）")
            return ExitCode.FAIL
        print(f"{'artifact':<24}{'dep':<24}{'declared':<18}{'current':<18}status")
        for e in rows:
            print(
                f"{e['artifact']:<24}{e['dep']:<24}{str(e['declared_version']):<18}"
                f"{str(e['current_version']):<18}{e['status']}"
            )
        ok, det = g.check()
        print(
            f"[governance] 一致性：{'PASS' if ok else 'FAIL'} | "
            f"edges={det.get('edges')} stale={det.get('stale_edges')} "
            f"unregistered={det.get('unregistered_deps')}"
        )
        return 0 if ok else 1
    if sub == "audit":
        rows = g.audit(
            limit=int(_flag(argv, "--limit", 50)),
            target=_flag(argv, "--target", "") or "",
            target_key=_flag(argv, "--key", "") or "",
        )
        for r in rows:
            print(
                f"  {r['ts']}  {r['action']:<14}{r['target']}｜{r['target_key']}"
                f"  {r['field']}: {r['old_value']} → {r['new_value']}"
                f"  [{r['basis']}]"
            )
        print(f"[governance] 共 {len(rows)} 条")
        return ExitCode.OK
    if sub == "artifacts":
        rows = g.artifacts(
            limit=int(_flag(argv, "--limit", 0)), kind=_flag(argv, "--kind", "") or ""
        )
        for r in rows:
            print(
                f"  {r['artifact_key']}  {r['kind']:<6}  {r['bytes']:>12}  "
                f"paths={len(r.get('paths') or [])}"
            )
        print(f"[governance] 共 {len(rows)} 件（path_keys 为集合：跨层硬链接共享 inode）")
        return ExitCode.OK
    if sub == "gates":
        rows = g.gate_results(
            run_id=_flag(argv, "--run", "") or "", limit=int(_flag(argv, "--limit", 100))
        )
        cur = None
        for r in rows:
            if r["run_id"] != cur:
                cur = r["run_id"]
                print(f"[governance] run {cur}")
            print(f"    {'[OK] ' if r['passed'] else '[FAIL]'}  {r['descr']}")
        print(f"[governance] 共 {len(rows)} 条")
        return ExitCode.OK
    print(
        f"未知 governance 子命令: {sub}（可用: init | status | watermarks | edges | "
        "audit | artifacts | gates）"
    )
    return ExitCode.FAIL
