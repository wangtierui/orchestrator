# -*- coding: utf-8 -*-
"""tests/test_p2_governance_layer.py — 第四批（P2-1/P2-2/P2-3b/P2-5/P2-6）回归断言

覆盖：
  · P2-2 调度事实源（判据 S）：yaml schema + 手册自动段一致（含**负例**：手改手册须被发现）
  · P2-1 条件触发（判据 T）：决策表形状 + 未实现条件**不得**默认放行 + argv 目标可达
  · P2-5 worklist：9 类 kind 全部登记且**双向闭合**（判据 J7）
  · P2-3b 投放区：注册表可读、扫描空区为 0、不可识别扩展名进 needs_review（含清理）
  · P2-6 doctor：18 项齐备、quick 子集正确
"""
from __future__ import annotations

import os

import pytest

import paths
from gates import gate_config_integrity

# ---- P2-2 调度事实源 ----


def _gsd():
    import importlib.util  # noqa: PLC0415

    fp = os.path.join(paths.ROOT, "tools", "gen_schedule_doc.py")
    spec = importlib.util.spec_from_file_location("_gsd_test", fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_schedule_yaml_schema_and_jobs():
    data = _gsd().load_schedule()
    jobs = data.get("jobs") or []
    assert len(jobs) >= 5
    ids = [j["id"] for j in jobs]
    assert len(set(ids)) == len(ids)
    assert all(j.get("kind") in ("cron", "event") for j in jobs)
    assert all(j.get("on_miss") in ("skip", "run_at_next_boot") for j in jobs)


def test_cron_label_rendering():
    gsd = _gsd()
    assert gsd.cron_label("0 6 * * *") == "每日 06:00"
    assert gsd.cron_label("0 1 * * 2") == "每周二 01:00"
    assert gsd.cron_label("0 9 1 * *") == "每月 1 日 09:00"
    assert gsd.cron_label("bad") == "bad"


def test_manual_matches_schedule_yaml():
    """判据 S4 正向：手册自动段 == yaml 渲染。"""
    problems, detail = gate_config_integrity._check_schedule()
    assert problems == [], problems
    assert detail["schedule"]["jobs"] >= 5


def test_manual_drift_is_detected(tmp_path):
    """判据 S4 **负例**：手工把定时表改一行必须被判定为漂移。"""
    gsd = _gsd()
    text = open(gsd.MANUAL, encoding="utf-8").read()
    i, k = text.find(gsd.TABLE_START), text.find(gsd.TABLE_END)
    assert i > 0 and k > i
    tampered = (text[:i + len(gsd.TABLE_START)]
                + "\n| **手改** | `0 0 * * *` | `manual` | 不应出现的行 |\n" + text[k:])
    want = gsd._replace_block(tampered, gsd.TABLE_START, gsd.TABLE_END,
                              gsd.render_table(gsd.load_schedule()["jobs"]))
    assert want != tampered, "改动手册后重渲染应与改动不同（否则判据 S4 失效）"


# ---- P2-1 条件触发 ----


def test_triggers_config_and_decision_table():
    from std_lib.common_lib import triggers as trg
    rows = trg.decide({"argv": []})
    assert len(rows) >= 5
    assert {"id", "stage", "enabled", "reasons", "steps", "on_fail"} <= set(rows[0])
    # 证据行必须非空（"可解释"是条件触发的核心纪律）
    assert all(r["reasons"] for r in rows)


def test_trigger_missing_condition_impl_not_enabled(monkeypatch):
    """**纪律②**：条件无实现时不得静默放行（防"条件触发"变成"永远触发"）。"""
    from std_lib.common_lib import triggers as trg
    monkeypatch.setitem(trg.CONDITION_IMPLS, "token_exists", None)
    rows = trg.decide({"argv": []})
    timeliness = next(r for r in rows if r["id"] == "timeliness_verify")
    assert timeliness["enabled"] is False
    assert any("无实现" in x for x in timeliness["reasons"])


def test_trigger_gate_judgments():
    problems, detail = gate_config_integrity._check_triggers()
    assert problems == [], problems
    assert detail["triggers"]["count"] >= 5


# ---- P2-5 worklist 双向闭合 ----


def test_all_worklist_kinds_wired_or_pending():
    from config.enums import WORKLIST_KIND
    problems, detail = gate_config_integrity._check_worklist()
    assert problems == [], problems
    wired = set(detail["worklist"]["wired"])
    pending = set(detail["worklist"]["pending"])
    assert wired | pending == set(WORKLIST_KIND), (wired, pending)
    # 第六批（D6 接入后）：所有 kind 均有产生方 → pending 清空、wired 全量闭合
    assert pending == set(), pending
    assert wired == set(WORKLIST_KIND), (wired, set(WORKLIST_KIND))


# ---- P2-3b 投放区 ----


def test_inbox_registry_and_empty_scan():
    import importlib.util  # noqa: PLC0415

    fp = os.path.join(paths.ROOT, "tools", "inbox_scan.py")
    spec = importlib.util.spec_from_file_location("_inbox_test", fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reg = mod.load_registry()
    assert reg["domains"], "注册表须至少一个域"
    assert set(reg["recognized_ext"]) >= {".pdf", ".docx", ".xlsx"}
    res = mod.scan(apply=False)
    assert res["counters"]["duplicate"] == 0
    assert res["scanned"] >= 0


def test_inbox_unrecognized_ext_needs_review():
    """§3.12.6：不可识别扩展名 → needs_review（**不静默丢弃**）。"""
    import importlib.util  # noqa: PLC0415

    fp = os.path.join(paths.ROOT, "tools", "inbox_scan.py")
    spec = importlib.util.spec_from_file_location("_inbox_neg", fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    probe_dir = os.path.join(paths.INBOX_DIR, "internal")
    if not os.path.isdir(probe_dir):
        pytest.skip("投放区 internal/ 不存在")
    probe = os.path.join(probe_dir, "_pytest_probe.rtf")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("probe")
    try:
        res = mod.scan(apply=False)
        rows = [r for r in res["rows"] if r["file"].endswith("_pytest_probe.rtf")]
        assert rows and rows[0]["decision"] == "needs_review", rows
    finally:
        os.remove(probe)


# ---- P2-6 doctor ----


def test_doctor_checks_complete():
    """T3：数量不写死 18（方案 §3.13.4 的目标），断言 id 唯一 + quick ⊆ 全量。"""
    from commands import doctor
    ids = [c[0] for c in doctor.CHECKS]
    assert len(ids) >= 18, f"检查项少于方案要求的 18 项：{ids}"
    assert len(set(ids)) == len(ids), ids
    assert set(doctor.QUICK_IDS) <= set(ids)


def test_doctor_quick_subset():
    from commands import doctor
    res = doctor.run_checks(quick=True)
    assert res["checked"] == len(doctor.QUICK_IDS)
    assert {i["id"] for i in res["items"]} == set(doctor.QUICK_IDS)
    assert all(i["state"] in ("ok", "warn", "fail") for i in res["items"])


def test_run_step_order_matches_call_sites():
    """P2-6：`STEP_ORDER` 与 `_run("<step>")` 实际调用点一致（防清单漂移）。

    这是 `gate_config_integrity` 判据 R 的**前置断言**（该判据待接；此测试先守住一致性）。
    """
    import importlib.util  # noqa: PLC0415

    fp = os.path.join(paths.ROOT, "tools", "run_production_refresh.py")
    spec = importlib.util.spec_from_file_location("_rpr_test", fp)
    rpr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rpr)
    assert len(rpr.STEP_ORDER) == 17
    # 每个步骤名必须能在本文件中找到**引号字面量**（`"gates"` / `f"clean:{src}"` ⇒ `"clean:{src}"`）
    text = open(fp, encoding="utf-8").read()
    for step in rpr.STEP_ORDER:
        assert f'"{step}"' in text or f'"{step.split("{src}")[0]}' in text, step
