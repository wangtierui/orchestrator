# -*- coding: utf-8 -*-
"""tests/test_failure_matrix.py — 失败传播矩阵与执行器归一化（W1-4，v2 §3.3.3）。

背景（D-7）：原执行器用「rc != 0 即链失败」一刀切 ⇒ 旁路步骤（`relevance:{src}`，
fail_semantics=warn）失败会把整条链标成失败并触发 `failed_step` 告警；只读披露/增强层
后端不可用（**合法状态**）同理。现按 `kind × fail_semantics → action` 矩阵分流：
  stop_chain（计入链失败）/ warn_continue（可见不阻断）/ force_rc0（合法未就绪）/ skip_or_run。
"""
from __future__ import annotations

import pytest

from config.enums import FAILURE_ACTIONS, FAILURE_MATRIX, STEP_KINDS, failure_action
from gates import gate_config_integrity as gci


@pytest.fixture(scope="module")
def rpr():
    mod = gci._chain_module()
    assert mod is not None, "链模块不可读"
    return mod


# ---- 矩阵自身 ----


def test_matrix_covers_all_kind_and_semantics():
    """矩阵必须覆盖全部 kind × fail_semantics，且取值都在受控动作域内。"""
    assert set(FAILURE_MATRIX) == set(STEP_KINDS)
    for kind, row in FAILURE_MATRIX.items():
        assert set(row.values()) <= FAILURE_ACTIONS, kind


def test_matrix_unknown_combination_fails_closed():
    """未知 kind/语义组合**保守按最严**（stop_chain）——不静默放宽。"""
    assert failure_action("不存在的kind", "abort") == "stop_chain"
    assert failure_action("main", "不存在的语义") == "stop_chain"


def test_disclosure_and_enhancement_never_block():
    """披露/增强层：任何失败语义都不得阻断链（后端不可用是合法状态）。"""
    for kind in ("disclosure", "enhancement"):
        for fs in ("abort", "warn", "rc0"):
            assert failure_action(kind, fs) == "force_rc0", (kind, fs)


def test_side_steps_warn_but_do_not_stop():
    """旁路（side）：失败可见但不阻断（含 `abort` 声明——以 warn 行为为准）。"""
    for fs in ("abort", "warn"):
        assert failure_action("side", fs) == "warn_continue", fs


def test_main_and_gate_stop_chain():
    """主链与链尾门禁：失败即停（交付准入门不容降级）。"""
    assert failure_action("main", "abort") == "stop_chain"
    assert failure_action("gate", "abort") == "stop_chain"


# ---- 执行器归一化 ----


def _mkentry(step: str, rc: int, *, skipped: bool = False) -> dict:
    return {"step": step, "rc": rc, "exit_code": rc, "elapsed_s": 1.0, "skipped": skipped}


def test_apply_matrix_forces_rc0_for_disclosure(rpr):
    """披露步骤失败 → raw_rc 留存、rc 归零（不计入链失败）。"""
    rep = [_mkentry("retention:plan", 3)]
    rpr._apply_failure_matrix(rep)
    e = rep[0]
    assert e["rc"] == 0 and e["raw_rc"] == 3
    assert e["forced_rc0"] is True
    assert [x for x in rep if x["rc"] != 0] == []


def test_apply_matrix_marks_side_as_degraded(rpr):
    """旁路步骤失败 → 记 degraded（台账可见），rc 不进 failed 桶。"""
    rep = [_mkentry("relevance:gov", 1)]
    rpr._apply_failure_matrix(rep)
    e = rep[0]
    assert e["degraded"] is True and e["raw_rc"] == 1
    assert e["rc"] == 1  # 保留原值供审计；分桶由 kind/action 决定
    assert e["action"] == "warn_continue"


def test_apply_matrix_keeps_main_failure(rpr):
    """主链失败 → 保留非零 rc（计入链失败）。"""
    rep = [_mkentry("clean:gov", 2)]
    rpr._apply_failure_matrix(rep)
    assert rep[0]["rc"] == 2 and rep[0]["action"] == "stop_chain"
    assert not rep[0].get("degraded")


def test_apply_matrix_skipped_is_not_failure(rpr):
    """跳过（条件未触发/未选中）恒 rc=0，不算失败。"""
    rep = [_mkentry("wiki:sync", 1, skipped=True)]
    rpr._apply_failure_matrix(rep)
    assert rep[0]["rc"] == 0
    assert not rep[0].get("degraded") and not rep[0].get("forced_rc0")


def test_stop_requested_only_for_stop_chain(rpr):
    """`--stop-on-error` 只对 stop_chain 生效；旁路/披露失败不中止链。"""
    import argparse

    args = argparse.Namespace(stop_on_error=True)
    main_fail = _mkentry("clean:gov", 1)
    main_fail["action"] = "stop_chain"
    assert rpr._stop_requested(args, main_fail) is True

    side_fail = _mkentry("relevance:gov", 1)
    side_fail["action"] = "warn_continue"
    assert rpr._stop_requested(args, side_fail) is False

    # 未归一化时按声明现查（矩阵 SSOT 单一）
    unnorm = _mkentry("clean:mof", 1)
    assert rpr._stop_requested(args, unnorm) is True
    unnorm_side = _mkentry("relevance:mof", 1)
    assert rpr._stop_requested(args, unnorm_side) is False

    # 未开启开关时永不中止
    assert rpr._stop_requested(argparse.Namespace(stop_on_error=False), main_fail) is False


def test_all_chain_steps_have_action(rpr):
    """链上每一步在归一化后都带 kind/action（台账可机读）。"""
    rep = [_mkentry(s.name, 0) for s in rpr.step_specs()]
    rpr._apply_failure_matrix(rep)
    assert all(e.get("kind") and e.get("action") for e in rep)
    assert {e["kind"] for e in rep} <= STEP_KINDS
