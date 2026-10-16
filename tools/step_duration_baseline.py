"""F1（方案 §13.8）：**步骤耗时性能基线**的采集与判读。

为什么需要（实测依据）
----------------------
`tools/style_report.py` 与 §13.11 行数预算解决的是「规模」，而**耗时**此前完全没有基线：
`run_step` 只有一个 `elapsed_s` 累计值，任何"这一步变慢了"的判断都只能靠人肉对比某次输出。
本工具把「同一步的历史耗时分布」固化为**可复算的基线文件**，让回归可被机器发现。

为什么现在能做（C3 刚落地的三列给了新数据）
---------------------------------------------
· `is_dry_run`：dry-run 的 `elapsed_s` 是**进程启动+列argv**的耗时（通常 <0.1s），
  混进统计会把分布压成一条贴着 0 的尖峰 ⇒ 必须先排除（TD-3 的同一判据）。
· `started_at` / `finished_at`：给出**时间窗**，可判定「本步的产物是否落在窗内」
  （N-152「危险组合」判定的基础，此前无法做）。

口径与产物
----------
产物：`data/run_state/step_duration_baseline.json`
    {generated_at, sample_runs, per_step: {step: {n, p50, p90, max, last, samples[]}},
     windows: {...}, notes}
读取侧只读json（不连库），符合「产物是事实源、库是投影」纪律。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

TZ8 = timezone(timedelta(hours=8))
OUT_NAME = "step_duration_baseline.json"


def _now_iso() -> str:
    return datetime.now(TZ8).replace(microsecond=0).isoformat()


def _pct(values: list[float], q: float) -> float:
    """分位数（线性插值；样本少时退化为最近秩，避免小样本统计失真）。"""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 2)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return round(xs[lo] + (xs[hi] - xs[lo]) * frac, 2)


def _open_conn() -> sqlite3.Connection | None:
    """打开治理库（只读）。不存在返回 None。"""
    db = os.path.join(paths.DATA_DIR, "governance.db")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def collect(window_runs: int = 30) -> dict:
    """采集基线（只读治理库，不写库）。"""
    con = _open_conn()
    if con is None:
        return {"available": False, "reason": "治理库不存在", "generated_at": _now_iso()}
    return _collect_from(con, window_runs)


def _collect_from(con: sqlite3.Connection, window_runs: int = 30) -> dict:
    """基线计算（**连接可注入** ⇒ 口径可测；只读，不写库）。

    抽出来是为了让契约测试用内存库验证口径（dry-run 排除 / 跳过样本排除 /
    分位数 / 时间窗），而不是只能对真库跑一遍"看起来对"。
    """
    # 列存在性（老库未迁移时降级：只统计 elapsed_s，并在 note 里披露）
    cols = {r[1] for r in con.execute("PRAGMA table_info(run_step)")}
    has_dry = "is_dry_run" in cols
    has_win = "started_at" in cols and "finished_at" in cols
    dry_pred = "is_dry_run=1 OR note LIKE ?" if has_dry else "note LIKE ?"
    args: tuple = ("%dry-run%",)

    # 最近 N 次**生产运行**（排除 dry-run）
    runs = [r[0] for r in con.execute(
        f"select run_id from run_log where run_id not in ("
        f"  select distinct run_id from run_step where {dry_pred}) "
        f"order by started_at desc limit ?", (*args, window_runs))]
    # 排除量披露（与 SLI 同口径）：读者据此判断基线是否可能被非生产流量污染
    excluded = [r[0] for r in con.execute(
        f"select count(distinct run_id) from run_step where {dry_pred}", args)][0]
    if not runs:
        return {"available": False, "reason": "无生产运行记录",
                "excluded_dry_run_total": int(excluded or 0), "generated_at": _now_iso()}

    per_step: dict[str, dict] = {}
    windows: dict[str, dict] = {}
    for rid in runs:
        q = ("select step, elapsed_s, started_at, finished_at, rc, skipped "
             "from run_step where run_id=?")
        for r in con.execute(q, (rid,)):
            step = str(r["step"])
            e = float(r["elapsed_s"] or 0.0)
            slot = per_step.setdefault(
                step, {"n": 0, "samples": [], "fails": 0, "skipped_n": 0})
            slot["skipped_n"] += 1 if r["skipped"] else 0
            # ⚠️ **跳过样本不计入耗时分布**：`--resume` 跳过的步骤 `elapsed_s≈0`
            # （未执行），混进分布会把 p50 压到 0.00（实测 18 个步骤里多数如此）
            # ⇒ 基线只能反映「真正跑了的那些次」。跳过率单列（续跑效率的另一项指标）。
            if not r["skipped"]:
                slot["n"] += 1
                slot["samples"].append(e)
            if r["rc"] not in (0,):
                slot["fails"] += 1
            if has_win and r["started_at"]:
                w = windows.setdefault(step, {"n": 0, "last_start": 0.0, "last_end": 0.0})
                w["n"] += 1
                w["last_start"] = max(w["last_start"], float(r["started_at"]))
                w["last_end"] = max(w["last_end"], float(r["finished_at"] or 0.0))

    for slot in per_step.values():
        xs = slot.pop("samples")
        slot["p50"] = _pct(xs, 0.5)
        slot["p90"] = _pct(xs, 0.9)
        slot["max"] = round(max(xs), 2) if xs else 0.0
        slot["last"] = round(xs[-1], 2) if xs else 0.0
        slot["mean"] = round(statistics.fmean(xs), 2) if xs else 0.0
        slot["unit"] = "s"
        slot["skip_rate"] = (round(100.0 * slot["skipped_n"] / (slot["n"] + slot["skipped_n"]), 1)
                             if (slot["n"] + slot["skipped_n"]) else 0.0)

    out = {
        "available": True,
        "generated_at": _now_iso(),
        "source": "governance.db.run_step（只读）",
        "window_runs": window_runs,
        "sample_runs": len(runs),
        "excluded_dry_run_total": int(excluded or 0),
        "dry_run_predicate": "is_dry_run 列" if has_dry else "note 文本（老库未迁移该列）",
        "has_time_window": has_win,
        "per_step": dict(sorted(per_step.items(), key=lambda kv: -kv[1]["p50"])),
        "windows": windows,
        "notes": [
            "dry-run 已排除（其 elapsed_s 是「列 argv」的进程耗时，会压扁分布）",
            "p50/p90 为线性插值分位数；样本 n 很小时退化为最近秩",
            "**跳过样本（--resume 跳过，elapsed_s≈0）不计入耗时分布**；skip_rate 单列（续跑效率）",
            "windows 仅在新列（started_at/finished_at）存在时产出，供「产物是否落在执行窗内」判定",
        ],
    }
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="步骤耗时基线采集（方案 §13.8）")
    ap.add_argument("--runs", type=int, default=30, help="回看最近 N 次生产运行")
    ap.add_argument("--out", default="", help="输出路径（默认 data/run_state/…）")
    ap.add_argument("--stdout", action="store_true", help="同时打印全文")
    a = ap.parse_args(argv)

    doc = collect(a.runs)
    out_dir = os.path.join(paths.DATA_DIR, "run_state")
    os.makedirs(out_dir, exist_ok=True)
    p = a.out or os.path.join(out_dir, OUT_NAME)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    if not doc.get("available"):
        print(f"[baseline] 不可用：{doc.get('reason')}（已写 {os.path.relpath(p, paths.ROOT)}）")
        return ExitCode.OK

    print(f"[baseline] → {os.path.relpath(p, paths.ROOT)}"
          f"（{doc['sample_runs']} 次生产运行，dry-run 判据={doc['dry_run_predicate']}）")
    print(f"{'步骤':<26}{'n':>4}{'跳过%':>7}{'p50':>10}{'p90':>10}{'max':>10}{'末次':>10}{'失败':>6}")
    for step, s in list(doc["per_step"].items())[:18]:
        print(f"{step[:25]:<26}{s['n']:>4}{s.get('skip_rate', 0):>7.1f}"
              f"{s['p50']:>10.2f}{s['p90']:>10.2f}{s['max']:>10.2f}{s['last']:>10.2f}{s['fails']:>6}")
    if doc["has_time_window"]:
        print(f"[baseline] 时间窗可用：{len(doc['windows'])} 个步骤有 started/finished_at")
    if a.stdout:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
