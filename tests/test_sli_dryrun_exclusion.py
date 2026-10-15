# -*- coding: utf-8 -*-
"""tests/test_sli_dryrun_exclusion.py — 判据：SLI 的链成功率**不得把 dry-run 计入运行**。

背景（2026-10-16 实测缺陷）：`sli_chain` 原取"最近 N 次 run_log"判failed 占比，
而 dry-run（`--dry-run` 只列 argv 不执行）也写 run_log/run_step：
  · run_step 5270 行中 dry-run 占 3449 行（65.4%）；run 数 141 中 105 个是纯 dry-run（74.5%）。
dry-run 每步 rc=0 ⇒ 只要窗口内多为 dry-run，成功率恒 100% ⇒ SLO ≥98% 永不触发，指标形同虚设。

守卫三类：
  1. 正例：窗口内全是 dry-run 时，SLI 必须**不把它们算作运行**；
  2. 负例：真实失败的生产运行必须被计入（不得因修口径而漏判失败）；
  3. 披露：`excluded_dry_run_total` 必须出现在结果里（排除量要可见，不许静默过滤）。
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.contract

from tools import check_slo as S

_DRY = "--dry-run（只列 argv，不执行）"


@pytest.fixture()
def conn():
    """内存库 + 最小 schema（run_log / run_step 两表即可覆盖 sli_chain）。"""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE run_log (run_id TEXT, started_at TEXT)")
    c.execute("CREATE TABLE run_step (run_id TEXT, step TEXT, status TEXT, note TEXT)")
    return c


def _add_run(c, run_id, at, steps, note=""):
    c.execute("INSERT INTO run_log VALUES (?,?)", (run_id, at))
    for i, st in enumerate(steps):
        c.execute("INSERT INTO run_step VALUES (?,?,?,?)", (run_id, f"s{i}", st, note))


def test_dry_run_only_window_yields_no_runs(conn):
    """负例：窗口内全是 dry-run ⇒ 不产出 SLI（而非虚高 100%）。"""
    for i in range(5):
        _add_run(conn, f"DRY{i}", f"2026-10-15 0{i}:00:00", ["ok"] * 3, _DRY)
    out = S.sli_chain(conn, 7)
    assert out is None, f"dry-run 被当成了运行：{out}"


def test_dry_run_excluded_but_production_counted(conn):
    """正例：dry-run 被排除，生产运行照常计入（且排除量要披露）。"""
    _add_run(conn, "DRY0", "2026-10-15 10:00:00", ["ok"] * 3, _DRY)
    _add_run(conn, "DRY1", "2026-10-15 11:00:00", ["ok"] * 3, _DRY)
    _add_run(conn, "P0", "2026-10-15 06:00:00", ["ok", "ok", "skip"])
    out = S.sli_chain(conn, 7)
    assert out is not None
    assert out["n"] == 1, f"窗口内只应有 1 次生产运行：{out}"
    assert out["value"] == 100.0
    assert out.get("excluded_dry_run_total") == 2, "排除量必须可见（不许静默过滤）"


def test_real_failure_still_counted(conn):
    """负例：生产运行里的失败步必须让 SLI 下降（不得因修口径而漏判失败）。"""
    _add_run(conn, "P0", "2026-10-15 06:00:00", ["ok", "ok", "ok"])
    _add_run(conn, "P1", "2026-10-15 07:00:00", ["ok", "failed", "ok"])
    out = S.sli_chain(conn, 7)
    assert out["n"] == 2 and out["ok"] == 1
    assert out["value"] == 50.0, f"失败未被计入：{out}"


def test_mixed_window_excludes_dry_run_from_ratio(conn):
    """正例：混合窗口下，比率只按生产运行计算（不是 3 次里的 2 次）。"""
    _add_run(conn, "P0", "2026-10-15 06:00:00", ["ok", "ok"])
    _add_run(conn, "DRY0", "2026-10-15 07:00:00", ["ok", "ok"], _DRY)
    _add_run(conn, "P1", "2026-10-15 08:00:00", ["ok", "failed"])
    out = S.sli_chain(conn, 10)
    assert out["n"] == 2, f"dry-run 计入分母了：{out}"
    assert out["value"] == 50.0


def test_slo_history_records_exclusion_note(conn):
    """正例：结果里带 note 说明为何排除（披露而非静默）。"""
    _add_run(conn, "DRY0", "2026-10-15 07:00:00", ["ok"], _DRY)
    _add_run(conn, "P0", "2026-10-15 06:00:00", ["ok"])
    out = S.sli_chain(conn, 7)
    assert "dry-run" in (out.get("note") or ""), f"缺少排除说明：{out}"
