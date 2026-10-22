# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store.ledger — 运行台账层（W4-c 五拆）。

run_log / run_step / gate_result 的读写；版本指纹（version_of_file 系）同层
（水位登记消费 file_version，属台账语义）。连接与 now_iso 经 `._db`。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime

from std_lib.common_lib.io_atomic import sha256_file

from ._db import _TZ, LOG, _ensure, connect, enabled, now_iso


def version_of_file(path: str, *, short: int = 16) -> str:
    """文件内容版本 = sha256(字节) 前 N 位；文件缺失返回 ""（调用方据此跳过登记）。"""
    if not path or not os.path.exists(path):
        return ""
    return sha256_file(path)[:short]


def file_version(path: str, *, short: int = 16) -> str:
    """`version_of_file` 别名（语义更贴合 watermark.version 语境）。"""
    return version_of_file(path, short=short)


def version_of_files(paths_list) -> str:
    """多文件联合版本（如某源的 csv + jsonl 视为一体）：以各自 sha 拼接入哈希。

    全部文件缺失（或全为空版本）→ 返回 ""，调用方据此跳过登记。
    """
    parts = []
    for p in paths_list:
        v = version_of_file(p)
        if not v:
            continue
        parts.append(f"{os.path.basename(p)}={v}")
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# run_log
# --------------------------------------------------------------------------- #
def current_run_id() -> str:
    """当前 run_id：编排经 `REG_ORCH_RUN_ID` env 下传给子进程（gates/各阶段共用）。"""
    return os.environ.get("REG_ORCH_RUN_ID", "").strip()


def run_start(argv=None, note: str = "") -> str:
    """登记一次运行，返回 run_id（并**同时置入 os.environ** 供子进程继承）。"""
    rid = "RUN-" + datetime.now(_TZ).strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    os.environ["REG_ORCH_RUN_ID"] = rid
    conn = _ensure()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_log(run_id,started_at,argv,ok,note) VALUES(?,?,?,NULL,?)",
            (rid, now_iso(), " ".join(str(a) for a in (argv or [])), note),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def run_finish(run_id: str, ok: bool, note: str = "") -> None:
    if not run_id:
        return
    conn = _ensure()
    try:
        conn.execute(
            "UPDATE run_log SET finished_at=?, ok=?, note=? WHERE run_id=?",
            (now_iso(), 1 if ok else 0, note, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def step_record(
    step: str,
    rc: int,
    *,
    exit_code: str = "",
    elapsed_s: float = 0,
    skipped: bool = False,
    note: str = "",
    run_id: str | None = None,
    started_at: float = 0.0,
    finished_at: float = 0.0,
    is_dry_run: bool = False,
) -> None:
    """登记一个步骤的结果（**幂等 upsert**：同 (run_id, step) 覆盖）。

    由 `tools/run_production_refresh._run` 调用；治理库未启用时静默返回（不中断主链）。

    C3（2026-10-16）：`started_at` / `finished_at` 是步骤**真实时间窗**（epoch 秒）。
    老调用方不传时留 0（与迁移前的老数据同语义）——**不把 `recorded_at` 反推成时间窗**：
    反推值 = 记录时刻 ∓ 耗时，与实测值语义不同，混用会污染根因分析的时间窗判定。
    TD-3（2026-10-16）：`is_dry_run` 显式标记，SLI 依此排除非生产流量（不靠 note 文本）。
    """
    if not enabled() or not step:
        return
    rid = run_id if run_id is not None else current_run_id()
    if not rid:
        return
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO run_step(run_id,step,rc,exit_code,elapsed_s,skipped,note,recorded_at,"
            " started_at,finished_at,is_dry_run)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(run_id,step) DO UPDATE SET rc=excluded.rc,"
            " exit_code=excluded.exit_code, elapsed_s=excluded.elapsed_s,"
            " skipped=excluded.skipped, note=excluded.note, recorded_at=excluded.recorded_at,"
            " started_at=excluded.started_at, finished_at=excluded.finished_at,"
            " is_dry_run=excluded.is_dry_run",
            (rid, step, int(rc), exit_code, float(elapsed_s), 1 if skipped else 0, note,
             now_iso(), float(started_at or 0.0), float(finished_at or 0.0),
             1 if is_dry_run else 0),
        )
        conn.commit()
    except sqlite3.Error as e:
        LOG.warning(f"[run_step] WARN 登记失败（不影响主链）: {type(e).__name__}: {e}")
    finally:
        conn.close()


def steps_of(run_id: str) -> list[dict]:
    """某次运行的步骤表（按 step 排序）。表未建（旧库未迁移）→ 空列表（读侧容错）。"""
    if not enabled() or not run_id:
        return []
    try:
        with connect(readonly=True) as c:
            return [
                dict(r)
                for r in c.execute("SELECT * FROM run_step WHERE run_id=? ORDER BY step", (run_id,))
            ]
    except sqlite3.Error as e:
        # 旧库（_SCHEMA_INT < 5）未迁移 → 读侧不得因此失败（写侧 init_db 会补建表）
        LOG.warning(f"[run_step] WARN 步骤表不可读（跑 `cli.py governance init` 迁移）: {e}")
        return []


def last_run_with_steps(*, include_dry_run: bool = False) -> tuple[str, list[dict]]:
    """最近一次**有步骤记录**的运行 → (run_id, steps)。无则 ("", [])。

    2026-10-22（第一百零九批）：默认**跳过 dry-run**。
    起因：一次 `cli.py run … --dry-run` 会把每条 `run_step` 记成 `SKIP`，
    而本函数取"最近 run_id"不分真假 ⇒ 确认台 P1 的 48 个节点**全部显示 skip**
    （实测踩到），读者会误判"链一直没跑"。dry-run 是"只列 argv 不执行"，
    它的状态没有任何执行语义，**不该冒充最近一次运行**。
    真需要看时传 `include_dry_run=True`。
    """
    if not enabled():
        return "", []
    try:
        with connect(readonly=True) as c:
            if include_dry_run:
                row = c.execute(
                    "SELECT run_id FROM run_step GROUP BY run_id "
                    "ORDER BY MAX(recorded_at) DESC LIMIT 1"
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT run_id FROM run_step GROUP BY run_id "
                    "HAVING MAX(COALESCE(is_dry_run, 0)) = 0 "
                    "ORDER BY MAX(recorded_at) DESC LIMIT 1"
                ).fetchone()
            if not row:
                return "", []
            rid = row["run_id"]
    except sqlite3.Error as e:
        LOG.warning(f"[run_step] WARN 步骤表不可读（跑 `cli.py governance init` 迁移）: {e}")
        return "", []
    return rid, steps_of(rid)


def list_runs(limit: int = 20) -> list[dict]:
    if not enabled():
        return []
    with connect(readonly=True) as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM run_log ORDER BY started_at DESC LIMIT ?", (int(limit),)
            )
        ]


