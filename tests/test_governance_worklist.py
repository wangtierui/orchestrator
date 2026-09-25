# -*- coding: utf-8 -*-
"""tests/test_governance_worklist.py — 待办队列单测（v2 §3.14.3 / P1-6）

覆盖：登记幂等（同 kind+subject 只留一条 open）、处置流转、处置后可重新登记、
软校验（未登记 kind 仍写入）、统计聚合、非法参数。全部用临时库。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    from std_lib.common_lib import governance_store

    governance_store.init_db()
    return governance_store


def test_add_list_resolve_flow(gs):
    iid = gs.worklist_add("timeliness_conflict", "税总发〔2020〕1号",
                          stage="2", payload={"a": 1}, suggestion="按权威源裁决")
    assert iid and iid.startswith("WL-")

    rows = gs.worklist_list()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["payload"] == {"a": 1}

    assert gs.worklist_resolve(iid, "确认为现行有效，采纳台账级") is True
    assert gs.worklist_list() == []
    assert len(gs.worklist_list(status="resolved")) == 1
    assert gs.worklist_list(status="resolved")[0]["resolution"] == "确认为现行有效，采纳台账级"


def test_add_is_idempotent_and_refreshes_payload(gs):
    i1 = gs.worklist_add("rfn_clean_drift_c2", "RFN-abc", payload={"v": 1}, confidence=0.4)
    i2 = gs.worklist_add("rfn_clean_drift_c2", "RFN-abc", payload={"v": 2}, confidence=0.9)

    assert i1 == i2, "同 kind+subject 的 open 项必须复用同一 item_id"
    rows = gs.worklist_list()
    assert len(rows) == 1
    assert rows[0]["payload"] == {"v": 2} and rows[0]["confidence"] == 0.9


def test_can_reopen_after_resolve(gs):
    i1 = gs.worklist_add("internal_identity_conflict", "IPN-1")
    assert gs.worklist_resolve(i1, "接受现状") is True
    i2 = gs.worklist_add("internal_identity_conflict", "IPN-1")
    assert i2 and i2 != i1
    assert len(gs.worklist_list()) == 1
    assert len(gs.worklist_list(status="resolved")) == 1


def test_reject_empty_kind_or_subject(gs):
    assert gs.worklist_add("", "x") is None
    assert gs.worklist_add("timeliness_conflict", "") is None
    assert gs.worklist_list() == []


def test_unregistered_kind_is_soft_accepted(gs, capsys):
    """软校验：未登记 kind 仍写入（治理库与 config 解耦），但打印告警。"""
    iid = gs.worklist_add("some_new_kind", "s1")
    assert iid is not None
    assert "未登记的 kind" in capsys.readouterr().out


def test_resolve_unknown_id_returns_false(gs):
    assert gs.worklist_resolve("WL-nonexistent", "x") is False


def test_resolve_rejects_invalid_status(gs):
    iid = gs.worklist_add("timeliness_conflict", "s2")
    with pytest.raises(ValueError):
        gs.worklist_resolve(iid, "x", status="open")
    assert gs.worklist_list()[0]["status"] == "open", "非法 status 不得改动队列"


def test_stats_aggregates_by_kind(gs):
    gs.worklist_add("timeliness_conflict", "a")
    gs.worklist_add("timeliness_conflict", "b")
    gs.worklist_add("internal_unaligned", "c")
    st = gs.worklist_stats()
    assert st["open"] == 3
    assert st["by_kind"] == {"internal_unaligned": 1, "timeliness_conflict": 2}
    assert st["oldest_open_days"] == 0
    assert st["all"] == {"resolved": 0, "dismissed": 0}


def test_kinds_are_registered_in_enum(gs):
    """队列里写入的 kind 必须都在 config.enums.WORKLIST_KIND 中（与 gate J7 同一判据）。"""
    from config.enums import WORKLIST_KIND

    for kind in ("timeliness_conflict", "rfn_clean_drift_c2",
                 "internal_identity_conflict", "internal_unaligned"):
        assert kind in WORKLIST_KIND
