# -*- coding: utf-8 -*-
"""用户活动让路守卫（批 65 / W-AT 残余）——**确定性设计**（零打桩、零双模块依赖 ✓）。

注：`wait_for_quiet_window` 返回**实际等待的时间差**（两次 `time.monotonic()` 之差），
即使"零等待"也可能为 9e-07 之类的小量 ⇒ 断言一律用**容差**（`< 0.05`）而非全等 ✗。

为何不用 monkeypatch 内部函数：本仓 `sys.path` 同时含仓根与 `std_lib` ⇒ 同一文件存在**两份模块对象** ✗，
按 `sys.modules` 遍历打桩**实测未生效**（测试读到真实活动 ⇒ 等待 4.2s ✗）。故 API 提供 **`quiet_fn`
注入点**（缺省 = `is_user_quiet`）⇒ 判据可确定性注入 ✓；让路总开关仍按**真实路径**验证 ✓。
"""
from __future__ import annotations

import time

from std_lib.scraper_std import user_activity as ua


def test_fail_open_when_unavailable() -> None:
    """读不到活动信息 ⇒ 视为安静（立即继续）——让路**不得**成为单点故障 ✗。"""
    assert ua.is_user_quiet.__doc__ is not None
    t0 = time.monotonic()
    assert ua.wait_for_quiet_window(max_wait=5.0, quiet_fn=lambda q: True) < 0.05
    assert time.monotonic() - t0 < 1.0


def test_waits_while_active_and_respects_cap() -> None:
    """用户持续活跃 ⇒ 等待到**上限**后继续（"延迟"而非"停摆" ✓）。"""
    t0 = time.monotonic()
    waited = ua.wait_for_quiet_window(quiet=1.5, max_wait=0.6, poll=0.1, quiet_fn=lambda q: False)
    dt = time.monotonic() - t0
    assert waited >= 0.5, "应等待接近上限"
    assert dt < 3.0, "不得超过上限太久（退避必须有界 ✓）"


def test_no_wait_when_quiet() -> None:
    """用户已安静 ⇒ **零等待**（不进入轮询 ✓）。"""
    calls = {"n": 0}

    def _q(_quiet: float) -> bool:
        calls["n"] += 1
        return True

    t0 = time.monotonic()
    assert ua.wait_for_quiet_window(quiet=1.5, max_wait=5.0, quiet_fn=_q) < 0.05
    assert calls["n"] == 1, "安静时应只判一次即返回 ✓"
    assert time.monotonic() - t0 < 1.0


def test_disabled_by_env_uses_real_path(monkeypatch) -> None:
    """`RCO_YIELD_TO_USER=0` ⇒ 让路全关（**真实路径**，不注入判据 ✓）。"""
    monkeypatch.setenv("RCO_YIELD_TO_USER", "0")
    t0 = time.monotonic()
    assert ua.wait_for_quiet_window(quiet=1.5, max_wait=3.0) < 0.05
    assert time.monotonic() - t0 < 1.0


def test_idle_seconds_is_sane() -> None:
    """真实读数合理性：非负且不可能超过 24.8 天（49.7 天 tick 回绕处理 ✓）。"""
    v = ua.user_idle_seconds()
    if v is not None:
        assert 0.0 <= v < 0x7FFFFFFF / 1000.0


def test_doc_convert_wires_quiet_window() -> None:
    """接线守卫：`doc_to_docx` 必须在**启动 LibreOffice 之前**让路，顺序为
    快照 → 让路 → 窗口站转换（先后错位即为无效实现 ✗）。"""
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = open(_os.path.join(root, "std_lib", "scraper_std", "doc_convert.py"),
               encoding="utf-8").read()
    assert "wait_for_quiet_window" in src, "缺少让路等待接线"
    i_snap = src.find("clipboard_guard.snapshot()")
    i_wait = src.find("wait_for_quiet_window")
    i_run = src.find("win_station.isolated_run(")
    assert -1 < i_snap < i_wait < i_run, "顺序必须为：快照 → 让路等待 → 窗口站转换 ✗"
