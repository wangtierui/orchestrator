# -*- coding: utf-8 -*-
"""tests/test_c1_human_audit.py — 判据 C1：审计面**必须覆盖人工处置**。

背景（2026-10-16 治理决策）：`audit_log` 此前只有 MCP 写级审计
（`mcp_server/registry.py:270`），而人工裁决走 `governance_store.worklist_resolve`
——CLI（`commands/worklist.py:53`）与确认台（`web/snapshots.py:557`）两个入口都经它，
却**不写审计**。后果：`resolved_by` 有处置人，但「为何如此裁决」无处可查，
641 条 open 的分批裁决将无法追溯决策依据。

守卫三类：
  1. **正例**：resolve/dismiss 各写一条审计，actor 与 `resolved_by` 同源、basis=处置理由；
  2. **负例（纪律）**：审计写入失败**不得阻断处置**（旁路留痕，否则诱导重复处置）；
  3. **负例（幂等）**：未命中（item 不存在/已处置）**不得**写审计（否则审计里出现幽灵裁决）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    """临时治理库夹具（与 `tests/test_governance_worklist.py:13` 同构：临时库 + 环境覆盖）。

    本文件**自建**而不跨文件导入——夹具是他文件的私有实现，跨文件 import 会让
    两份用例耦合并在其重构时连带失败。
    """
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    from std_lib.common_lib import governance_store as _gs

    _gs.init_db()
    return _gs



def _audit_rows(gs, limit: int = 50) -> list[dict]:
    return gs.list_audit(limit=limit)


def test_resolved_writes_audit(gs) -> None:
    """正例：裁决写一条审计，actor/理由/新旧状态齐全。"""
    item_id = gs.worklist_add(
        "rfn_theme_uncertain", "人工裁决留痕用例", stage="test",
        payload={"detail": "c1"}, suggestion="登记主题", confidence=0.5)
    assert item_id
    assert gs.worklist_resolve(item_id, "确认为药品监管主题，采纳", by="alice") is True

    rows = [r for r in _audit_rows(gs) if r.get("target_key") == item_id]
    assert rows, "裁决后 audit_log 无对应记录（C1 缺口）"
    rec = rows[0]
    assert rec["action"] == "worklist_resolved"
    assert rec["target"] == "worklist"
    assert rec["field"] == "status"
    assert rec["old_value"] == "open"
    assert rec["new_value"] == "resolved"
    assert rec["basis"] == "确认为药品监管主题，采纳"
    assert rec["actor"] == "alice"
    # actor 必须与台账 resolved_by 同源（不得两处口径分叉）
    row = next(r for r in gs.worklist_list(status="resolved") if r["item_id"] == item_id)
    assert row["resolved_by"] == rec["actor"]


def test_dismissed_writes_audit(gs) -> None:
    """正例：dismiss（判为无需处置）同样留痕，且 action 可区分。"""
    item_id = gs.worklist_add(
        "relevance_boundary", "dismiss 留痕用例", stage="test")
    assert item_id
    assert gs.worklist_resolve(item_id, "人工判定为无需处置", by="bob",
                               status="dismissed") is True
    rows = [r for r in _audit_rows(gs) if r.get("target_key") == item_id]
    assert rows and rows[0]["action"] == "worklist_dismissed"
    assert rows[0]["new_value"] == "dismissed"


def test_miss_does_not_write_audit(gs) -> None:
    """负例：未命中（不存在/已处置）不得写审计（否则审计里出现幽灵裁决）。"""
    before = len(_audit_rows(gs))
    assert gs.worklist_resolve("WL-does-not-exist", "x") is False
    assert len(_audit_rows(gs)) == before, "未命中的处置却写了审计"


def test_audit_failure_does_not_block_resolution(gs, monkeypatch) -> None:
    """负例（纪律）：审计写入失败**不得阻断处置**——裁决是已发生的事实。"""
    item_id = gs.worklist_add(
        "timeliness_conflict", "审计失败不阻断用例", stage="test")
    assert item_id

    def _boom(*_a, **_k):
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr("std_lib.common_lib.governance_store.projections.log_audit", _boom)
    # 处置仍须成功（否则调用方会重试 → 重复处置）
    assert gs.worklist_resolve(item_id, "审计不可用时的裁决", by="carol") is True
    row = next(r for r in gs.worklist_list(status="resolved") if r["item_id"] == item_id)
    assert row["resolution"] == "审计不可用时的裁决"
    assert not [r for r in _audit_rows(gs) if r.get("target_key") == item_id]
