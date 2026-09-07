# -*- coding: utf-8 -*-
"""
std_lib.common_lib.fs_lock — 文件锁与原子写（N-8 SSOT 收口，2026-09-08 移植自旧仓 fs_lock.py）

收口说明（专项三）：旧仓 repo 根 fs_lock.py（PidFileLock/ProcessLock/atomic_write_*）已被
gov/mof/nfra/pbc 与 classifier diff_timeliness 引用；rfn/registry.py 自带 msvcrt _lock、
mof_law_scraper 自写 _atomic_write 均属重复 → 新仓统一使用本模块（P2 收口）。
路径不硬编码：锁路径由调用方经 paths 派生后传入。
"""
from __future__ import annotations

import json
import os
import time


# --------------------------------------------------------------------------- #
# 原子写
# --------------------------------------------------------------------------- #
def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """tmp + os.replace 原子写文本；失败回退直写。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding) as fh:
        fh.write(text)
    try:
        os.replace(tmp, path)
    except OSError:
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)


def atomic_write_json(path: str, obj, encoding: str = "utf-8", ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=ensure_ascii, indent=2), encoding=encoding)


def atomic_write_csv_dict(path: str, rows: list[dict], fieldnames: list[str],
                          encoding: str = "utf-8-sig") -> None:
    """原子写 DictWriter CSV（UTF-8 BOM）。供 rfn/registry 与 consolidate 等收敛调用（P2）。"""
    import csv
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    try:
        os.replace(tmp, path)
    except OSError:
        with open(path, "w", encoding=encoding, newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)


# --------------------------------------------------------------------------- #
# 进程锁
# --------------------------------------------------------------------------- #
class PidFileLock:
    """PID 文件锁：存活检测 + max_age 陈旧抢占。"""

    def __init__(self, lock_file: str, max_age_sec: int = 6 * 3600):
        self.lock_file = lock_file
        self.max_age = max_age_sec

    def acquire(self) -> bool:
        if self._is_alive():
            return False
        os.makedirs(os.path.dirname(os.path.abspath(self.lock_file)) or ".", exist_ok=True)
        with open(self.lock_file, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}|{int(time.time())}")
        return True

    def release(self) -> None:
        try:
            os.remove(self.lock_file)
        except OSError:
            pass

    def _is_alive(self) -> bool:
        if not os.path.exists(self.lock_file):
            return False
        try:
            with open(self.lock_file, encoding="utf-8") as fh:
                pid_s, ts_s = fh.read().split("|")
            pid, ts = int(pid_s), int(ts_s)
        except Exception:
            # 格式损坏视为残留，可抢占
            return False
        if time.time() - ts > self.max_age:
            return False
        return _pid_alive(pid)


class ProcessLock(PidFileLock):
    """兼容别名：旧仓 fs_lock.ProcessLock 语义（见 gov scraper / weekly_refresh 引用）。"""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False
