# -*- coding: utf-8 -*-
"""user_activity.py —— **用户活动感知（让路）**（批 65 / 遗留 W-AT 残余的针对性加固）。

## 为何需要（问题与已完成的处置）
用户症状：**在其他办公软件中复制粘贴失效或被抢占**。已完成的根治链：
  1. **批 61**：LibreOffice 转换改到**独立窗口站**（`win_station.py`）—— 对普通剪贴板操作有效，
     但实测 **LO 会自行挂回 `WinSta0`** ✗ ⇒ 单靠隔离不足；
  2. **批 61**：**剪贴板保全护栏**（`clipboard_guard.py`）—— 转换前快照、转换后**原样恢复**
     ⇒ 用户内容**永不丢失** ✓（但窗口期内**当场粘贴**仍可能落空 ✗ = W-AT 残余）；
  3. **批 64**：**内容哈希缓存** —— 同一 `.doc` 只转换一次（实测 102s/Δ=194 → 0.02s/Δ=0 ✓）
     ⇒ 窗口次数从 N 降为"每份新内容一次" ✓。
本模块补上**最后一段**：**用户正在用键鼠时不要开始转换** ✓ —— 直接对准"用户复制的**那一刻**被打断"这一实际场景，
而非笼统降频。

## 设计（质量优先的取舍）
| 决策 | 理由 |
|---|---|
| 用 `GetLastInputInfo`（**只读**系统 API）判断"距上次键鼠输入的秒数" | 零侵入：不挂钩、不注入、不读用户内容 ✓ |
| **只在启动转换前**等待（不在转换中打断） | LO 转换一旦开始无法安全中止（中止会遗留进程/半成品 ✗）；"不开始"是唯一安全的让路方式 ✓ |
| **等待上限**（默认 60s，`RCO_QUIET_MAX_WAIT` 可调） | 用户长时间连续敲键盘时不能无限阻塞流水线 ⇒ **退避上限**保证"延迟"而非"停摆" ✓ |
| **fail-open**：API 不可用/异常 ⇒ **立即继续** | 让路是体验优化，**绝不能**变成流水线的单点故障 ✗ |
| 与既有开关统一：`RCO_YIELD_TO_USER=0`（批 58 让路总开关） | 一处开关管全部让路行为（优先级、quiet 等待）⇒ 语义一致、易解释 ✓ |
| 49.7 天 tick 回绕处理（`& 0xFFFFFFFF`） | `GetTickCount` 每 49.7 天回绕 ⇒ 不处理会算出**巨值**导致永久等待 ✗（健壮性细节 ✓） |

## 公开 API
    user_idle_seconds() -> float | None       距上次输入的秒数（不可用 → None）
    is_user_quiet(quiet: float = 1.5) -> bool 用户是否已"安静"≥quiet 秒（不可用 → True，fail-open）
    wait_for_quiet_window(*, quiet, max_wait, poll) -> float  等待至安静；返回实际等待秒数
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable  # noqa: TC003（仅类型注解）
from typing import Any

IS_WIN = sys.platform.startswith("win")
#: 默认"安静"判据：距上次键鼠输入 ≥ 该秒数
QUIET_SECONDS_DEFAULT = 1.5
#: 默认等待上限（秒）；超过即放弃等待、按计划执行（保证"延迟"而非"停摆"）
MAX_WAIT_DEFAULT = 60.0
#: 轮询间隔（秒）
POLL_DEFAULT = 0.5


def _enabled() -> bool:
    """让路总开关（与批 58 的优先级让路共用同一环境变量 ⇒ SSOT ✓）。"""
    return os.environ.get("RCO_YIELD_TO_USER", "1").strip() not in ("0", "false", "False")


def user_idle_seconds() -> float | None:
    """距上次**键鼠输入**的秒数；不可用（非 Windows/API 失败）→ `None`。

    实现：`GetLastInputInfo` 取"上次输入时的 tick"，与 `GetTickCount` 相减；
    两者均为 32 位且 49.7 天回绕 ⇒ 用 `& 0xFFFFFFFF` 取无符号差 ✓（不处理会算出巨值 ✗）。
    """
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
        u32.GetLastInputInfo.restype = wintypes.BOOL
        k32.GetTickCount.restype = wintypes.DWORD
        if not u32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        now = int(k32.GetTickCount()) & 0xFFFFFFFF
        last = int(lii.dwTime) & 0xFFFFFFFF
        delta_ms = (now - last) & 0xFFFFFFFF          # 回绕安全的无符号差 ✓
        if delta_ms > 0x7FFFFFFF:                     # 异常大（>24.8 天）⇒ 视为不可用，避免误判
            return None
        return delta_ms / 1000.0
    except Exception:  # noqa: BLE001  任何异常 ⇒ 不可用（fail-open ✓）
        return None


def is_user_quiet(quiet: float = QUIET_SECONDS_DEFAULT) -> bool:
    """用户是否已安静 ≥ `quiet` 秒；**不可用 ⇒ True**（fail-open：绝不因读不到活动而阻塞 ✓）。"""
    if not _enabled():
        return True
    idle = user_idle_seconds()
    return True if idle is None else (idle >= quiet)


def wait_for_quiet_window(*, quiet: float = QUIET_SECONDS_DEFAULT,
                          max_wait: float | None = None,
                          poll: float = POLL_DEFAULT,
                          quiet_fn: Callable[[float], bool] | None = None) -> float:
    """等待至"用户安静"；返回**实际等待秒数**（0 表示无需等待）。

    · 关闭让路/读不到活动 ⇒ 立即返回 0 ✓；
    · 等待上限：`max_wait`（缺省取 env `RCO_QUIET_MAX_WAIT` 或 `MAX_WAIT_DEFAULT`）✓；
    · 超上限 ⇒ 返回已等待秒数并**继续执行**（延迟而非停摆 ✓）；
    · `quiet_fn` 为**测试注入点**（缺省 = `is_user_quiet`）：让判据可**确定性**注入，
      避免依赖 monkeypatch 内部函数（本仓双模块副本曾使其失效 ✗）。
    """
    if not _enabled():
        return 0.0
    if max_wait is None:
        try:
            max_wait = float(os.environ.get("RCO_QUIET_MAX_WAIT", "") or MAX_WAIT_DEFAULT)
        except ValueError:
            max_wait = MAX_WAIT_DEFAULT
    max_wait = max(0.0, float(max_wait))
    _quiet_fn = quiet_fn or is_user_quiet
    t0 = time.monotonic()
    while (time.monotonic() - t0) < max_wait:
        if _quiet_fn(quiet):
            return time.monotonic() - t0
        time.sleep(min(poll, max(0.05, max_wait - (time.monotonic() - t0))))
    return time.monotonic() - t0


def describe_state() -> dict[str, Any]:
    """当前状态摘要（供日志/诊断；`idle=None` 表示不可用）。"""
    idle = user_idle_seconds()
    return {"enabled": _enabled(), "idle_seconds": idle,
            "quiet": is_user_quiet(), "is_win": IS_WIN}


if __name__ == "__main__":  # 离线自检
    import json

    print(json.dumps(describe_state(), ensure_ascii=False, indent=1))
