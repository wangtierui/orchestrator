# -*- coding: utf-8 -*-
"""
commands/status — 状态与待办自披露（v2 §3.13.5，P2-6）

把"人需要记住的"（下一步跑什么 / 有几个待办 / 哪些阶段上次失败了）变成**可查询**：
数据来源全部是事实源（`run_log` / `run_step` / `watermark` / `worklist` / `doctor.json` /
`schedule.yaml`），**不含任何助手记忆**（v2 §3.13.5 明确要求）。

用法：
    python cli.py status                 # 人类摘要
    python cli.py status --json          # 机器可读
    python cli.py status --exit-code     # 有 stale 水位或有 open 待办 → 非 0（供告警联动）
"""

from __future__ import annotations

import json
import os

import paths
from config.exitcodes import ExitCode  # noqa: E402  (R3：退出码语义化)
from std_lib.common_lib import governance_store as gs

DOCTOR_JSON = os.path.join(paths.ROOT, "reports", "_tmp", "doctor.json")
# R12（v2 §5）：open 待办**不设为门禁 FAIL**（避免长期卡死交付），但滞留超阈值要告警
WORKLIST_AGED_DAYS = 14


def _run_summary() -> dict:
    runs = gs.list_runs(limit=1) if gs.enabled() else []
    if not runs:
        return {"run_id": "", "failed": [], "steps": []}
    rid = runs[0]["run_id"]
    steps = gs.steps_of(rid)
    return {
        "run_id": rid,
        "started_at": runs[0].get("started_at", ""),
        "finished_at": runs[0].get("finished_at", ""),
        "ok": runs[0].get("ok"),
        "note": runs[0].get("note", ""),
        "steps": steps,
        "failed": [s for s in steps if s.get("rc")],
        "skipped": [s for s in steps if s.get("skipped")],
    }


def _watermark_summary() -> dict:
    if not gs.enabled():
        return {"enabled": False}
    ok, det = gs.check_dependencies()
    wms = gs.list_watermarks()
    return {
        "enabled": True,
        "ok": ok,
        "problems": (det.get("problems") or [])[:5],
        "total": len(wms),
        "detail": det,
    }


def _worklist_summary() -> dict:
    st = gs.worklist_stats() if gs.enabled() else {"enabled": False, "open": 0, "by_kind": {}}
    rows = gs.worklist_list(status="open") if gs.enabled() else []
    oldest = ""
    if rows:
        oldest_row = min(rows, key=lambda r: r.get("created_at", ""))
        oldest = f"{oldest_row.get('item_id', '')} ({oldest_row.get('created_at', '')[:10]})"
    return {
        **st,
        # worklist_stats 返回裸 dict（历史遗留，值类型运行时确定：oldest_open_days 恒为 int）
        "aged": (int(st.get("oldest_open_days", 0)) >= WORKLIST_AGED_DAYS),  # type: ignore[arg-type]
        "oldest": oldest,
        "aged_threshold_days": WORKLIST_AGED_DAYS,
    }


def _doctor_summary() -> dict:
    if not os.path.exists(DOCTOR_JSON):
        return {"present": False}
    try:
        d = json.load(open(DOCTOR_JSON, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"present": True, "error": f"{type(e).__name__}"}
    return {
        "present": True,
        "checked": d.get("checked"),
        "fail": d.get("fail"),
        "warn": d.get("warn"),
        "failed_ids": d.get("failed_ids"),
    }


def _schedule_summary() -> dict:
    try:
        from bootstrap import bootstrap  # noqa: PLC0415

        bootstrap("all", include_tools=True)  # commands/ 禁自行注入（gate_import_bootstrap 硬零层）
        import install_schedule  # noqa: PLC0415

        jobs = install_schedule.cron_jobs()
        return {
            "jobs": len(jobs),
            "ids": [j["id"] for j in jobs],
            "installed": None,
        }  # 已安装状态由 `cli.py schedule verify` 给出（较慢）
    except Exception as e:  # noqa: BLE001
        return {"jobs": 0, "error": f"{type(e).__name__}"}


def collect() -> dict:
    return {
        "run": _run_summary(),
        "watermark": _watermark_summary(),
        "worklist": _worklist_summary(),
        "doctor": _doctor_summary(),
        "schedule": _schedule_summary(),
    }


def render(res: dict) -> str:
    r, w, k = res["run"], res["watermark"], res["worklist"]
    lines: list[str] = []
    if r["run_id"]:
        status = "ok" if r.get("ok") else ("partial" if r.get("finished_at") else "running")
        lines.append(
            f"本轮 : {r['run_id']}  status={status}  failed={len(r['failed'])}"
            f"  skipped={len(r['skipped'])}"
        )
        if r["failed"]:
            detail = " · ".join(f"{s['step']}(rc={s['rc']})" for s in r["failed"][:5])
            lines.append(f"       └ 失败: {detail}")
    else:
        lines.append("本轮 : （治理库无运行记录）")
    if w.get("enabled"):
        n_bad = len(w.get("problems") or [])
        lines.append(
            f"水位 : {w['total']} 条登记 / {n_bad} 项异常"
            f"{'（→ gate_watermark 预测: FAIL）' if n_bad else '（ok）'}"
        )
        for p in (w.get("problems") or [])[:3]:
            lines.append(f"       └ {p}")
    else:
        lines.append("水位 : （治理库未启用）")
    if k.get("enabled") is False:
        lines.append("待办 : （治理库未启用）")
    else:
        by_kind = " | ".join(f"{a} {b}" for a, b in (k.get("by_kind") or {}).items())
        lines.append(f"待办 : {k.get('open', 0)} open  ({by_kind or '无'})")
        if k.get("oldest"):
            flag = " ⚠️ 超阈值" if k.get("aged") else ""
            lines.append(
                f"       └ 最长滞留: {k['oldest']} {k.get('oldest_open_days', 0)}d"
                f"（阈值 {k.get('aged_threshold_days')}d）{flag}"
            )
    d = res["doctor"]
    if d.get("present"):
        lines.append(
            f"环境 : doctor {d.get('checked')} 项  fail={d.get('fail')}"
            f" warn={d.get('warn')}"
            f"{'  ' + str(d.get('failed_ids')) if d.get('failed_ids') else ''}"
        )
    else:
        lines.append("环境 : （无 doctor.json；跑 `cli.py doctor`）")
    s = res["schedule"]
    lines.append(
        f"调度 : schedule.yaml {s.get('jobs', 0)} 项 "
        f"{s.get('ids') or ''}（安装状态见 `cli.py schedule verify`）"
    )
    return "\n".join(lines)


def _exit_code(res: dict) -> int:
    """`--exit-code`：有 stale/未登记水位、或有 open 待办、或 doctor FAIL → 非 0（供告警联动）。"""
    if res["watermark"].get("enabled") and (res["watermark"].get("problems") or []):
        return ExitCode.DATA
    if res["worklist"].get("open"):
        return ExitCode.FAIL
    if res["doctor"].get("fail"):
        return ExitCode.ENV
    return ExitCode.OK


def main(argv=None) -> int:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="状态与待办自披露（v2 §3.13.5）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
    ap.add_argument(
        "--exit-code",
        action="store_true",
        help="按状态取退出码：有 open 待办→1 / 水位异常→2 / doctor FAIL→3",
    )
    args = ap.parse_args(argv)
    res = collect()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(res))
    return _exit_code(res) if args.exit_code else 0


def run(argv=None) -> int:
    return main(argv)
