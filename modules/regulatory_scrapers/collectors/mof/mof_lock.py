# -*- coding: utf-8 -*-
"""mof/mof_lock — **运行锁**（单实例保护）。

为什么单独成文件（2026-10-20 第一百零五批）
------------------------------------------
**`_LOCKS` 必须与锁函数同处**（与 `gov_session` 的 `_RESP_TEXT` 同一类约束）：
`acquire_lock` 写入 `_LOCKS`、`release_lock` 从它取同一实例去释放
（`fs_lock.ProcessLock` 的 release 必须作用于 acquire 返回的**同一个对象**）。
把状态与函数拆到两个模块会造成"两处字典、释放不到原锁 ⇒ 锁永远泄漏"，
而这种缺陷**不会报错**，只会让第二次运行被自己的残留锁挡住。
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

import os

from std_lib.common_lib import fs_lock

_LOCKS: dict = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）


def _read_lock_pid(lock_path):
    """读取锁文件首段（PID）。格式：PID|ISO|token。失败返回 None。"""
    try:
        with open(lock_path, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return None
    try:
        return int(first.split("|")[0])
    except (ValueError, IndexError):
        return None


def _pid_alive(pid):
    """跨平台进程存活检测（Windows/Linux 均可用 os.kill(pid, 0)）。"""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def acquire_lock(lock_path, max_age_sec=3 * 3600):
    """基于 PID 存活校验的运行锁（实现统一委托 fs_lock.ProcessLock，N-8）。

    - 判定逻辑（PID 存活 + max_age 陈旧兜底）仅在 fs_lock 公共库一份，四源共用；
    - 锁存在且其中 PID 仍在运行 -> 返回 False（并发重复，调用方跳过本次）；
    - PID 已死 / 锁损坏 / 超龄 -> 覆盖抢占。

    注：保留本函数签名仅为兼容既有调用方与测试；新代码请直接用 fs_lock.ProcessLock。
    """
    lk = fs_lock.ProcessLock(lock_path, max_age_sec=max_age_sec)
    if lk.acquire():
        _LOCKS[lock_path] = lk
        return True
    return False


def release_lock(lock_path):
    """释放由 acquire_lock 获取的锁（幂等；未持有则无操作）。"""
    lk = _LOCKS.pop(lock_path, None)
    if lk is not None:
        lk.release()


__all__ = ['_LOCKS', '_pid_alive', '_read_lock_pid', 'acquire_lock', 'release_lock']
