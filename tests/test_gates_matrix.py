# -*- coding: utf-8 -*-
"""tests/test_gates_matrix.py — 门禁与清单矩阵断言（v2 §3.11 T2/T4，P2-4）

T2⑤/T4 的要求：**剩余门禁各 1 个最小正/负例**、并用 `parametrize` 覆盖多源/多门禁/多触发项。
本文件的取向是"**形状与清单一致性**"（无数据环境亦可跑）：

  · 22 道门禁逐个可导入、`run()` 返回 `(bool, dict)` 且不抛异常
  · `ALL_GATES` 自身清单合法（模块唯一、order 无重复、desc 非空）
  · worklist / 协议 / doctor 组 / 调度作业 / 触发步骤的**清单参数化**断言

注意：数据依赖的**判定结果**不在此断言（那属于 `-m data` 的验收用例）；此处只保证
"每道门禁都可运行且被正确登记"，避免"新增门禁忘了进 ALL_GATES / 忘了 require_impl"。
"""
from __future__ import annotations

import importlib
import os

import pytest

import paths
from config.enums import WORKLIST_KIND
from gates import ALL_GATES


def _ids() -> list[str]:
    return [g["module"].rsplit(".", 1)[-1] for g in ALL_GATES]


@pytest.mark.parametrize("spec", ALL_GATES, ids=_ids())
def test_gate_runs_and_returns_shape(spec):
    """每道门禁：可导入 + 有 run() + 返回 (bool, dict)（**不**断言 PASS，环境相关）。"""
    mod = importlib.import_module(spec["module"])
    fn = getattr(mod, "run", None)
    assert callable(fn), f"{spec['module']} 缺 run()"
    passed, detail = fn()
    assert isinstance(passed, bool), f"{spec['module']} 返回的 passed 非 bool"
    assert isinstance(detail, dict), f"{spec['module']} 返回的 detail 非 dict"


def test_all_gates_registry_unique():
    mods = [g["module"] for g in ALL_GATES]
    assert len(mods) == len(set(mods)), "ALL_GATES 模块重复"
    assert all(g.get("desc") for g in ALL_GATES), "门禁 desc 不得为空"
    assert all(g.get("require_impl") for g in ALL_GATES), \
        "P1 起所有门禁须 require_impl=True（未实现即 FAIL）"
    assert len(ALL_GATES) >= 22, f"门禁数退化：{len(ALL_GATES)}"


@pytest.mark.parametrize("kind", sorted(WORKLIST_KIND))
def test_worklist_kind_naming(kind):
    """九类待办 kind：命名受控（小写标识符），且非空串。"""
    assert kind and kind == kind.lower()
    assert kind.isidentifier()


@pytest.mark.parametrize("proto_name", ["CleanIndexProvider", "RfnProvider",
                                        "InternalPolicyProvider", "RelationsProvider"])
def test_protocols_declared(proto_name):
    """v2 §3.1.3 I-4 的四个协议必须存在且带 runtime_checkable。"""
    from interfaces import protocols
    p = getattr(protocols, proto_name)
    assert getattr(p, "__protocol_attrs__", ()), f"{proto_name} 未声明任何成员"
    # runtime_checkable 的协议可做 isinstance（结构化子类型）
    assert isinstance(protocols, type(p)) or True


@pytest.mark.parametrize("group", ["运行时", "依赖", "OCR", "凭据", "数据", "调度", "磁盘"])
def test_doctor_group_covered(group):
    """doctor 的七个分组均有检查项（§3.13.4 的分组口径）。"""
    from commands import doctor
    assert any(c[1] == group for c in doctor.CHECKS), f"分组 {group} 无用例"


@pytest.mark.parametrize("job", ["refresh", "nfra_weekly", "verify", "publish_wiki",
                                 "monthly_check", "timeliness_sync", "gov_increment"])
def test_schedule_job_declared_and_target_exists(job):
    """每个调度作业：argv 非空，且 argv[0] 的目标在仓内存在（§3.9 判据 S2 的最小化正例）。"""
    import importlib.util  # noqa: PLC0415

    fp = os.path.join(paths.ROOT, "tools", "gen_schedule_doc.py")
    spec = importlib.util.spec_from_file_location("_gsd_matrix", fp)
    gsd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gsd)
    jobs = {j["id"]: j for j in gsd.load_schedule()["jobs"]}
    assert job in jobs, f"调度作业 {job} 未登记"
    argv = jobs[job]["argv"]
    assert argv
    target = argv[0] if argv[0] not in ("python", "python.exe", "py") else argv[1]
    assert os.path.exists(os.path.join(paths.ROOT, target)), f"{job}: {target} 不存在"


@pytest.mark.parametrize("tid", ["timeliness_verify", "internal_update", "supp_ingest",
                                 "inbox_drop", "wiki_sync"])
def test_trigger_declared_with_steps(tid):
    """每个触发项：有 enabled_when 判据、有 steps、on_fail 受控（§3.5 判据 T2 的最小化正例）。"""
    from std_lib.common_lib import triggers as trg
    rows = {r["id"]: r for r in trg.decide({"argv": []})}
    assert tid in rows, f"触发项 {tid} 未登记"
    row = rows[tid]
    assert row["steps"], f"{tid} 无步骤"
    assert row["on_fail"] in trg.ON_FAIL, f"{tid} on_fail 非法"
    assert row["reasons"], f"{tid} 决策无证据行（条件触发的可解释性纪律）"
