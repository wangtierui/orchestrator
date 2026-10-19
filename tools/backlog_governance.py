# -*- coding: utf-8 -*-
"""tools.backlog_governance — **诊断第 3 波：待办积压治理回路**（2026-10-19）

背景（五阶段审查的用途基线）
----------------------------
项目是「**低故障率 + 高积压**」：实测 open 641 条、处置仅 3 条，
而产生速率约 330 条/月（2026-09 +407、2026-10 +251）
⇒ **净增长**，任何不持续的治理都会被增量淹没。

第 1 波（`gate_triage`）解"门禁为何红"，第 2 波（`step_triage`）解"步骤为何败"，
都面向**低频故障**；本波面向**高频积压**——它的核心不是"判得更准"，
而是"**让回路持续转动**"：监控 → 判定 → 处置 → 度量。

三个子命令
--------
1. `growth`    积压增长监控（只读）：按 kind/月的产生与处置、净增长、处置率。
               可被调度调用，**超阈值时给出告警载荷**（`--json` 供 notify 用）。
2. `selfclosed` 机器判定「自证闭环」项（只读，先看再执行）。
3. `apply`     受控执行（写）：默认 dry-run，须 `--execute --by --reason`。

机器判定的边界（关键设计）
--------------------------
**只判"自证闭环"这一类**，且判据是**保守的两条件同时成立**：
  ① subject 以闭环词开头（`已修复`/`已处置`/`已补齐`/`已修`…）；
  ② payload **没有** `待裁决` 键（还有待决事项 ⇒ 未闭环）。
两者缺一不可 ⇒ **宁可漏判，不可错判**。漏判的代价是留在堆里；
错判的代价是把真待办关掉、且审计显示"已处置"——不可逆。

明确**不**做：不判主题归属（`rfn_theme_uncertain`，标题不在归属表内）、
不判相关性边界（需业务口径）、不判"建议"类（payload 只有 `建议` 键、
无闭环证据）。这些属业务判断，由 `worklist_triage bundle` 出裁决包人工处理。

写入口径
--------
一律走 `governance_store.worklist_resolve()`（表级唯一写口，C1 已在其内落审计）
⇒ CLI/确认台/MCP 全通道同口径，逐条落 `audit_log`。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode
from std_lib.common_lib.governance_store import worklist_resolve

#: subject 的"闭环自证"词（**句中匹配**——实测真实 subject 的宣告不在开头：
#: `N-208 已修复并完成历史数据补救` / `写入侧「正文载体混入 attachments」已修复`）
_CLOSED_WORDS = ("已修复", "已处置", "已补齐", "已修", "已落地", "已解除", "已完成补救")

#: 否定语境护栏：subject 含这些词 ⇒ **不判闭环**（实测真实存在此类项）
_NEGATIVE_HINTS = ("部分完成", "未执行", "未改码", "改法已定", "待执行", "未清",
                   "残余", "待产品决策")

#: payload 里出现即表示"仍有待决事项"的键（出现则**不**判闭环）
_PENDING_KEYS = ("待裁决", "待确认", "待人工", "待转交", "待产品决策", "建议")

#: 积压告警阈值（可由 `--warn-open` 覆盖）
DEFAULT_WARN_OPEN = 500

#: 净增长告警阈值（条/月）
DEFAULT_WARN_NET_PER_MONTH = 100


def _connect() -> sqlite3.Connection:
    """连治理库（只读）。

    ⚠️ 路径解析与 `governance_store._db.db_path()` **同源**：
    优先 `REG_ORCH_GOVERNANCE_DB` 环境变量，否则 `paths.DATA_DIR/governance.db`。
    两处若各写一套，测试注入 env 时只会对一半生效（本工具曾因此连到真库）。
    """
    db = os.environ.get("REG_ORCH_GOVERNANCE_DB") or os.path.join(
        paths.DATA_DIR, "governance.db")
    return sqlite3.connect("file:" + db + "?mode=ro", uri=True)


def _one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


# --------------------------------------------------------------------------- #
# growth
# --------------------------------------------------------------------------- #
def collect_growth(conn: sqlite3.Connection) -> dict:
    """采集积压增长指标（只读）。"""
    out: dict = {
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "open": _one(conn, "SELECT COUNT(*) FROM worklist WHERE status='open'"),
        "resolved": _one(conn, "SELECT COUNT(*) FROM worklist WHERE status='resolved'"),
        "dismissed": _one(conn, "SELECT COUNT(*) FROM worklist WHERE status='dismissed'"),
    }
    total = out["open"] + out["resolved"] + out["dismissed"]
    out["total"] = total
    handled = out["resolved"] + out["dismissed"]
    out["disposal_rate_pct"] = round(100.0 * handled / total, 2) if total else 0.0

    # 按 kind 的 open 分布
    out["open_by_kind"] = {
        k: n for k, n in conn.execute(
            "SELECT kind, COUNT(*) FROM worklist WHERE status='open' "
            "GROUP BY kind ORDER BY 2 DESC")
    }
    # 按月产生
    out["created_by_month"] = {
        m: n for m, n in conn.execute(
            "SELECT substr(created_at,1,7), COUNT(*) FROM worklist "
            "GROUP BY 1 ORDER BY 1")
    }
    # 按月处置（C1 落地后 audit_log 才有人工处置记录）
    out["resolved_by_month"] = {
        m: n for m, n in conn.execute(
            "SELECT substr(ts,1,7), COUNT(*) FROM audit_log "
            "WHERE action IN ('worklist_resolved','worklist_dismissed') "
            "GROUP BY 1 ORDER BY 1")
    }
    # 近 30 天净增长
    # ⚠️ cutoff 的字符串格式必须与 `created_at`/`ts` **逐字同构**：
    #    实测二者是 `2026-09-26T12:28:06+0800`（时区偏移**无冒号**），
    #    而 `datetime.isoformat()` 产出 `+08:00`（**有冒号**）⇒ 字符串比较时
    #    `+0800` < `+08:00` ⇒ **所有记录都 >= cutoff**（曾实测"近30天=全部658条"）。
    cutoff = (datetime.now(UTC) - timedelta(days=30)).astimezone()
    cutoff_s = cutoff.strftime("%Y-%m-%dT%H:%M:%S%z")
    c30 = _one(conn, "SELECT COUNT(*) FROM worklist WHERE created_at >= ?", (cutoff_s,))
    r30 = _one(conn, "SELECT COUNT(*) FROM audit_log WHERE ts >= ? "
                     "AND action IN ('worklist_resolved','worklist_dismissed')",
               (cutoff_s,))
    out["last_30d"] = {"created": c30, "resolved": r30, "net": c30 - r30,
                       "cutoff": cutoff_s}
    out["last_activity"] = {
        "newest_worklist": conn.execute(
            "SELECT MAX(created_at) FROM worklist").fetchone()[0],
        "newest_resolution": conn.execute(
            "SELECT MAX(ts) FROM audit_log WHERE action IN "
            "('worklist_resolved','worklist_dismissed')").fetchone()[0],
    }
    # **数据跨度披露**（防误判）：实测本仓待办历史仅 23 天（最早 2026-09-26），
    # 故"近 30 天"天然等于全部记录。不披露的话，后人会把 658 当成统计错误。
    lo, hi = conn.execute("SELECT MIN(created_at), MAX(created_at) "
                          "FROM worklist").fetchone()
    out["history_span"] = {"first": lo, "last": hi}
    return out


def assess_growth(g: dict, warn_open: int, warn_net: int) -> dict:
    """据阈值判定是否该告警（口径显式，便于复现）。"""
    alerts: list[dict] = []
    if g["open"] >= warn_open:
        alerts.append({
            "kind": "open_backlog",
            "detail": f"open 待办 {g['open']} 条 ≥ 阈值 {warn_open}",
            "evidence": g["open_by_kind"],
        })
    if g["last_30d"]["net"] >= warn_net:
        alerts.append({
            "kind": "net_growth",
            "detail": f"近 30 天净增 {g['last_30d']['net']} 条 "
                      f"（产生 {g['last_30d']['created']} / 处置 {g['last_30d']['resolved']}）"
                      f" ≥ 阈值 {warn_net}",
            "evidence": g["last_30d"],
        })
    return {"should_alert": bool(alerts), "alerts": alerts,
            "thresholds": {"warn_open": warn_open, "warn_net_per_month": warn_net}}


# --------------------------------------------------------------------------- #
# selfclosed
# --------------------------------------------------------------------------- #
def _closed_reason(subject: str, payload: dict) -> str:
    """判定一条是否「自证闭环」；返回理由（空串=不判）。

    **保守三条件 + 一护栏**（实测真实数据后定形，见模块 docstring）：
      ① subject **含**闭环词（句中匹配，非开头——实测宣告多在句中）；
      ② subject **不含**否定语境（部分完成/未执行/未改码/改法已定/残余…）；
      ③ payload **无**待决键（待裁决/建议/待转交…——有即未闭环）。
    三者全满足才判。宁可漏判（留在堆里），不可错判（不可逆）。
    """
    s = (subject or "").strip()
    hit = next((w for w in _CLOSED_WORDS if w in s), "")
    if not hit:
        return ""
    neg = next((n for n in _NEGATIVE_HINTS if n in s), "")
    if neg:
        return ""
    for k in _PENDING_KEYS:
        if k in payload:
            return ""
    return ("subject 含闭环词「%s」、无否定语境、payload 无待决键（%s）"
            % (hit, "/".join(_PENDING_KEYS)))


def collect_selfclosed(conn: sqlite3.Connection) -> list[dict]:
    """扫 open 里「自证闭环」的项（只读）。"""
    out: list[dict] = []
    for iid, kind, subj, pj in conn.execute(
            "SELECT item_id, kind, subject, payload_json FROM worklist "
            "WHERE status='open' ORDER BY created_at"):
        try:
            d = json.loads(pj or "{}")
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        r = _closed_reason(subj, d)
        if r:
            out.append({"item_id": iid, "kind": kind, "subject": subj,
                        "reason": r, "created_at": None})
    # 补 created_at（上面没取）
    if out:
        ids = [x["item_id"] for x in out]
        q = ",".join("?" * len(ids))
        for iid, at in conn.execute(
                f"SELECT item_id, created_at FROM worklist WHERE item_id IN ({q})", ids):
            for x in out:
                if x["item_id"] == iid:
                    x["created_at"] = at
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_growth(warn_open: int, warn_net: int, as_json: bool) -> int:
    conn = _connect()
    try:
        g = collect_growth(conn)
    finally:
        conn.close()
    verdict = assess_growth(g, warn_open, warn_net)
    payload = {"metrics": g, "verdict": verdict}

    # 告警产生方（2026-10-19，T2）：`schedule.yaml` 的 `backlog_growth` 作业
    # 每日跑本命令 ⇒ 超阈值即送达（`notify.on` 与 `NOTIFY_EVENTS` 已同步登记）。
    # **放在 `--json` 分叉之前**：调度用 `--json` 时也必须发告警，否则形同没有。
    # 告警失败**不改**返回码（旁路留痕，不能让"没送达"伪装成"没超阈值"）。
    if verdict["should_alert"]:
        try:
            from std_lib.common_lib import notify as _notify

            _notify.notify(
                "backlog_growth",
                "待办积压触发阈值：open=%d，近 30 天净增 %d"
                % (g["open"], g["last_30d"]["net"]),
                {"open": g["open"],
                 "disposal_rate_pct": g["disposal_rate_pct"],
                 "last_30d": g["last_30d"],
                 "open_by_kind": g["open_by_kind"],
                 "alerts": verdict["alerts"],
                 "thresholds": verdict["thresholds"]})
        except Exception as e:  # noqa: BLE001
            print(f"[backlog] WARN 告警发送失败（不影响返回码）: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return ExitCode.FAIL if verdict["should_alert"] else ExitCode.OK
    print("[backlog] open=%d  total=%d  处置率=%.2f%%"
          % (g["open"], g["total"], g["disposal_rate_pct"]))
    print("  近 30 天：产生 %d / 处置 %d / 净增 %d"
          % (g["last_30d"]["created"], g["last_30d"]["resolved"], g["last_30d"]["net"]))
    print("  open 按 kind：")
    for k, n in list(g["open_by_kind"].items())[:8]:
        print(f"    {k:28s} {n}")
    print("  按月产生：%s" % g["created_by_month"])
    print("  按月处置：%s" % (g["resolved_by_month"] or "（无记录）"))
    hs = g.get("history_span") or {}
    if hs.get("first"):
        print("  数据跨度：%s → %s（**近 30 天天然≈全部记录**，非统计错误）"
              % (hs["first"][:10], (hs.get("last") or "")[:10]))
    print("  最后活动：新待办 %s / 最后处置 %s"
          % (g["last_activity"]["newest_worklist"] or "（无）",
             g["last_activity"]["newest_resolution"] or "（无）"))
    # 告警已在 `--json` 分叉之前统一发出（见上）⇒ 这里只做人类可读输出。
    if verdict["should_alert"]:
        print("\n  ⚠ 触发告警（已发 notify：backlog_growth）：")
        for a in verdict["alerts"]:
            print(f"    · [{a['kind']}] {a['detail']}")
        return ExitCode.FAIL
    print("\n  OK 未触发告警阈值")
    return ExitCode.OK


def cmd_selfclosed(as_json: bool) -> int:
    conn = _connect()
    try:
        items = collect_selfclosed(conn)
    finally:
        conn.close()
    if as_json:
        print(json.dumps({"count": len(items), "items": items},
                         ensure_ascii=False, indent=2))
        return ExitCode.OK
    print(f"[backlog] 自证闭环可机器判定：{len(items)} 条")
    for x in items:
        print(f"  - {x['item_id']}  {x['kind']}")
        print(f"      {x['subject'][:70]}")
        print(f"      依据：{x['reason']}")
    if not items:
        print("  （无）")
    return ExitCode.OK


def cmd_decide(kind: str, keep_subject: str, execute: bool, by: str,
               reason: str, status: str = "dismissed") -> int:
    """按 kind **批量裁决**（保留项不动）。

    用途：业务方给出「某 kind 整体不纳入」这类**明确决策**后，一次性处置，
    但不许碰到「决策里点名保留」的项（`keep_subject` 子串匹配）。

    ⚠️ **不猜业务事实**：保留项需要登记进归属表时，须先指定**主题**才算完；
    本命令只把它留在 open，不代填主题。
    """
    con = _connect()
    try:
        rows = con.execute(
            "SELECT item_id, subject FROM worklist WHERE status='open' AND kind=? "
            "ORDER BY created_at", (kind,)).fetchall()
    finally:
        con.close()
    keep = [r for r in rows if keep_subject and keep_subject in (r[1] or "")]
    todo = [r for r in rows if not (keep_subject and keep_subject in (r[1] or ""))]
    if not execute:
        print(f"[backlog] dry-run：kind={kind} 将 {status} {len(todo)} 条，"
              f"保留 {len(keep)} 条（加 --execute --by --reason 才执行）")
        for iid, subj in todo[:8]:
            print(f"  - {iid}  {subj[:62]}")
        if len(todo) > 8:
            print(f"  … 共 {len(todo)} 条")
        if keep:
            print("  保留（不动）：")
            for iid, subj in keep:
                print(f"  * {iid}  {subj[:62]}")
        return ExitCode.OK
    if not by or not reason:
        print("[backlog] 拒绝执行：--by 与 --reason 必填（审计要求，C1）",
              file=sys.stderr)
        return ExitCode.USAGE
    ok = fail = 0
    for iid, _ in todo:
        try:
            if worklist_resolve(iid, reason, by=by, status=status):
                ok += 1
            else:
                fail += 1
        except Exception as e:  # noqa: BLE001  单条失败不阻断其余
            fail += 1
            print(f"  [err] {iid}: {type(e).__name__}: {e}")
    print(f"[backlog] 执行完毕：{status} {ok} 条 / 未命中 {fail} 条 / "
          f"保留 {len(keep)} 条（by={by}）")
    return ExitCode.OK if fail == 0 else ExitCode.FAIL


def cmd_apply(execute: bool, by: str, reason: str) -> int:
    if not execute:
        items = collect_selfclosed(_connect())
        print(f"[backlog] dry-run：将 dismiss {len(items)} 条自证闭环项"
              "（加 --execute --by --reason 才执行）")
        for x in items:
            print(f"  - {x['item_id']}  {x['subject'][:60]}")
        return ExitCode.OK
    if not by or not reason:
        print("[backlog] 拒绝执行：--by 与 --reason 必填（审计要求，C1）",
              file=sys.stderr)
        return ExitCode.FAIL
    conn = _connect()
    try:
        items = collect_selfclosed(conn)
    finally:
        conn.close()
    ok = fail = 0
    for x in items:
        try:
            if worklist_resolve(x["item_id"], reason, by=by, status="dismissed"):
                ok += 1
            else:
                fail += 1
                print(f"  [miss] {x['item_id']}（状态已变或不存在）")
        except Exception as e:  # noqa: BLE001  单条失败不阻断其余
            fail += 1
            print(f"  [err] {x['item_id']}: {type(e).__name__}: {e}")
    print(f"[backlog] 执行完毕：dismissed {ok} 条 / 未命中 {fail} 条"
          f"（by={by}）")
    return ExitCode.OK if fail == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="待办积压治理回路（诊断第 3 波）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_g = sub.add_parser("growth", help="积压增长监控（只读；超阈值返回码 2）")
    p_g.add_argument("--warn-open", type=int, default=DEFAULT_WARN_OPEN)
    p_g.add_argument("--warn-net", type=int, default=DEFAULT_WARN_NET_PER_MONTH,
                     help="近 30 天净增阈值")
    p_g.add_argument("--json", action="store_true")

    p_s = sub.add_parser("selfclosed", help="机器判定自证闭环项（只读）")
    p_s.add_argument("--json", action="store_true")

    p_a = sub.add_parser("apply", help="受控执行（默认 dry-run）")
    p_a.add_argument("--execute", action="store_true")
    p_a.add_argument("--by", default="")
    p_a.add_argument("--reason", default="")

    p_d = sub.add_parser("decide", help="按 kind 批量裁决（保留项不动；默认 dry-run）")
    p_d.add_argument("--kind", required=True)
    p_d.add_argument("--keep-subject", default="",
                     help="含该子串的条目**不动**（业务决策点名保留的项）")
    p_d.add_argument("--status", default="dismissed",
                     choices=("dismissed", "resolved"))
    p_d.add_argument("--execute", action="store_true")
    p_d.add_argument("--by", default="")
    p_d.add_argument("--reason", default="")

    a = ap.parse_args(argv)
    if a.cmd == "growth":
        return cmd_growth(a.warn_open, a.warn_net, a.json)
    if a.cmd == "selfclosed":
        return cmd_selfclosed(a.json)
    if a.cmd == "decide":
        return cmd_decide(a.kind, a.keep_subject, a.execute, a.by,
                          a.reason, a.status)
    return cmd_apply(a.execute, a.by, a.reason)


if __name__ == "__main__":
    raise SystemExit(main())
