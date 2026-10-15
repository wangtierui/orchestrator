# -*- coding: utf-8 -*-
"""tests/test_worklist_triage.py — 分批裁决工具的契约测试（判据配套）。

守卫四条（每条都有对应的**失态**用例，因为「会误判」的批处理工具比没有更危险）：
  1. **默认只读**：`scan` / `bundle` 不得写库（否则「看一眼」就改了数据）；
  2. **机器只判机器能判的**：非自证条目不得被纳入自动批（B1 谓词曾漏扫 subject ⇒ 假阴性；
     反向风险是过宽 ⇒ 单测锁定"自证词"边界）；
  3. **覆盖闭合**：所有 open 项都归入某批次（漏归口 ⇒ 条目永远不出现在任何裁决包里）；
  4. **执行需人工闸门**：缺 `--by` 或 `--reason` 时拒绝执行（无依据的处置不可审计）。
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.contract

from tools import worklist_triage as T

OPEN_STATUS = "open"


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    """临时治理库（与既有治理测试同构：临时库 + 环境覆盖，不碰真库）。"""
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    from std_lib.common_lib import governance_store as _gs

    _gs.init_db()
    return _gs


def _class_map(gs, kind: str) -> dict[str, str]:
    """通过**工具自身 API** 取 item_id → 分类（`_class` 由 `_rows_of` 附加，
    `worklist_list()` 不提供——测试不应假设不存在的行为）。"""
    rows = gs.worklist_list(status=OPEN_STATUS)
    out: dict[str, str] = {}
    for b in T.BATCHES:
        if b["kind"] != kind:
            continue
        for r in T._rows_of(rows, kind, b["batch_id"]):
            out[r["item_id"]] = r["_class"]
    return out


def _mk(gs, kind: str, subject: str, payload: dict) -> str:
    iid = gs.worklist_add(kind, subject, stage="t", payload=payload, suggestion="s")
    assert iid
    return iid


# --------------------------------------------------------------------------- #
# 1) 默认只读
# --------------------------------------------------------------------------- #
def test_scan_is_read_only(gs, capsys):
    """正例：`scan` 不改变任何状态（只读纪律）。"""
    _mk(gs, "relevance_boundary", "summary:9", {"total": 9, "registered": 5})
    before = len(gs.worklist_list(status=OPEN_STATUS))
    assert T.cmd_scan() == 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == before


def test_bundle_is_read_only(gs, tmp_path, capsys):
    """正例：`bundle` 只写裁决包文件，不动台账。"""
    _mk(gs, "rfn_theme_uncertain", "《某办法》", {"decision": "uncertain"})
    before = len(gs.worklist_list(status=OPEN_STATUS))
    assert T.cmd_bundle("rfn_theme_uncertain", str(tmp_path / "out")) == 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == before
    assert os.listdir(tmp_path / "out")


# --------------------------------------------------------------------------- #
# 2) 机器只判机器能判的
# --------------------------------------------------------------------------- #
def test_b2_predicate_only_summary_items(gs):
    """B2 机器判据：仅 `summary:` 前缀的汇总条目可机器处置。"""
    summary = {"total": 588, "registered": 50, "note": "汇总"}
    detail = {"title": "中华人民共和国反洗钱法", "hits": ["a", "b"], "snippet": "…"}
    a = _mk(gs, "relevance_boundary", "summary:588", summary)
    b = _mk(gs, "relevance_boundary", "中华人民共和国反洗钱法", detail)
    cls = _class_map(gs, "relevance_boundary")
    assert cls[a] == "auto", "汇总条目应可机器判定"
    assert cls[b] == "human", "明细条目含业务判断，须人工"


def test_b1_predicate_scans_subject_and_payload(gs):
    """B1 机器判据：自证闭环词在 **subject 或 payload** 任一处即命中（防假阴性）。"""
    in_subject = _mk(gs, "trigger_manual_breakpoint", "N-208 已修复并完成历史数据补救", {})
    in_payload = _mk(gs, "trigger_manual_breakpoint", "某某问题", {"现状": "已处置完毕"})
    pending = _mk(gs, "trigger_manual_breakpoint", "待裁决的开放问题", {"建议": "① 或 ②"})
    cls = _class_map(gs, "trigger_manual_breakpoint")
    assert cls[in_subject] == "auto"
    assert cls[in_payload] == "auto"
    assert cls[pending] == "human", "无闭环标记不得误判为已闭环"


# --------------------------------------------------------------------------- #
# 3) 覆盖闭合
# --------------------------------------------------------------------------- #
def test_every_open_item_is_covered_by_a_batch(gs):
    """正例：所有 open 项都归入某批次（漏归口 ⇒ 永不出现在任何裁决包）。"""
    kinds = ["trigger_manual_breakpoint", "relevance_boundary", "rfn_theme_uncertain",
             "internal_unaligned", "rfn_clean_drift_c2", "timeliness_conflict",
             "clean_quarantine_triage"]
    for k in kinds:
        _mk(gs, k, f"{k}-subject", {"x": 1})
    rows = gs.worklist_list(status=OPEN_STATUS)
    covered: list[str] = []
    for b in T.BATCHES:
        covered += [r["item_id"] for r in T._rows_of(rows, b["kind"], b["batch_id"])]
    assert len(covered) == len(rows), (
        f"覆盖 {len(covered)}/{len(rows)}；未归批 kind="
        f"{sorted({r['kind'] for r in rows} - {b['kind'] for b in T.BATCHES})}")


# --------------------------------------------------------------------------- #
# 4) 执行需人工闸门
# --------------------------------------------------------------------------- #
def test_apply_dry_run_by_default(gs, capsys):
    """正例：`apply` 默认 dry-run（不落库）。"""
    _mk(gs, "relevance_boundary", "summary:1", {"total": 1})
    assert T.cmd_apply("B2", False, "", "", False) == 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == 1, "dry-run 却落库了"


def test_apply_requires_by_and_reason(gs, capsys):
    """负例：执行缺 `--by` 或 `--reason` 时拒绝（无处置人/无依据的处置不可审计）。"""
    _mk(gs, "relevance_boundary", "summary:1", {"total": 1})
    assert T.cmd_apply("B2", True, "", "有依据但缺处置人", False) != 0
    assert T.cmd_apply("B2", True, "alice", "", False) != 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == 1, "缺参数却仍落库了"


def test_apply_b1_requires_explicit_opt_in(gs, capsys):
    """负例：B1（弱证据）必须显式 `--include-self-closed` 才执行。"""
    _mk(gs, "trigger_manual_breakpoint", "某问题 已修复", {})
    assert T.cmd_apply("B1", True, "alice", "依据", False) == 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == 1, "未 opt-in 却执行了弱证据批次"


def test_apply_writes_audit_per_item(gs):
    """正例：执行后逐条落审计（判据 C1 端到端：worklist 状态 + audit_log 同时变）。"""
    iid = _mk(gs, "relevance_boundary", "summary:5", {"total": 5})
    assert T.cmd_apply("B2", True, "alice", "汇总项无裁决对象") == 0
    assert not [r for r in gs.worklist_list(status=OPEN_STATUS) if r["item_id"] == iid]
    rows = gs.list_audit(limit=20)
    hit = [r for r in rows if r.get("target_key") == iid]
    assert hit, "处置后 audit_log 无留痕"
    assert hit[0]["action"] == "worklist_dismissed"
    assert hit[0]["actor"] == "alice"
    assert hit[0]["basis"] == "汇总项无裁决对象"


def test_apply_refuses_business_judgement_batch(gs, capsys):
    """负例：需业务判断的批次（B3/B4）拒绝执行——工具不代做业务决定。"""
    _mk(gs, "rfn_theme_uncertain", "《某办法》", {"decision": "uncertain"})
    assert T.cmd_apply("B4", True, "alice", "想批量处置", False) == 0
    assert len(gs.worklist_list(status=OPEN_STATUS)) == 1, "业务判断批次被自动处置了"


# --------------------------------------------------------------------------- #
# 5) 动作名受控（曾因写成 dismiss 而被写口拒绝）
# --------------------------------------------------------------------------- #
def test_auto_actions_are_within_controlled_domain():
    """正例：批次声明的 auto_action 必须 ∈ {resolved, dismissed}（写口受控域）。"""
    for b in T.BATCHES:
        act = b.get("auto_action")
        if act:
            assert act in ("resolved", "dismissed"), f"{b['batch_id']} 动作越界：{act}"


def test_bundle_json_is_machine_readable(gs, tmp_path):
    """正例：裁决包 json 结构稳定（供后续批量导入/核对）。"""
    _mk(gs, "rfn_theme_uncertain", "《某办法》", {"decision": "uncertain", "title": "《某办法》"})
    assert T.cmd_bundle("rfn_theme_uncertain", str(tmp_path / "out")) == 0
    files = [f for f in os.listdir(tmp_path / "out") if f.endswith(".json")]
    doc = json.load(open(tmp_path / "out" / files[0], encoding="utf-8"))
    assert doc["kind"] == "rfn_theme_uncertain"
    assert doc["batches"] and "items" in doc["batches"][0]
    assert doc["batches"][0]["items"][0]["subject"] == "《某办法》"
