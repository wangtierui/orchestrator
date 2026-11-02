# -*- coding: utf-8 -*-
"""tools/recount_step_records.py — 按**当前产物**复算某轮 run_step.record_count。

为什么需要它（而不是"再跑一遍链"）：
  · 计数口径改过一次（多产物求和 → 取最大，避免同批数据的不同视图被重复计），
    历史轮次里落的值仍是旧口径；
  · 复算只读产物文件、只改 `run_step.record_count` 一列，**不重跑任何生产步骤**，
    因此不会改动数据面，也不产生新的 run。

口径与生产链完全一致：直接复用 `tools.run_production_refresh._step_record_count`
（唯一实现，避免"复算"与"生产"两套算法漂移）。

用法：
    python -m tools.recount_step_records                 # 复算最近一轮非 dry-run
    python -m tools.recount_step_records --run-id RUN-…  # 指定轮次
    python -m tools.recount_step_records --dry-run       # 只打印，不写库
"""
from __future__ import annotations

import argparse


def _target_run_id(explicit: str | None) -> str:
    from std_lib.common_lib import governance_store as gs

    if explicit:
        return explicit
    rid, _rows = gs.last_run_with_steps()
    return rid or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recount_step_records", description="复算步骤记录数")
    ap.add_argument("--run-id", default="", help="目标轮次（默认最近一轮非 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只打印差异，不写库")
    args = ap.parse_args(argv)

    from std_lib.common_lib.governance_store._db import connect
    from tools.run_production_refresh import _step_record_count

    rid = _target_run_id(args.run_id or None)
    if not rid:
        print("[recount] 没有可复算的轮次（治理库为空或未启用）")
        return 2

    conn = connect(readonly=False)
    rows = conn.execute(
        "SELECT step, record_count FROM run_step WHERE run_id=? ORDER BY step", (rid,)
    ).fetchall()
    if not rows:
        print(f"[recount] 轮次 {rid} 没有步骤行")
        return 2

    changed, same = [], 0
    for step, old in rows:
        new = _step_record_count(str(step))
        if int(old) == int(new):
            same += 1
            continue
        changed.append((str(step), int(old), int(new)))

    for step, old, new in changed:
        print(f"  {step}: {old} → {new}")
    print(f"[recount] 轮次 {rid}：{len(rows)} 步，变更 {len(changed)}，未变 {same}"
          f"{'（dry-run，未写库）' if args.dry_run else ''}")

    if changed and not args.dry_run:
        conn.executemany(
            "UPDATE run_step SET record_count=? WHERE run_id=? AND step=?",
            [(new, rid, step) for step, _old, new in changed],
        )
        conn.commit()
        print("[recount] 已写回 run_step.record_count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
