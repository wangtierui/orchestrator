# -*- coding: utf-8 -*-
"""tests/test_step_duration_baseline.py — 性能基线工具的契约测试。

守卫两条口径（都是实测踩出来的，不锁住就会回退）：
  1. **dry-run 不进分布**：其 `elapsed_s` 是「列 argv」的进程耗时（通常 <0.1s），
     混进分布会把 p50 压平；
  2. **resume 跳过样本不进分布**：`--resume` 跳过的步骤 `elapsed_s≈0`（未执行），
     混进分布会让 p50 塌到 0.00（实测 18 步里多数如此），基线失去判读价值。
另锁两条产物契约：分位数计算正确、跳过率单列。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from tools import step_duration_baseline as B


@pytest.fixture()
def conn():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE run_log (run_id TEXT, started_at TEXT)")
    c.execute("CREATE TABLE run_step (run_id TEXT, step TEXT, elapsed_s REAL, "
               "started_at REAL, finished_at REAL, rc INTEGER, skipped INTEGER, note TEXT)")
    return c


def _run(c, rid, at, rows):
    """rows: [(step, elapsed, rc, skipped, note)]"""
    c.execute("INSERT INTO run_log VALUES (?,?)", (rid, at))
    for step, e, rc, sk, note in rows:
        c.execute("INSERT INTO run_step VALUES (?,?,?,?,?,?,?,?)",
                  (rid, step, e, 0.0, 0.0, rc, sk, note))


# --------------------------------------------------------------------------- #
# 口径 1：dry-run 排除
# --------------------------------------------------------------------------- #
def test_dry_run_not_counted(conn):
    """正例：dry-run 的**整个 run** 不进入统计（其耗时是「列 argv」的进程耗时）。

    注意 dry-run 是 **run 级**行为（`_DRY_RUN` 是编排器全局开关），不是某步骤属性
    ⇒ 用例须用两个独立 run 构造，不能把两者塞进同一个 run_id。
    """
    _run(conn, "R1", "2026-10-15 06:00:00", [
        ("clean:gov", 180.0, 0, 0, ""),
        ("clean:gov", 190.0, 0, 0, ""),
    ])
    _run(conn, "DRY", "2026-10-15 07:00:00", [
        ("clean:gov", 0.5, 0, 0, "--dry-run（只列 argv，不执行）"),
        ("gates", 0.4, 0, 0, "--dry-run（只列 argv，不执行）"),
    ])
    out = B._collect_from(conn, 30)
    assert out["sample_runs"] == 1, f"dry-run run 未被排除：{out}"
    s = out["per_step"]["clean:gov"]
    assert s["n"] == 2, f"dry-run 样本混入统计：{s}"
    assert "gates" not in out["per_step"], "纯 dry-run run 的步骤不应出现"
    assert out["excluded_dry_run_total"] >= 1, "排除量未披露"


# --------------------------------------------------------------------------- #
# 口径 2：跳过样本不入分布
# --------------------------------------------------------------------------- #
def test_skipped_not_in_duration_stats(conn):
    """负例：`--resume` 跳过的样本（elapsed≈0）不得把 p50 压到 0。"""
    # 3 次真跑（100/110/120s）+ 9 次跳过（0.0s）⇒ 正确 p50 ≈ 110
    rows = [("clean:gov", v, 0, 0, "") for v in (100.0, 110.0, 120.0)]
    rows += [("clean:gov", 0.0, 0, 1, "--resume：上次 rc=0")] * 9
    _run(conn, "R1", "2026-10-15 06:00:00", rows)
    out = B._collect_from(conn, 30)
    s = out["per_step"]["clean:gov"]
    assert s["n"] == 3, f"n 应只计实际执行：{s}"
    assert s["p50"] == 110.0, f"p50 被跳过样本压平了：{s}"
    assert s["skip_rate"] == 75.0, f"跳过率未单列：{s}"


def test_all_skipped_step_has_zero_duration_stats(conn):
    """边界：某步全被跳过 ⇒ 耗时统计为 0 但 skip_rate=100（不伪装成"很快"）。"""
    _run(conn, "R1", "2026-10-15 06:00:00",
         [("semantic:assist", 0.0, 0, 1, "--resume")] * 4)
    s = B._collect_from(conn, 30)["per_step"]["semantic:assist"]
    assert s["n"] == 0 and s["p50"] == 0.0 and s["skip_rate"] == 100.0


# --------------------------------------------------------------------------- #
# 分位数与产物契约
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("xs,q,expect", [
    ([1.0], 0.5, 1.0),
    ([1.0, 2.0, 3.0], 0.5, 2.0),
    ([1.0, 2.0, 3.0, 4.0], 0.5, 2.5),
    ([0.0, 10.0], 0.9, 9.0),
    ([], 0.5, 0.0),
])
def test_percentile(xs, q, expect):
    """正例：线性插值分位数（含空样本退化）。"""
    assert B._pct(xs, q) == pytest.approx(expect)


def test_product_has_required_keys(conn):
    """正例：产物含判读所需字段（p50/p90/max/last/skip_rate/windows/判据来源披露）。"""
    _run(conn, "R1", "2026-10-15 06:00:00", [("gates", 120.0, 0, 0, "")])
    out = B._collect_from(conn, 30)
    for k in ("available", "generated_at", "sample_runs", "per_step", "dry_run_predicate",
              "has_time_window", "notes"):
        assert k in out, f"产物缺字段 {k}"
    s = out["per_step"]["gates"]
    for k in ("n", "p50", "p90", "max", "last", "mean", "unit", "skip_rate", "fails"):
        assert k in s, f"per_step 缺字段 {k}"


def test_time_window_reported_when_columns_present(conn):
    """正例：有 started_at/finished_at 时产出时间窗（供"产物是否落在执行窗内"判定）。"""
    conn.execute("INSERT INTO run_log VALUES ('R1','2026-10-15 06:00:00')")
    conn.execute("INSERT INTO run_step VALUES "
                 "('R1','clean:gov',120.0,1000.0,1120.0,0,0,'')")
    out = B._collect_from(conn, 30)
    assert out["has_time_window"] is True
    w = out["windows"]["clean:gov"]
    assert w["last_start"] == 1000.0 and w["last_end"] == 1120.0


def test_no_production_run_is_graceful(conn):
    """负例：只有 dry-run ⇒ 明确报"无生产运行"（不产出误导性的基线）。"""
    _run(conn, "R1", "2026-10-15 06:00:00",
         [("clean:gov", 0.2, 0, 0, "--dry-run（只列 argv，不执行）")])
    out = B._collect_from(conn, 30)
    assert out.get("available") is False
    assert "无生产运行" in out.get("reason", "")
