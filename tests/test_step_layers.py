# -*- coding: utf-8 -*-
"""tests/test_step_layers.py — `STEP_LAYERS` 声明面与判据 R4（W1-3）。

纪律：断言**声明面**本身（受控值/触发器闭合/拓扑无环/血缘键闭合/计划完备）与**判据的
正负例**（破坏即 FAIL）——每条判据都必须有「改坏就红」的负例，否则判据本身不可信。
"""
from __future__ import annotations

import pytest

from gates import gate_config_integrity as gci


@pytest.fixture(scope="module")
def rpr():
    """链模块（运行时；dataclass 需先登记 sys.modules）。"""
    mod = gci._chain_module()
    assert mod is not None, "链模块不可读"
    return mod


# ---- 声明面结构 ----


def test_step_layers_declaration_shape(rpr):
    """分层声明的基本形状：10 层 / 32 步 / 受控 kind。"""
    from config.enums import STEP_KINDS

    assert len(rpr.STEP_LAYERS) >= 8, sorted(rpr.STEP_LAYERS)
    # 第一百一十三批：32 → 33（relations:ext）
    assert len(rpr.step_specs()) == 33
    assert {s.kind for s in rpr.step_specs()} <= STEP_KINDS


def test_every_step_in_exactly_one_plan_segment(rpr):
    """执行计划完备：每步恰好一次（漏=永不执行，重=重复执行）。"""
    from collections import Counter

    plan = [n for _layer, names in rpr.LAYER_EXEC_PLAN for n in names]
    cnt = Counter(plan)
    assert [k for k, v in cnt.items() if v > 1] == []
    assert set(plan) == {s.name for s in rpr.step_specs()}


def test_conditional_steps_declare_existing_triggers(rpr):
    """条件步骤的 trigger_id 必须存在于 triggers.yaml 且在链内承接口径内。"""
    from std_lib.common_lib import triggers as trg

    ids = {str(t.get("id")) for t in (trg.load_triggers().get("triggers") or [])}
    by_step = {s.trigger_id for s in rpr.step_specs() if s.trigger_id}
    assert by_step <= ids, by_step - ids
    assert by_step == set(trg.CHAIN_TRIGGER_IDS), (
        f"链内承接口径与实际步骤声明不一致: {by_step ^ set(trg.CHAIN_TRIGGER_IDS)}"
    )


def test_produces_are_registered_in_gov_artifacts(rpr):
    """`produces` 必须全部登记进 GOV_ARTIFACTS（否则该产物无血缘可查）。"""
    undeclared = sorted({p for s in rpr.step_specs() for p in s.produces} - set(rpr.GOV_ARTIFACTS))
    assert undeclared == [], f"produces 未登记进 GOV_ARTIFACTS: {undeclared}"


def test_step_spec_rejects_uncontrolled_kind(rpr):
    """构造期校验：非受控 kind 即抛错（不是「事后被发现」）。"""
    with pytest.raises(ValueError, match="kind"):
        rpr.StepSpec("x", "不存在的kind", "abort", "p", 10)


def test_step_spec_rejects_trigger_id_mismatch(rpr):
    """条件步骤必须声明 trigger_id；非条件步骤不得声明。"""
    with pytest.raises(ValueError, match="trigger_id"):
        rpr.StepSpec("x", "conditional", "skip", "p", 10)
    with pytest.raises(ValueError, match="trigger_id"):
        rpr.StepSpec("x", "main", "abort", "p", 10, trigger_id="wiki_sync")


def test_step_spec_rejects_uncontrolled_fail_semantics(rpr):
    from config.enums import FAIL_SEMANTICS

    bad = sorted(FAIL_SEMANTICS - {"abort", "warn", "rc0", "skip"}) or ["__none__"]
    with pytest.raises(ValueError, match="fail_semantics"):
        rpr.StepSpec("x", "main", bad[0], "p", 10)


# ---- 判据 R4：正例 + 负例 ----


def test_r4_passes_on_real_declarations():
    """正例：真实声明面下 R4 全绿。"""
    problems, detail = gci._check_step_layers()
    assert problems == [], problems
    assert detail.get("steps") == 33, detail


def test_r4_rejects_produces_outside_gov_artifacts(monkeypatch):
    """负例：produces 指向未登记产物 → R4-d FAIL。"""
    mod = gci._chain_module()
    monkeypatch.setitem(mod.GOV_ARTIFACTS, "__probe_artifact__", {"produced_by": "x"})
    # 伪造一个 produces 未登记的步骤（冻结 dataclass ⇒ 用替身模块属性）
    fake = mod.StepSpec("probe:step", "main", "abort", "p", 10, produces=("未登记产物",))
    monkeypatch.setattr(mod, "step_specs", lambda: (fake,), raising=False)
    problems, _d = gci._check_step_layers()
    assert any("R4-d" in p for p in problems), problems


def test_r4_rejects_unknown_trigger(monkeypatch):
    """负例：条件步骤声明了不存在的 trigger_id → R4-b FAIL。"""
    mod = gci._chain_module()
    fake = mod.StepSpec("probe:step", "conditional", "skip", "p", 10, trigger_id="不存在的触发项")
    monkeypatch.setattr(mod, "step_specs", lambda: (fake,), raising=False)
    problems, _d = gci._check_step_layers()
    assert any("R4-b" in p for p in problems), problems


def test_r4_detects_dependency_cycle(monkeypatch):
    """负例：步骤互依成环 → R4-c FAIL（拓扑校验真的会红）。"""
    mod = gci._chain_module()
    a = mod.StepSpec("probe:a", "main", "abort", "pa", 10, depends_on=("probe:b",))
    b = mod.StepSpec("probe:b", "main", "abort", "pb", 10, depends_on=("probe:a",))
    monkeypatch.setattr(mod, "step_specs", lambda: (a, b), raising=False)
    problems, _d = gci._check_step_layers()
    assert any("R4-c" in p for p in problems), problems


def test_r4_rejects_step_missing_from_plan(monkeypatch):
    """负例：声明了却不在执行计划中（永不执行）→ R4-e FAIL。"""
    mod = gci._chain_module()
    ghost = mod.StepSpec("probe:ghost", "main", "abort", "pg", 10)
    orig = mod.step_specs()
    monkeypatch.setattr(mod, "step_specs", lambda: orig + (ghost,), raising=False)
    problems, _d = gci._check_step_layers()
    assert any("R4-e" in p and "probe:ghost" in p for p in problems), problems
