# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store.watermark — 产物水位与依赖边（W4-c 五拆）。

「产物 ← 依赖」的机器可读声明（gate_watermark 判据的事实源）。
连接/路径/时间基准经 `._db`；版本指纹经 `.ledger`。
"""

from __future__ import annotations

import json
import sqlite3

from ._db import LOG, SCHEMA_VERSION, _ensure, connect, db_path, enabled, now_iso
from .ledger import current_run_id


def record_watermark(
    artifact_key: str,
    produced_by: str,
    version: str,
    *,
    inputs: dict | None = None,
    produced_at: str = "",
    record_count: int | None = None,
    schema_version: str = SCHEMA_VERSION,
    recorded_by: str = "",
    run_id: str | None = None,
    round_id: str | None = None,
    stage: str = "",
) -> dict | None:
    """登记/更新产物水位（幂等 upsert）。

    inputs: {依赖 artifact_key: 该依赖**登记时**的 version} —— 即"我是在哪个上游版本上算出来的"。
            调用方只应登记**当前实际可得**的依赖版本；取不到（如上游未登记）则不要放进 inputs，
            否则会造出"永远对不上"的假边（门禁会判陈旧）。
    round_id: 轮次标识（列名 `round`，取 `run_id`）；同值即"同一轮"，供
            `dependency_edges` 的 `ok_within_round` 判据使用（v2 §3.3.1）。
    stage:    产生该产物的阶段 id（如 `"2.6"`）；仅审计与排障用，不参与判据。

    **校验收紧（v2 §3.3.1，2026-09-26）**：`produced_by` 为空即**拒绝写入**并告警
    （原实现允许任意字符串入库，导致"谁产的"不可追溯）；此时返回 None。
    返回登记后的行（dict）；`version` 为空（产物缺失）时**不写**并返回 None。
    """
    if not version:
        return None
    if not str(produced_by or "").strip():
        LOG.warning(
            f"[governance] WARN 拒绝登记 {artifact_key}：produced_by 为空"
            "（水位必须可追溯到唯一产出方）"
        )
        return None
    row = {
        "artifact_key": artifact_key,
        "produced_by": produced_by,
        "recorded_by": recorded_by or produced_by,
        "produced_at": produced_at or now_iso(),
        "schema_version": schema_version,
        "version": version,
        "inputs_json": json.dumps(inputs or {}, ensure_ascii=False, sort_keys=True),
        "record_count": record_count,
        "run_id": run_id if run_id is not None else current_run_id(),
        "round": round_id if round_id is not None else current_run_id(),
        "stage": stage,
    }
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO watermark(artifact_key,produced_by,recorded_by,produced_at,"
            "schema_version,version,inputs_json,record_count,run_id,round,stage)"
            " VALUES(:artifact_key,:produced_by,:recorded_by,:produced_at,"
            ":schema_version,:version,:inputs_json,:record_count,:run_id,:round,:stage)"
            " ON CONFLICT(artifact_key) DO UPDATE SET"
            " produced_by=excluded.produced_by, recorded_by=excluded.recorded_by,"
            " produced_at=excluded.produced_at, schema_version=excluded.schema_version,"
            " version=excluded.version, inputs_json=excluded.inputs_json,"
            " record_count=excluded.record_count, run_id=excluded.run_id,"
            " round=excluded.round, stage=excluded.stage",
            row,
        )
        conn.commit()
    finally:
        conn.close()
    return row


def get_watermark(artifact_key: str) -> dict | None:
    if not enabled():
        return None
    with connect(readonly=True) as c:
        r = c.execute("SELECT * FROM watermark WHERE artifact_key=?", (artifact_key,)).fetchone()
        return _wm_row(r) if r else None


def list_watermarks() -> list[dict]:
    if not enabled():
        return []
    with connect(readonly=True) as c:
        return [_wm_row(r) for r in c.execute("SELECT * FROM watermark ORDER BY artifact_key")]


def _wm_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["inputs"] = json.loads(d.get("inputs_json") or "{}")
    except ValueError:
        d["inputs"] = {}
    return d


def dependency_edges() -> list[dict]:
    """把 watermark 表展开为有向边列表（`reports/…方案_20260917.md` §1.4 的机器可读形态）。

    每行：{artifact, produced_by, version, round, dep, declared_version, current_version,
           dep_round, status}
    **四态判据**（v2 §3.3.1，2026-09-26 增第 4 态）：
      - `ok`              ：声明版本 == 当前版本；
      - `ok_within_round` ：版本不一致，但上游与本产物**同一轮**（`round` 相同且非空）
                            —— 链内"先算后用 + 同轮内上游被再次推进"属正常顺序特性
                            （典型：`clauses:{src}` 在阶段 1 登记，`cleaned:{src}` 在
                            阶段 2 时效回写后才重新登记），不再误判为陈旧；
      - `stale`           ：上游在**更晚轮次**推进，或双方轮次不可比 → 阻断；
      - `unregistered`    ：上游未登记水位 → 披露不阻断。
    """
    wms = {w["artifact_key"]: w for w in list_watermarks()}
    edges: list[dict] = []
    for key, w in sorted(wms.items()):
        ins = w.get("inputs") or {}
        if not ins:
            continue
        a_round = str(w.get("round") or "")
        for dep, declared in sorted(ins.items()):
            dep_w = wms.get(dep) or {}
            cur = dep_w.get("version", "")
            dep_round = str(dep_w.get("round") or "")
            if not cur:
                status = "unregistered"
            elif str(cur) == str(declared):
                status = "ok"
            elif a_round and dep_round and a_round == dep_round:
                status = "ok_within_round"
            else:
                status = "stale"
            edges.append(
                {
                    "artifact": key,
                    "produced_by": w.get("produced_by", ""),
                    "version": w.get("version", ""),
                    "round": a_round,
                    "dep": dep,
                    "declared_version": declared,
                    "current_version": cur,
                    "dep_round": dep_round,
                    "status": status,
                }
            )
    return edges


def check_dependencies() -> tuple[bool, dict]:
    """一致性判据（供 gate_watermark 消费）：任一边 stale 即 False。

    - `stale` → 阻断（上游已推进、产物未重跑 —— 正是 mtime 判据想抓却抓不准的情形）
    - `ok_within_round` → **不阻断**（同轮顺序特性；计数在 detail 披露）
    - `unregistered` / 无 inputs → 不阻断（过渡期覆盖度不足属预期，只在 detail 披露）
    """
    if not enabled():
        return True, {"enabled": False, "note": "治理库未启用（阶段 1 未运行），本判据跳过"}
    edges = dependency_edges()
    stale = [e for e in edges if e["status"] == "stale"]
    within = [e for e in edges if e["status"] == "ok_within_round"]
    unreg = sorted({e["dep"] for e in edges if e["status"] == "unregistered"})
    wms = list_watermarks()
    bad_rows = [
        w["artifact_key"]
        for w in wms
        if not str(w.get("produced_at") or "").strip() or not str(w.get("version") or "").strip()
    ]
    no_round = [w["artifact_key"] for w in wms if not str(w.get("round") or "").strip()]
    problems = []
    if stale:
        problems.append(
            "水位陈旧（上游已推进、产物未重跑）："
            + "; ".join(
                f"{e['artifact']} 声明 {e['dep']}={e['declared_version']} "
                f"但当前 {e['dep']}={e['current_version']}"
                for e in stale[:5]
            )
            + f"（共 {len(stale)} 条）"
        )
    if bad_rows:
        problems.append(f"水位行缺 produced_at/version：{bad_rows[:5]}")
    detail = {
        "enabled": True,
        "db": db_path(),
        "artifacts": len(wms),
        "edges": len(edges),
        "stale_edges": len(stale),
        "within_round_edges": len(within),
        "unregistered_deps": unreg,
        "problems": problems,
        "note": "判据=已登记产物的每条声明依赖边版本一致；`ok_within_round`（同轮顺序特性）与 "
        "`unregistered`（覆盖度不足）均不阻断，仅在 detail 披露",
    }
    if within:
        detail["within_round_deps"] = sorted({f"{e['artifact']}<-{e['dep']}" for e in within})
    if no_round and wms:
        # 过渡期披露：迁移前登记的历史水位缺 round，无法参与同轮判据（会按 stale 处理）
        detail["rows_without_round"] = len(no_round)
    return (not problems), detail