# --------------------------------------------------------------------------- #
# watermark（核心）
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# gate_result（门禁结果归档；原物理位置在 projections 区，按职责归位本层）
# --------------------------------------------------------------------------- #
def record_gate_results(run_id: str, results, *, recorded_at: str = "") -> int:
    """归档一次门禁运行的全部结果（同 run_id 幂等覆盖）。results: [{desc,passed,detail}]。"""
    if not run_id or not results:
        return 0
    conn = _ensure()
    n = 0
    try:
        ts = recorded_at or now_iso()
        for r in results:
            conn.execute(
                "INSERT OR REPLACE INTO gate_result(run_id,gate,descr,passed,detail_json,recorded_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    r.get("module") or r.get("gate") or r.get("desc", ""),
                    r.get("desc", ""),
                    1 if r.get("passed") else 0,
                    json.dumps(r.get("detail", {}), ensure_ascii=False, default=str),
                    ts,
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def list_gate_results(run_id: str = "", limit: int = 100) -> list[dict]:
    if not enabled():
        return []
    sql = "SELECT * FROM gate_result"
    args: list = []
    if run_id:
        sql += " WHERE run_id=?"
        args.append(run_id)
    sql += " ORDER BY recorded_at DESC, gate LIMIT ?"
    args.append(int(limit))
    with connect(readonly=True) as c:
        return [dict(r) for r in c.execute(sql, args)]


# --------------------------------------------------------------------------- #
# 阶段 2：元数据四表写入/读取（投影器唯一入口 tools/governance_sync.py 调用）
# --------------------------------------------------------------------------- #
