# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store.worklist — 待办队列（W4-c 五拆）。

v2 §3.14.3 / P1-6：链外节点「决策自动化」缺口的显式化——产生方登记待办，
人工处置（resolve/dismiss）留痕。连接与时间基准经 `._db`。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime

from ._db import _TZ, LOG, _ensure, connect, enabled, now_iso
from .ledger import current_run_id


# --------------------------------------------------------------------------- #
# worklist（待办队列，v2 §3.14.3 / P1-6）
# --------------------------------------------------------------------------- #
def _wl_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["payload"] = json.loads(d.get("payload_json") or "{}")
    except ValueError:
        d["payload"] = {}
    return d


def worklist_add(
    kind: str,
    subject: str,
    *,
    stage: str = "",
    artifact_key: str = "",
    payload: dict | None = None,
    suggestion: str = "",
    confidence: float | None = None,
    round_id: str | None = None,
    item_id: str = "",
) -> str | None:
    """登记一条待办（**幂等**：同 `(kind, subject)` 的 open 项已存在则更新上下文并复用 id）。

    kind      : 必须 ∈ `config.enums.WORKLIST_KIND`。本模块做**软校验**——不在集合内仅打印
                告警仍写入（保持治理库与 config 解耦）；硬断言由 `gate_config_integrity` 承担。
    subject   : 目标标识（RFN / IPN / dedup_key / 冲突键 / 文件路径）——与 kind 共同构成 open 唯一键。
    payload   : 决策所需上下文（建议 / 票数 / 置信度 / 冲突双方 / change_trace…）。
    suggestion: 机器建议（可为空）；confidence: 建议置信度。

    返回 item_id；治理库未启用或写入异常时返回 None（**调用方不得因此中断**）。
    """
    if not kind or not subject:
        return None
    try:
        from config.enums import WORKLIST_KIND

        if kind not in WORKLIST_KIND:
            LOG.warning(f"[worklist] WARN 未登记的 kind={kind!r}（应加入 config.enums.WORKLIST_KIND）")
    except Exception:  # noqa: BLE001  非源码树/未注入 sys.path：跳过软校验
        pass
    if not enabled():
        return None

    rid = round_id if round_id is not None else current_run_id()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    conn = _ensure()
    try:
        existing = conn.execute(
            "SELECT item_id FROM worklist WHERE kind=? AND subject=? AND status='open'",
            (kind, subject),
        ).fetchone()
        if existing:
            # 幂等刷新：上下文可能随数据变化（票数/冲突双方），但保留 created_at 与 item_id
            conn.execute(
                "UPDATE worklist SET stage=?, artifact_key=?, round=?, payload_json=?,"
                " suggestion=?, confidence=? WHERE item_id=?",
                (
                    stage,
                    artifact_key,
                    rid,
                    payload_json,
                    suggestion,
                    confidence,
                    existing["item_id"],
                ),
            )
            conn.commit()
            return existing["item_id"]

        # item_id 需在"同一秒内处置后重新登记"时仍唯一 → 带毫秒 + 冲突自增后缀。
        # （曾被单测检出：秒级时间戳 + 同 pid + 同 kind|subject 哈希 → 重开时主键冲突）
        base = (
            "WL-"
            + datetime.now(_TZ).strftime("%Y%m%d%H%M%S%f")[:-3]
            + f"-{os.getpid()}-"
            + hashlib.sha256(f"{kind}|{subject}".encode()).hexdigest()[:6]
        )
        iid = item_id or base
        _n = 1
        while conn.execute("SELECT 1 FROM worklist WHERE item_id=?", (iid,)).fetchone():
            _n += 1
            iid = f"{base}-{_n}"
        conn.execute(
            "INSERT INTO worklist(item_id,kind,stage,artifact_key,round,subject,payload_json,"
            "suggestion,confidence,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?, 'open', ?)",
            (
                iid,
                kind,
                stage,
                artifact_key,
                rid,
                subject,
                payload_json,
                suggestion,
                confidence,
                now_iso(),
            ),
        )
        conn.commit()
        return iid
    except sqlite3.Error as e:
        LOG.warning(f"[worklist] WARN 登记失败（不影响主链）: {type(e).__name__}: {e}")
        return None
    finally:
        conn.close()


def worklist_list(*, status: str = "open", kind: str = "", limit: int = 0) -> list[dict]:
    """列出待办（默认仅 open）；`status=""` 表示全部状态。"""
    if not enabled():
        return []
    sql = "SELECT * FROM worklist"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at, kind, subject"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with connect(readonly=True) as c:
        return [_wl_row(r) for r in c.execute(sql, args)]


def worklist_resolve(
    item_id: str, resolution: str, *, by: str = "", status: str = "resolved"
) -> bool:
    """处置一条待办（`resolved` = 已裁决；`dismissed` = 判为无需处置）。

    返回是否命中并更新（未命中返回 False，调用方据此提示）。
    """
    if not enabled() or not item_id:
        return False
    if status not in ("resolved", "dismissed"):
        raise ValueError(f"非法 status={status!r}（仅 resolved / dismissed）")
    conn = _ensure()
    try:
        cur = conn.execute(
            "UPDATE worklist SET status=?, resolved_by=?, resolved_at=?, resolution=?"
            " WHERE item_id=? AND status='open'",
            (status, by or current_run_id() or "cli", now_iso(), resolution, item_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def worklist_stats() -> dict:
    """待办统计（供 `cli.py status` / 告警联动）：按 kind 计数 + 最老 open 滞留天数。"""
    if not enabled():
        return {"enabled": False, "open": 0, "by_kind": {}}
    rows = worklist_list(status="open")
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    oldest_days = 0
    if rows:
        try:
            first = min(r["created_at"] for r in rows)
            t0 = datetime.strptime(first[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_TZ)
            oldest_days = max(0, (datetime.now(_TZ) - t0).days)
        except (ValueError, TypeError):
            oldest_days = 0
    return {
        "enabled": True,
        "open": len(rows),
        "by_kind": dict(sorted(by_kind.items())),
        "oldest_open_days": oldest_days,
        "all": {s: len(worklist_list(status=s)) for s in ("resolved", "dismissed")},
    }


# --------------------------------------------------------------------------- #
# audit_log（append-only）
# --------------------------------------------------------------------------- #
