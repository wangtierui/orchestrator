# -*- coding: utf-8 -*-
"""tests/test_artifact_step_ownership.py — 治理判据：产物**必须有步骤归属**。

背景（2026-10-16，诊断第 1 波实测发现）：`GOV_ARTIFACTS` 有 19 个产物键，但只有
**11 个**在 `StepSpec.produces` 声明了生产步骤 ⇒ 其余 8 个在门禁归因时
**拿不到"重跑哪步"的建议**（`produced_by` 只是模块路径，不是步骤名）。

判据（双向，与既有 R4-d「produces ⊆ GOV_ARTIFACTS」互补）：
  · **正向**（既有 R4-d）：声明了产物却不在血缘声明面 ⇒ FAIL（已在 gate_config_integrity）；
  · **反向**（本测试）：血缘声明面里的产物却**无步骤归属** ⇒ FAIL。
    两向合起来 ⇒ 血缘声明面与链声明面**双向闭合**。

为什么放测试而非门禁：门禁 `ALL_GATES` 是交付闸门，新增判据须配J1–J6 登记与
负例；而这是**声明面一致性**（与 J12/J14 同类的纪律），放 contract 测试更轻。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from tools.run_production_refresh import GOV_ARTIFACTS, step_specs


@pytest.fixture(scope="module")
def ownership() -> dict[str, tuple[str, ...]]:
    """产物键 → 声明生产它的步骤名元组（一个产物可由多步生产）。"""
    out: dict[str, list[str]] = {}
    for s in step_specs():
        for p in s.produces:
            out.setdefault(p, []).append(s.name)
    return {k: tuple(v) for k, v in out.items()}


def test_every_artifact_has_a_producing_step(ownership) -> None:
    """正例：`GOV_ARTIFACTS` 的**每个**产物都有步骤归属（治理缺口已闭合）。"""
    missing = sorted(set(GOV_ARTIFACTS) - set(ownership))
    assert not missing, (
        f"产物无步骤归属（门禁归因将给不出重跑建议）：{missing}")


def test_declared_produces_are_registered(ownership) -> None:
    """正向（与 R4-d 同源的独立复核）：`produces` ⊆ `GOV_ARTIFACTS`。"""
    ghost = sorted(set(ownership) - set(GOV_ARTIFACTS))
    assert not ghost, f"声明了未登记的产物：{ghost}"


def test_ownership_points_to_real_steps(ownership) -> None:
    """正例：归属的步骤名必须是**链上真实步骤**（不是模块路径——那会产出跑不起来的命令）。"""
    names = {s.name for s in step_specs()}
    for art, steps in ownership.items():
        for st in steps:
            assert st in names, f"{art} 的归属 {st!r} 不是链上步骤名"


def test_facade_and_triage_agree_on_ownership(ownership) -> None:
    """正例：门禁归因工具与声明面**同源**（不得各写一份映射——那就是第二事实源）。"""
    from tools.gate_triage import _artifact_keys

    amap = _artifact_keys()
    for art in GOV_ARTIFACTS:
        assert art in amap, f"gate_triage 的产物映射缺 {art}"
        step, module = amap[art]
        if step:
            assert ownership.get(art) and step in ownership[art], (
                f"{art}: gate_triage 说 {step}，声明面说 {ownership.get(art)}")
        else:
            assert module, f"{art}: 既无步骤也无模块路径，归因将给不出任何线索"


def test_recently_added_ownerships_are_correct(ownership) -> None:
    """锁定本批补的 7 处归属（防日后被"顺手改错"）。

    取证依据见 `tools/run_production_refresh.py` 内各处的注释（watermark 的
    `produced_by`/`stage` + `LAYER_EXEC_PLAN` 层归属）。
    """
    expected = {
        "raw:{src}": "collect:{src}",
        "clean_index": "apply:{src}",
        "timeliness:state": "timeliness:verify",
        "rfn_attr": "classify:all",
        "rfn_theme": "classify:all",
        "internal_index": "internal:update",
        "reports:classifier": "reports:build",
        "wiki:sync": "wiki:sync",
    }
    for art, step in expected.items():
        assert ownership.get(art) == (step,), (
            f"{art} 的归属应为 {step}，实际 {ownership.get(art)}")
