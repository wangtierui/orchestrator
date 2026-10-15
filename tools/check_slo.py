# -*- coding: utf-8 -*-
"""tools/check_slo — **SLI/SLO 判定与错误预算**（方案 §13.3，P1；阈值定义见 ADR-0012）。

三个 SLI（唯一事实源 = `config/schedule.yaml` 的 `notify.sli` 段，本工具只读不算）：
  · sli_chain_success_rate：最近 N 次运行中 `failed=0` 的占比
  · sli_data_freshness    ：关键产物水位 age ≤ 族阈值的占比（阈值同 gen_quality_metrics）
  · sli_gate_pass_rate    ：最近 N 轮 gates 全绿占比（准入门，SLO=100%）

行为：
  · 连续 **2 个周期**低于 SLO ⇒ 发 `slo_breach` 告警（受控事件，schedule.yaml 已登记）；
  · 每次运行把 SLI 快照与预算消耗写入 `data/run_state/slo_history.jsonl`（趋势可查）；
  · **只判定不阻断**（预算耗尽时输出建议：冻结非必需变更，由用户裁决——ADR-0012 §错误预算）。

用法：python -m tools.check_slo [--json] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

# 引导：**唯一引导点 `bootstrap`** —— 不自写 `sys.path.insert`
# （门禁 `gate_import_bootstrap` 判据：tools/ 的注入数**只减不增**，新代码必须走此形态，
#  与 `tools/audit_health.py` 同款）。因此运行方式为 **模块方式**：
#     python -m tools.check_slo
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT
TZ8 = timezone(timedelta(hours=8))
# 快照台账落 `reports/slo/`（入库）：SLI 趋势是**审计证据**（错误预算复盘要用），
# 不放 data/run_state/（该目录整体被 .gitignore 忽略）。
HISTORY = os.path.join(paths.REPORTS_DIR, "slo", "slo_history.jsonl")
# 新鲜度族阈值（与 gen_quality_metrics.FRESH_DAYS 同源，避免两处漂移）
FRESH_DAYS = {"raw": 7, "cleaned": 10, "clauses": 30, "published": 30}
BREACH_CONSECUTIVE = 2  # 连续 N 个周期低于 SLO 才告警（防单点抖动误报）


def _cfg() -> dict:
    import yaml

    p = os.path.join(paths.CONFIG_DIR, "schedule.yaml")
    with open(p, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("notify", {}).get("sli", {}) or {}


def _rows(conn, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return list(conn.execute(sql, args))
    except Exception:  # noqa: BLE001 取数失败 ⇒ 该 SLI 不可用（不猜）
        return []


#: dry-run 识别的 note 片段（与 `tools/run_production_refresh.py` 写入的文本一致）。
#: 用既有 `note` 文本识别而非加列——零 schema 变更、零主链改动。
_DRY_RUN_NOTE = "%dry-run%"


def sli_chain(conn, window: int) -> dict | None:
    """链成功率：最近 N 次**生产运行**（run_log，排除 dry-run）中 failed=0 的占比。

    ⚠️ 口径（2026-10-16 修正）：此前把 dry-run 也当"运行"，而 dry-run 每步 rc=0
    ⇒ 只要窗口内多为 dry-run，成功率恒 100%，SLO 永不触发。dry-run **不是一次运行**
    （它不执行任何步骤），排除它是对定义的纠正，不是口径变更。
    """
    # 先在 SQL 侧取「**不含任何 dry-run 步骤**」的运行（= 生产运行），再取最近 N 个。
    # ⚠️ 不能"先取最近 N 个再逐个过滤"：实测最近 N 个常常全是 dry-run（gates 每次调用
    #产生 1 个 dry-run，占 run 数 74.5%），过滤后为空 ⇒ SLI 直接失效（比虚高更糟）。
    prod = [rid for (rid,) in _rows(
        conn,
        "select r.run_id from run_log r where r.run_id not in ("
        "  select run_id from run_step where note like ?) "
        "order by r.started_at desc limit ?", (_DRY_RUN_NOTE, window))]
    dry_runs = _rows(
        conn,
        "select count(distinct run_id) from run_step where note like ?", (_DRY_RUN_NOTE,))
    n_dry = int(dry_runs[0][0]) if dry_runs else 0
    if not prod:
        return None
    ok = 0
    for run_id in prod:
        bad = _rows(conn, "select count(*) from run_step where run_id=? and status not in "
                          "('ok','skip','degraded')", (run_id,))
        if (bad[0][0] if bad else 0) == 0:
            ok += 1
    out: dict = {"value": round(100.0 * ok / len(prod), 2), "n": len(prod), "ok": ok}
    if n_dry:
        out["excluded_dry_run_total"] = n_dry
        out["note"] = f"全库 {n_dry} 次 dry-run 已排除（不计入运行；dry-run 不执行任何步骤）"
    return out


def sli_freshness(conn, window: int) -> dict | None:
    """数据新鲜度：关键族水位 age ≤ 族阈值的占比。"""
    rows = _rows(conn, "select artifact_key, produced_at from watermark")
    if not rows:
        return None
    now = datetime.now(TZ8)
    ok = tot = 0
    for key, ts in rows:
        fam = str(key).split(":", 1)[0]
        limit = FRESH_DAYS.get(fam)
        if limit is None or not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts))
            if t.tzinfo is None:
                t = t.replace(tzinfo=TZ8)
        except ValueError:
            continue
        tot += 1
        if (now - t).total_seconds() / 86400.0 <= limit:
            ok += 1
    if not tot:
        return None
    return {"value": round(100.0 * ok / tot, 2), "n": tot, "ok": ok}


def sli_gates(conn, window: int) -> dict | None:
    """门禁通过率：最近 N 轮（全部门禁跑完记一轮）全部通过的比例。"""
    rows = _rows(conn, "select run_id, sum(case when passed=0 then 1 else 0 end) "
                      "from gate_result group by run_id order by run_id desc limit ?",
                 (window,))
    if not rows:
        return None
    ok = sum(1 for _, bad in rows if not bad)
    return {"value": round(100.0 * ok / len(rows), 2), "n": len(rows), "ok": ok}


def _history_append(rec: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _recent_streak(name: str) -> int:
    """从历史里取该 SLI **连续低于 SLO** 的周期数（含本周期）。"""
    streak = 0
    if not os.path.exists(HISTORY):
        return streak
    rows = []
    with open(HISTORY, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    for rec in reversed(rows):
        m = (rec.get("sli") or {}).get(name) or {}
        slo = (rec.get("targets") or {}).get(name)
        v = m.get("value")
        if slo is None or v is None or v >= slo:
            break
        streak += 1
    return streak


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SLI/SLO 判定与错误预算（方案 §13.3）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="不打印明细")
    a = ap.parse_args(argv)

    import sqlite3

    from std_lib.common_lib import governance_store as gs

    cfg = _cfg()
    conn = sqlite3.connect(gs.db_path())
    try:
        out: dict = {"schema_version": "1.0",
                     "checked_at": datetime.now(TZ8).replace(microsecond=0).isoformat(),
                     "sli": {}, "targets": {}, "breaches": [], "unavailable": []}
        for name, fn in (("sli_chain_success_rate", sli_chain),
                         ("sli_data_freshness", sli_freshness),
                         ("sli_gate_pass_rate", sli_gates)):
            spec = cfg.get(name) or {}
            window = int(spec.get("window") or 7)
            slo = float(spec.get("slo") or 0.0) * 100.0
            got = fn(conn, window)
            out["targets"][name] = slo
            if got is None:
                out["unavailable"].append(name)
                continue
            out["sli"][name] = got
            if slo and got["value"] < slo:
                streak = _recent_streak(name) + 1
                if streak >= BREACH_CONSECUTIVE:
                    out["breaches"].append({"sli": name, "value": got["value"],
                                            "slo": slo, "consecutive": streak})
    finally:
        conn.close()

    _history_append({"at": out["checked_at"], "sli": out["sli"], "targets": out["targets"]})

    if out["breaches"]:
        from std_lib.common_lib import notify

        for b in out["breaches"]:
            notify.notify(
                "slo_breach",
                f"SLI 连续 {b['consecutive']} 周期低于 SLO：{b['sli']}",
                b, force=True,
            )

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if out["breaches"] else 0

    if not a.quiet:
        for name in ("sli_chain_success_rate", "sli_data_freshness", "sli_gate_pass_rate"):
            m = out["sli"].get(name)
            tgt = out["targets"].get(name)
            if m is None:
                print(f"  {name:24s} 不可用（无数据）")
                continue
            flag = "OK " if m["value"] >= (tgt or 0) else "LOW"
            print(f"  [{flag}] {name:24s} {m['value']:6.2f}%（SLO {tgt:.0f}%，"
                  f"窗口 n={m['n']}）")
        for b in out["breaches"]:
            print(f"  [BREACH] {b['sli']} {b['value']}% < SLO {b['slo']:.0f}%"
                  f"（连续 {b['consecutive']} 周期）")
        hs = out["sli"].get("sli_chain_success_rate") or {}
        slo = out["targets"].get("sli_chain_success_rate") or 0
        if hs and slo:
            budget = round(max(0.0, 100.0 - slo - (100.0 - hs["value"])), 2)
            print(f"  错误预算余额（链成功率口径）：{budget:.2f} 个百分点"
                  "（耗尽 → 建议冻结非必需变更，由用户裁决）")
    print(f"[slo] 已记录快照 {os.path.relpath(HISTORY, ROOT)}")
    return 1 if out["breaches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
