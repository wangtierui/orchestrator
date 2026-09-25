# -*- coding: utf-8 -*-
"""tests/test_governance_watermark.py — 水位机制单测（v2 §3.3.1 / §3.11 T2）

覆盖三件事（全部用临时库，不触碰生产 `data/governance.db`）：
  ① 观测表**列级迁移**幂等且不丢历史（`_SCHEMA_INT` 2 → 3 的 round/stage）；
  ② `record_watermark` 的**产出入表校验**（produced_by 空即拒写）；
  ③ `dependency_edges` 的**四态判据**，重点是新增态 `ok_within_round`
     （同轮"先算后用 + 上游同轮推进"不再误判 stale）与跨轮 `stale` 的区分。
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    """隔离的治理库（经 REG_ORCH_GOVERNANCE_DB 覆盖，绝不落到生产库）。"""
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    from std_lib.common_lib import governance_store

    governance_store.init_db()
    return governance_store


def _status(edges, artifact, dep):
    for e in edges:
        if e["artifact"] == artifact and e["dep"] == dep:
            return e["status"]
    return None


# --------------------------------------------------------------------------- #
# ① 列级迁移
# --------------------------------------------------------------------------- #
def test_migration_adds_round_stage_and_keeps_rows(gs):
    """先造"v2 版本"的旧库（无 round/stage），再迁移 → 补列且旧行保留。"""
    db = gs.db_path()
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE watermark")
        c.execute(
            "CREATE TABLE watermark(artifact_key TEXT PRIMARY KEY, produced_by TEXT NOT NULL,"
            " recorded_by TEXT NOT NULL DEFAULT '', produced_at TEXT NOT NULL,"
            " schema_version TEXT NOT NULL, version TEXT NOT NULL,"
            " inputs_json TEXT NOT NULL DEFAULT '{}', record_count INTEGER, run_id TEXT)")
        c.execute("INSERT INTO watermark(artifact_key,produced_by,produced_at,schema_version,"
                  "version) VALUES('legacy','x','2026-01-01T00:00:00+0800','1.1','abc')")
        c.execute("PRAGMA user_version=2")

    gs.init_db()   # 触发迁移

    with sqlite3.connect(db) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(watermark)")]
        assert "round" in cols and "stage" in cols
        assert c.execute("PRAGMA user_version").fetchone()[0] == gs._SCHEMA_INT
        # 观测表**不可 DROP**：历史行必须还在
        assert c.execute("SELECT COUNT(*) FROM watermark WHERE artifact_key='legacy'"
                         ).fetchone()[0] == 1


def test_migration_is_idempotent(gs):
    """重复 init_db 不报错、不改变结构（幂等）。"""
    gs.init_db()
    gs.init_db()
    cols = [r[1] for r in gs.connect(readonly=True).execute("PRAGMA table_info(watermark)")]
    assert cols.count("round") == 1 and cols.count("stage") == 1


# --------------------------------------------------------------------------- #
# ② 登记校验
# --------------------------------------------------------------------------- #
def test_record_rejects_empty_produced_by(gs):
    """produced_by 为空 → 拒写（原实现允许任意字符串入库）。"""
    assert gs.record_watermark("k", "", "v1") is None
    assert gs.get_watermark("k") is None


def test_record_rejects_empty_version(gs):
    assert gs.record_watermark("k", "some.mod", "") is None


def test_record_round_defaults_and_explicit(gs, monkeypatch):
    monkeypatch.setenv("REG_ORCH_RUN_ID", "RUN-20260926-000000-1")
    row = gs.record_watermark("k", "mod.a", "v1")
    assert row is not None and row["round"] == "RUN-20260926-000000-1"

    row2 = gs.record_watermark("k2", "mod.a", "v1", round_id="RUN-OTHER", stage="2.6")
    assert row2["round"] == "RUN-OTHER" and row2["stage"] == "2.6"


# --------------------------------------------------------------------------- #
# ③ 四态判据
# --------------------------------------------------------------------------- #
def test_edges_ok_when_versions_match(gs):
    gs.record_watermark("dep", "mod.dep", "v1", round_id="R1")
    gs.record_watermark("art", "mod.art", "v1", inputs={"dep": "v1"}, round_id="R1")
    assert _status(gs.dependency_edges(), "art", "dep") == "ok"
    assert gs.check_dependencies()[0] is True


def test_edges_ok_within_round_when_same_round(gs):
    """核心新态：同轮内上游被再次推进（版本变了）→ 不判 stale。"""
    gs.record_watermark("dep", "mod.dep", "v1", round_id="R1")
    gs.record_watermark("art", "mod.art", "v1", inputs={"dep": "v1"}, round_id="R1")
    # 同一轮内上游推进（模拟 clauses 之后 cleaned 被时效回写刷新）
    gs.record_watermark("dep", "mod.dep", "v2", round_id="R1")

    edges = gs.dependency_edges()
    assert _status(edges, "art", "dep") == "ok_within_round"
    ok, detail = gs.check_dependencies()
    assert ok is True, "同轮顺序特性不得阻断"
    assert detail["within_round_edges"] == 1 and detail["stale_edges"] == 0


def test_edges_stale_when_dep_advanced_in_later_round(gs):
    """上游在**更晚轮次**推进 → 真陈旧 → 阻断。"""
    gs.record_watermark("dep", "mod.dep", "v1", round_id="R1")
    gs.record_watermark("art", "mod.art", "v1", inputs={"dep": "v1"}, round_id="R1")
    gs.record_watermark("dep", "mod.dep", "v2", round_id="R2")

    assert _status(gs.dependency_edges(), "art", "dep") == "stale"
    ok, detail = gs.check_dependencies()
    assert ok is False and detail["stale_edges"] == 1


def test_edges_stale_when_round_missing(gs):
    """round 缺失（迁移前的历史行）→ 保守判 stale，不放行。

    注意：版本一致时仍是 `ok`（版本判据优先）；本用例构造**版本不一致 + 双方无 round**
    的场景，验证不会因"无法比较轮次"而误判为同轮。
    """
    gs.record_watermark("dep", "mod.dep", "v2", round_id="")
    gs.record_watermark("art", "mod.art", "v1", inputs={"dep": "v1"}, round_id="")

    assert _status(gs.dependency_edges(), "art", "dep") == "stale"


def test_edges_unregistered_not_blocking(gs):
    gs.record_watermark("art", "mod.art", "v1", inputs={"ghost": "v1"}, round_id="R1")
    assert _status(gs.dependency_edges(), "art", "ghost") == "unregistered"
    assert gs.check_dependencies()[0] is True
