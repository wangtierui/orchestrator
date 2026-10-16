# -*- coding: utf-8 -*-
"""tests/test_run_step_schema_ext.py — C3 + TD-3 的 schema 扩展守卫。

三条纪律各有对应用例：
  1. **迁移幂等且不改数据**：`ALTER TABLE ADD COLUMN` 后行数不变、老数据三列为 0
     （**不反推填充**——反推值 = 记录时刻 ∓ 耗时，与实测语义不同，混用会污染时间窗判定）；
  2. **写入方三字段齐备**：`step_record` 落 started_at/finished_at/is_dry_run；
  3. **SLI 判据优先用列**：有 `is_dry_run` 列时用它（文案变更不影响口径），
     无列时降级为 note 文本（老库兼容）——**两态都要测**，否则改动只在其中一态被验证。
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.contract

from std_lib.common_lib import governance_store as gs
from tools import check_slo as S

_DRY = "--dry-run（只列 argv，不执行）"


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """临时治理库（覆盖 env，触达真迁移逻辑）。

    注意：`step_record` 无 `run_id` 时取 `current_run_id()`，为空则**直接返回**
    （`ledger.py:103-105`）⇒ 用例须显式传 `run_id`（与 `test_w4_data_layer.py` 同做法）。
    """
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "g.db"))
    gs.init_db()
    return gs


def _cols(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(run_step)")}


# --------------------------------------------------------------------------- #
# 1) 迁移
# --------------------------------------------------------------------------- #
def test_migration_adds_three_columns(lib):
    """正例：迁移后 run_step 含三列。"""
    with gs.connect(readonly=True) as c:
        cols = _cols(c)
    assert {"started_at", "finished_at", "is_dry_run"} <= cols


def test_migration_is_idempotent(lib):
    """正例：重复 init_db 不报错、列数不变（幂等）。"""
    before = 0
    with gs.connect(readonly=True) as c:
        before = len(_cols(c))
    lib.init_db()
    lib.init_db()
    with gs.connect(readonly=True) as c:
        assert len(_cols(c)) == before


def test_migration_does_not_backfill(lib):
    """负例：老数据三列保持 0（**不反推填充**）。

    反推（用 recorded_at ∓ elapsed_s 倒算）看似"补全"，实则把估算值混入实测列——
    根因分析按时间窗比对产物 mtime 时，估算窗会让判定失真而**无从察觉**。
    """
    lib.step_record("clean:gov", 0, elapsed_s=100.0, note="", run_id="run-old")
    with gs.connect(readonly=True) as c:
        row = tuple(c.execute("SELECT started_at, finished_at FROM run_step").fetchone())
    assert row == (0.0, 0.0), f"老数据被回填了：{row}"


def test_record_writes_new_fields(lib):
    """正例：写入方三字段齐备。"""
    lib.step_record("collect:nfra", 0, elapsed_s=12.5, started_at=1000.0,
                    finished_at=1012.5, is_dry_run=True, note="x", run_id="run-x")
    with gs.connect(readonly=True) as c:
        row = tuple(c.execute(
            "SELECT started_at, finished_at, is_dry_run FROM run_step").fetchone())
    assert row == (1000.0, 1012.5, 1)


def test_record_defaults_are_zero(lib):
    """正例：不传新字段时落 0/0/0（与迁移前的老数据同语义，不臆造）。"""
    lib.step_record("clean:gov", 0, elapsed_s=1.0, note="", run_id="run-y")
    with gs.connect(readonly=True) as c:
        row = tuple(c.execute(
            "SELECT started_at, finished_at, is_dry_run FROM run_step").fetchone())
    assert row == (0.0, 0.0, 0)


# --------------------------------------------------------------------------- #
# 2) SLI 判据双态
# --------------------------------------------------------------------------- #
def _mem(with_col: bool):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE run_log (run_id TEXT, started_at TEXT)")
    cols = ("run_id, step, status, note, is_dry_run" if with_col
            else "run_id, step, status, note")
    c.execute(f"CREATE TABLE run_step ({cols})")
    return c


def _mk(c, rid, at, steps, note="", has_col=False, dry=False):
    """写一次运行。

    `has_col` = 表**结构**是否有 is_dry_run 列；`dry` = **该行**是否 dry-run。
    二者必须分开——先前用同一个参数表达，导致「列判据」测试里 dry 行的列值恒为 0。
    """
    c.execute("INSERT INTO run_log VALUES (?,?)", (rid, at))
    for i, st in enumerate(steps):
        if has_col:
            c.execute("INSERT INTO run_step VALUES (?,?,?,?,?)",
                      (rid, f"s{i}", st, note, 1 if dry else 0))
        else:
            c.execute("INSERT INTO run_step VALUES (?,?,?,?)", (rid, f"s{i}", st, note))


def test_sli_prefers_column_when_present():
    """正例：有列时按 `is_dry_run` 判（即使 note 文本不含 dry-run 字样也能识别）。"""
    c = _mem(with_col=True)
    # 关键构造：dry-run 行的 note 改成**不含** dry-run 字样的文案
    _mk(c, "DRY0", "2026-10-15 07:00:00", ["ok"], note="试运行（只列 argv）",
       has_col=True, dry=True)
    _mk(c, "P0", "2026-10-15 06:00:00", ["ok"], has_col=True)
    out = S.sli_chain(c, 7)
    assert out is not None and out["n"] == 1, f"未按列识别 dry-run：{out}"
    assert out.get("dry_run_predicate") == "is_dry_run 列"
    c.close()


def test_sli_falls_back_to_note_without_column():
    """正例：无列时降级为 note 文本判据（老库兼容路径也被验证）。"""
    c = _mem(with_col=False)
    _mk(c, "DRY0", "2026-10-15 07:00:00", ["ok"], note=_DRY)
    _mk(c, "P0", "2026-10-15 06:00:00", ["ok"])
    out = S.sli_chain(c, 7)
    assert out is not None and out["n"] == 1, f"文本兜底失效：{out}"
    assert "note 文本" in (out.get("dry_run_predicate") or "")
    c.close()


def test_sli_still_counts_real_failures_with_column():
    """负例：列判据下真实失败仍被计入（不得因改成列而漏判失败）。"""
    c = _mem(with_col=True)
    _mk(c, "P0", "2026-10-15 06:00:00", ["ok", "ok"], has_col=True)
    _mk(c, "P1", "2026-10-15 07:00:00", ["ok", "failed"], has_col=True)
    out = S.sli_chain(c, 7)
    assert out["n"] == 2 and out["value"] == 50.0
    c.close()
