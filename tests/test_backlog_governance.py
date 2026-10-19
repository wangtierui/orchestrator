# -*- coding: utf-8 -*-
"""tests/test_backlog_governance.py — 诊断第 3 波（待办积压治理回路）契约测试。

锁的要点（每条都配失态）：
  ① **机器判定边界保守**——三条件 + 一护栏，缺一不判（宁可漏判不可错判）；
  ② **时区格式与库内同构**——`created_at` 是 `+0800`（无冒号），
     cutoff 若用 `isoformat()` 的 `+08:00` 会全中（曾实测"近30天=全部"）；
  ③ **口径可复现**——增长指标有 `cutoff` 与 `history_span` 披露，
     不让后人把"近30天=全部"误判成统计错误；
  ④ **写口唯一**——一律走 `worklist_resolve`（C1 审计在其内）；
  ⑤ **`--by`/`--reason` 必填**——审计要求，缺则拒绝执行。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure():
    """无需注入 `sys.path`：`tests/conftest.py:19` 已 `bootstrap("all",
    include_tools=True)`（唯一引导点；`gate_import_bootstrap` 的基线只减不增，
    测试内自注入会顶破基线）。保留空函数只为不改动既有调用点。"""
    return None


def _mkdb(tmp_path, rows):
    """建一个最小治理库（worklist + audit_log 两表）。

    ⚠️ DDL 按**真实 schema** 建（不是简版）：`worklist_resolve` 会写
    `resolved_by`/`resolved_at`/`resolution`，缺列直接 OperationalError
    （这条坑是测试自己踩出来的）。
    """
    db = tmp_path / "gov.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE worklist(
          item_id      TEXT PRIMARY KEY,
          kind         TEXT NOT NULL,
          stage        TEXT NOT NULL DEFAULT '',
          artifact_key TEXT NOT NULL DEFAULT '',
          round        TEXT NOT NULL DEFAULT '',
          subject      TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          suggestion   TEXT NOT NULL DEFAULT '',
          confidence   REAL,
          status       TEXT NOT NULL DEFAULT 'open',
          resolved_by  TEXT NOT NULL DEFAULT '',
          resolved_at  TEXT NOT NULL DEFAULT '',
          resolution   TEXT NOT NULL DEFAULT '',
          created_at   TEXT NOT NULL
        );
        CREATE TABLE audit_log(
          id         INTEGER PRIMARY KEY AUTOINCREMENT,
          ts         TEXT NOT NULL,
          actor      TEXT NOT NULL DEFAULT '',
          action     TEXT NOT NULL,
          target     TEXT NOT NULL,
          target_key TEXT NOT NULL DEFAULT '',
          field      TEXT NOT NULL DEFAULT '',
          old_value  TEXT NOT NULL DEFAULT '',
          new_value  TEXT NOT NULL DEFAULT '',
          basis      TEXT NOT NULL DEFAULT '',
          run_id     TEXT
        );
    """)
    con.executemany(
        "INSERT INTO worklist(item_id,kind,subject,status,created_at,payload_json)"
        " VALUES(?,?,?,?,?,?)", rows)
    con.commit()
    return con


def _row(iid, subject, *, kind="trigger_manual_breakpoint", status="open",
         at="2026-10-01T10:00:00+0800", payload=None):
    return (iid, kind, subject, status, at, json.dumps(payload or {}, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# selfclosed 判定边界
# --------------------------------------------------------------------------- #
class TestSelfClosed:
    def test_closed_subject_without_pending_key_is_detected(self, tmp_path) -> None:
        """正例：subject 含闭环词、无否定语境、payload 无待决键 ⇒ 判定。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "N-208 已修复并完成历史数据补救",
                                    payload={"修复": "…", "效果": "…"})])
        got = B.collect_selfclosed(con)
        assert [x["item_id"] for x in got] == ["A"]
        assert "已修复" in got[0]["reason"]

    def test_pending_key_blocks_detection(self, tmp_path) -> None:
        """负例：payload 有 `待裁决` ⇒ **不判**（还有待决事项）。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "某功能已修复",
                                    payload={"待裁决": "删还是换"})])
        assert B.collect_selfclosed(con) == []

    def test_negative_hint_blocks_detection(self, tmp_path) -> None:
        """负例：subject 含否定语境（部分完成/未改码/改法已定）⇒ **不判**。"""
        _ensure()
        from tools import backlog_governance as B

        rows = [
            _row("A", "W-H 部分完成：已核实边界、未改码"),
            _row("B", "表格轨残余三项改法已定未执行"),
            _row("C", "历史 raw 双份未清 + 下次刷新消除"),
        ]
        con = _mkdb(tmp_path, rows)
        assert B.collect_selfclosed(con) == []

    def test_advice_only_payload_blocks(self, tmp_path) -> None:
        """负例：payload 只有 `建议`（待决策）⇒ **不判**。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "某缺陷已修复",
                                    payload={"建议": "可再优化"})])
        assert B.collect_selfclosed(con) == []

    def test_open_only_and_non_dict_payload_skipped(self, tmp_path) -> None:
        """负例：只扫 `status='open'`；payload 非 dict 不崩。"""
        _ensure()
        from tools import backlog_governance as B

        rows = [_row("A", "已修复并补救", status="dismissed"),
                ("B", "k", "已修复", "open", "2026-10-01T10:00:00+0800", "[1,2,3]")]
        con = _mkdb(tmp_path, rows)
        assert B.collect_selfclosed(con) == []

    def test_closed_word_must_be_in_subject_not_payload(self, tmp_path) -> None:
        """负例：闭环词只在 payload 里、subject 没有 ⇒ **不判**
        （subject 才是这条待办的对外声明）。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "某待办", payload={"状态": "已修复"})])
        assert B.collect_selfclosed(con) == []


