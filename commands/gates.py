# -*- coding: utf-8 -*-
"""commands.gates — orchestrator 命令：gates（自 cli.py 迁移，2026-09-13 审查 P3）。

子选项（诊断第 1 波，2026-10-16）：
  `--triage <gate>`  失败归因（只读，不跑门禁）⇒ `tools/gate_triage`
  `--triage-list`    近N 天 FAIL 概览
两者都不改变门禁语义与退出码：给了 `--triage` 走分析路径，否则跑门禁。

阶段 1（2026-09-18）：门禁结果**归档到治理库** `gate_result` 表（按 run_id）。
  - run_id 由编排经 `REG_ORCH_RUN_ID` env 下传（`tools/run_production_refresh._run`）；
  - 治理库未启用或非编排运行（无 run_id）→ 静默跳过，**不影响门禁语义与退出码**；
  - 归档失败只告警（治理库是旁路观测设施，不得反过来阻断门禁）。
"""

from __future__ import annotations


def _archive(results) -> None:
    """把本次门禁结果写入治理库（尽力而为）。"""
    try:
        from std_lib.common_lib import governance_store as gs

        run_id = gs.current_run_id()
        if not gs.enabled() or not run_id:
            return
        n = gs.record_gate_results(run_id, results)
        print(f"[gates] 已归档 {n} 条门禁结果到治理库（run_id={run_id}）")
    except Exception as e:  # noqa: BLE001
        print(f"[gates] WARN 治理库归档失败（不影响门禁结论）: {type(e).__name__}: {e}")


def run(argv):
    # 归因模式（诊断第 1 波，2026-10-16）：`cli.py gates --triage <gate>`。
    # **不跑门禁**、只做失败归因；不传该选项时下方逻辑逐字不变。
    args = list(argv or [])
    if "--triage" in args:
        from tools import gate_triage as T

        i = args.index("--triage")
        gate = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else ""
        run_id = ""
        for j, a in enumerate(args):
            if a == "--run-id" and j + 1 < len(args):
                run_id = args[j + 1]
        return T.main(args[:i] + [gate, "--run-id", run_id] if gate else ["--list"])
    if "--triage-list" in args:
        from tools import gate_triage as T

        return T.main(["--list"])

    from gates import GatesRunner

    ok, results = GatesRunner().run()
    for r in results:
        flag = "[OK] " if r["passed"] else "[FAIL]"
        print(f"  {flag}  {r['desc']}  {r.get('detail')}")
    _archive(results)
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1
