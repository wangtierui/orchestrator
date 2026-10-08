# -*- coding: utf-8 -*-
"""tests.test_gate_run_steps — N-152 防护的**回归测试**（2026-10-01 补：此前零覆盖）。

背景：2026-09-29 真实全流程中 `clean:gov` **超时被强杀**，而它**已写出的产物**随后被
`apply:gov`/`classify:all` 当作正常输入采用，`gates` 仍报"全部门禁通过"——失败被**无感吞掉**。
`gates/gate_run_steps.py` 是唯一的防线（凭"**产物 mtime 落在失败步骤时间窗内**"判定危险组合），
但此前**没有任何测试**。本文件用**临时目录 + monkeypatch**完全隔离地覆盖它的三层判据：

  ① 无台账 → **跳过并披露**（克隆环境不该 FAIL）；
  ② **危险组合**（失败 + 产物 mtime 在窗内）→ **FAIL**（G-2）；
  ③ 人工确认件（acknowledged.json）→ **降为 WARN**（留痕后不静默）；
  ④ 失败但产物不在窗内（历史产物）→ **不阻断**，仅 G-3 WARN。

为何必须隔离：`_product_of` 会扫描**真实** cleaned 目录，直接构造"危险组合"就得改真实产物的
mtime —— 那是事实源元数据，**不能碰**。故把 `paths.MODULES_DIR` / 台账路径指向 tmp。
"""
from __future__ import annotations

import json
import os
import time

import pytest

from gates import gate_run_steps as grs


def _setup(tmp_path, monkeypatch, *, steps: list, products: list, ack: dict | None = None):
    """构造隔离环境：临时 cleaned 目录 + 台账 + （可选）确认件。"""
    mods = tmp_path / "modules"
    cleaned = mods / "regulatory_scrapers" / "data" / "cleaned"
    cleaned.mkdir(parents=True)
    for name in products:
        p = cleaned / name
        p.write_text("x\n", encoding="utf-8")
        # 产物 mtime 与会话无关，显式设为"现在"，再由步骤时间窗决定是否落在窗内
        os.utime(p, None)
    monkeypatch.setattr(grs.paths, "MODULES_DIR", str(mods), raising=False)
    monkeypatch.setattr(grs.paths, "ROOT", str(tmp_path), raising=False)

    run_state = tmp_path / "run_state"
    run_state.mkdir(parents=True)
    monkeypatch.setattr(grs, "RUN_STATE", str(run_state))
    monkeypatch.setattr(grs, "LEDGER", str(run_state / "last_run_steps.json"))
    monkeypatch.setattr(grs, "ACK", str(run_state / "acknowledged.json"))
    (run_state / "last_run_steps.json").write_text(
        json.dumps({"run_at": "2026-09-30 23:40:55", "total_elapsed_s": 1.0, "steps": steps, "failed": steps},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    if ack is not None:
        (run_state / "acknowledged.json").write_text(json.dumps({"acked": ack}, ensure_ascii=False), encoding="utf-8")
    return cleaned


def _failed_step(window_covers_now: bool) -> dict:
    now = time.time()
    return {
        "step": "clean:gov",
        "rc": -1,
        "exit_code": "TIMEOUT",
        "skipped": False,
        # 时间窗覆盖"现在"（危险）或落在过去（安全）
        "started_at": (now - 10) if window_covers_now else (now - 7200),
        "ended_at": (now + 10) if window_covers_now else (now - 3600),
    }


def test_missing_ledger_is_skipped_with_disclosure(tmp_path, monkeypatch) -> None:
    """① 无台账 → **跳过并披露**（判据不可执行 ≠ 判据通过）。"""
    monkeypatch.setattr(grs.paths, "ROOT", str(tmp_path), raising=False)  # 同盘：避免 relpath 跨盘报错
    monkeypatch.setattr(grs, "RUN_STATE", str(tmp_path / "nope"))
    monkeypatch.setattr(grs, "LEDGER", str(tmp_path / "nope" / "last_run_steps.json"))
    ok, detail = grs.run()
    assert ok is True
    assert "无运行台账" in str(detail["run_steps"]["skipped"])


def test_dangerous_combo_fails(tmp_path, monkeypatch) -> None:
    """② 危险组合（失败步骤 + 产物 mtime 落在其时间窗内）→ **FAIL**（G-2）。"""
    _setup(tmp_path, monkeypatch, steps=[_failed_step(True)], products=["gov_cleaned_fixture.jsonl"])
    ok, detail = grs.run()
    assert ok is False, "失败步骤的产物已落盘且被下游采用 → 必须阻断"
    problems = " ".join(detail["problems"])
    assert "G-2" in problems and "clean:gov" in problems
    assert detail["run_steps"]["dangerous"][0]["step"] == "clean:gov"
    assert "gov_cleaned_fixture.jsonl" in detail["run_steps"]["dangerous"][0]["products"][0]


def test_acknowledged_downgrades_to_warning(tmp_path, monkeypatch) -> None:
    """③ 人工确认件（留痕）→ 降为 **WARN**，不阻断但仍披露。"""
    _setup(
        tmp_path,
        monkeypatch,
        steps=[_failed_step(True)],
        products=["gov_cleaned_fixture.jsonl"],
        ack={"clean:gov": {"reason": "已核对影响可控", "acked_at": "2026-10-01T09:00:00"}},
    )
    ok, detail = grs.run()
    assert ok is True, "已留痕确认后不应阻断"
    warns = " ".join(detail["run_steps"]["warnings"])
    assert "G-2" in warns and "人工确认" in warns


def test_failed_without_products_in_window_is_warning(tmp_path, monkeypatch) -> None:
    """④ 失败但产物 mtime **不在窗内**（＝上一轮的历史产物）→ 不阻断，仅 G-3 WARN。

    这正是 2026-09-30 实跑的情形：`clean:gov` 失败，但 gov cleaned 的 mtime 早于该步时间窗
    → 未被那次失败运行产生 → 无危险组合 → 门禁 rc=0 **是正确的**（不得误报）。
    """
    _setup(tmp_path, monkeypatch, steps=[_failed_step(False)], products=["gov_cleaned_fixture.jsonl"])
    ok, detail = grs.run()
    assert ok is True, "历史产物不应被误判为失败产物"
    assert detail["run_steps"]["dangerous"] == []
    assert "G-3" in " ".join(detail["run_steps"]["warnings"])


@pytest.mark.parametrize("step_name", ["clean:mof", "apply:nfra", "collect:pbc"])
def test_product_mapping_covers_kinds(tmp_path, monkeypatch, step_name: str) -> None:
    """`_product_of` 覆盖 `clean`/`apply`/`collect` 三类（确认/未知类不判定）。"""
    src = step_name.split(":")[1]
    mods = tmp_path / "modules"
    (mods / "regulatory_scrapers" / "data" / "cleaned").mkdir(parents=True)
    (mods / "regulatory_scrapers" / "data" / "raw").mkdir(parents=True)
    (mods / "regulatory_scrapers" / "data" / "raw" / f"{src}_laws.json").write_text("{}", encoding="utf-8")
    (mods / "regulatory_scrapers" / "data" / "cleaned" / f"{src}_cleaned_20260930.jsonl").write_text("x", encoding="utf-8")
    monkeypatch.setattr(grs.paths, "MODULES_DIR", str(mods), raising=False)
    got = grs._product_of(step_name)
    assert got, f"{step_name} 应映射到产物"
    # 未登记的步骤类 → 不判定（返回空，避免误报）
    assert grs._product_of("semantic:assist") == []