# --------------------------------------------------------------------------- #
# growth 口径
# --------------------------------------------------------------------------- #
class TestGrowth:
    def test_cutoff_format_matches_stored_timestamps(self, tmp_path) -> None:
        """正例：cutoff 格式与库内 `created_at` **同构**（`+0800` 无冒号）。

        这条是曾实测的坑：`isoformat()` 给 `+08:00`，与 `+0800` 字符串比较时
        所有记录都 >= cutoff。
        """
        _ensure()

        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [])
        g = B.collect_growth(con)
        cut = g["last_30d"]["cutoff"]
        assert cut.endswith("+0800") or cut.endswith("+0000"), cut
        assert ":" not in cut[-6:], f"时区偏移不得带冒号：{cut}"

    def test_net_growth_counts_only_recent(self, tmp_path) -> None:
        """正例：近 30 天窗口**不含**更早记录（窗口口径真实生效）。"""
        _ensure()
        from datetime import datetime, timedelta

        from tools import backlog_governance as B

        old = (datetime.now(UTC) - timedelta(days=90)).astimezone()
        old_s = old.strftime("%Y-%m-%dT%H:%M:%S%z")
        new = (datetime.now(UTC) - timedelta(days=3)).astimezone()
        new_s = new.strftime("%Y-%m-%dT%H:%M:%S%z")
        con = _mkdb(tmp_path, [_row("OLD", "旧的", at=old_s), _row("NEW", "新的", at=new_s)])
        g = B.collect_growth(con)
        assert g["last_30d"]["created"] == 1, g["last_30d"]

    def test_history_span_disclosed(self, tmp_path) -> None:
        """正例：披露数据跨度（防止把"近30天≈全部"误判成统计错误）。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "x", at="2026-09-26T12:28:06+0800")])
        hs = B.collect_growth(con)["history_span"]
        assert hs["first"] == "2026-09-26T12:28:06+0800"

    def test_alert_when_over_threshold(self, tmp_path) -> None:
        """正例：open ≥ 阈值 ⇒ 判应告警，且带 evidence。"""
        _ensure()
        from tools import backlog_governance as B

        rows = [_row(f"A{i}", "x") for i in range(5)]
        con = _mkdb(tmp_path, rows)
        v = B.assess_growth(B.collect_growth(con), warn_open=3, warn_net=999)
        assert v["should_alert"]
        assert v["alerts"][0]["kind"] == "open_backlog"
        assert v["alerts"][0]["evidence"]

    def test_no_alert_below_threshold(self, tmp_path) -> None:
        """负例：低于阈值不告警（阈值口径显式可复现）。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "x")])
        v = B.assess_growth(B.collect_growth(con), warn_open=999, warn_net=999)
        assert not v["should_alert"]

    def test_empty_db_does_not_crash(self, tmp_path) -> None:
        """负例：空库不崩、处置率不除零。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [])
        g = B.collect_growth(con)
        assert g["open"] == 0 and g["disposal_rate_pct"] == 0.0


# --------------------------------------------------------------------------- #
# apply 受控执行
# --------------------------------------------------------------------------- #
class TestApply:
    def test_dry_run_default_no_write(self, tmp_path, capsys) -> None:
        """负例：默认 dry-run **不写库**。"""
        _ensure()
        from tools import backlog_governance as B

        con = _mkdb(tmp_path, [_row("A", "已修复并补救")])
        before = con.execute("SELECT COUNT(*) FROM worklist WHERE status='open'").fetchone()[0]
        B.cmd_apply(False, "", "")
        after = con.execute("SELECT COUNT(*) FROM worklist WHERE status='open'").fetchone()[0]
        assert before == after == 1

    def test_execute_requires_by_and_reason(self, capsys) -> None:
        """负例：`--execute` 缺 `--by`/`--reason` ⇒ 拒绝（审计要求）。"""
        _ensure()
        from tools import backlog_governance as B

        assert B.cmd_apply(True, "", "理由") != 0
        assert B.cmd_apply(True, "someone", "") != 0
        assert "必填" in capsys.readouterr().err

    def test_execute_writes_audit_per_item(self, tmp_path, monkeypatch) -> None:
        """正例：执行后每条都落 audit_log（C1 审计在写口内）。

        ⚠️ 用**官方覆盖机制** `REG_ORCH_GOVERNANCE_DB`（`governance_store._db:318`）
        而非改 `paths.DATA_DIR`——后者不可靠（写口可能已解析过路径）。
        """
        _ensure()
        import importlib

        from tools import backlog_governance as B

        _mkdb(tmp_path, [_row("A", "已修复并补救"),
                         _row("B", "另一项已处置")])
        monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
        # 写口按 env 解析路径，但连接可能已建 ⇒ 重载相关模块
        for m in [k for k in list(sys.modules) if "governance_store" in k]:
            del sys.modules[m]
        importlib.reload(B)
        try:
            rc = B.cmd_apply(True, "tester", "自证闭环批量 dismiss")
        finally:
            for m in [k for k in list(sys.modules) if "governance_store" in k]:
                del sys.modules[m]
            importlib.reload(B)
        assert rc == 0, "两条都应成功处置"
        con2 = sqlite3.connect(str(tmp_path / "gov.db"))
        st = dict(con2.execute("SELECT item_id, status FROM worklist").fetchall())
        n_audit = con2.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='worklist_dismissed'").fetchone()[0]
        con2.close()
        assert st == {"A": "dismissed", "B": "dismissed"}, st
        assert n_audit == 2, f"每条都应落审计：{n_audit}"
