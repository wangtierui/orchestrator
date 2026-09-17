# -*- coding: utf-8 -*-
"""commands.gates — orchestrator 命令：gates（自 cli.py 迁移，2026-09-13 审查 P3）。

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
    from gates import GatesRunner
    ok, results = GatesRunner().run()
    for r in results:
        flag = "[OK] " if r["passed"] else "[FAIL]"
        print(f"  {flag}  {r['desc']}  {r.get('detail')}")
    _archive(results)
    print("====================")
    print("PASS: 全部门禁通过" if ok else "FAIL: 存在未通过门禁")
    return 0 if ok else 1
