# -*- coding: utf-8 -*-
"""
std_lib.common_lib.fs_lock — 跨进程 PID 文件锁 + 原子写（N-8 SSOT，P2 收口）

实现与旧仓 regulatory_scrapers/fs_lock.py **API 完全一致**（P2 收口目标：后续 gov/mof/nfra/
pbc collector 与 classifier 迁移代码 `from std_lib.common_lib import fs_lock` 后行为零差异）：
  - atomic_write_text / atomic_write_json（tempfile + os.replace 原子替换，Windows 安全）
  - ProcessLock（acquire/release bool API + 上下文管理器）
  - PidFileLock（=ProcessLock 别名，占用抛 BusyError 语义见 __enter__）
  - with_pid_lock 便捷入口

占用判定：锁文件存在且（PID 存活 且 未超 max_age_sec）→ 被占用；否则（PID 死/内容损坏/超龄）
→ 可抢占。路径一律由调用方经 paths 派生传入，本模块不硬编码任何绝对路径。
"""
from __future__ import annotations

import json
import os
import tempfile
import time


def atomic_write_text(path, text, encoding="utf-8", overwrite=True):
    """原子写文本：写同目录临时文件 + os.replace 替换目标（Windows 安全）。
    overwrite=False 且目标已存在 → 抛 FileExistsError，不覆盖。
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=os.path.splitext(path)[1] or ".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if not overwrite and os.path.exists(path):
            raise FileExistsError(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj, encoding="utf-8", ensure_ascii=False, indent=2, **json_kwargs):
    """原子写 JSON（默认 ensure_ascii=False, indent=2）。"""
    atomic_write_text(
        path,
        json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, **json_kwargs),
        encoding=encoding,
    )


class BusyError(RuntimeError):
    """锁被其他存活进程占用。"""


def _pid_alive(pid):
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _read_lock_pid(lock_path):
    try:
        with open(lock_path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class ProcessLock:
    """跨进程 PID 文件锁（N-8 核心实现，单一事实源）。

    双 API（同一套判定逻辑，避免各源各自实现）：
      - acquire() / release()：返回 bool，便于调度脚本「占用即 sys.exit(0)」；
      - with ProcessLock(...)：上下文管理器，占用时抛 BusyError。

    占用判定：锁文件存在且（内容可读 且 PID 存活 且 未超 max_age_sec）→ 被占用；
    否则（PID 已死 / 内容损坏 / 超龄）→ 判定陈旧可抢占。
    """

    def __init__(self, lock_path, max_age_sec=3 * 3600, own_pid=None):
        self.lock_path = lock_path
        self.max_age_sec = max_age_sec
        self.own_pid = own_pid or os.getpid()
        self._acquired = False

    def acquire(self):
        """尝试获取锁：成功 True；被其他存活进程占用 → False（不抛异常）。"""
        if os.path.exists(self.lock_path):
            pid = _read_lock_pid(self.lock_path)
            stale = False
            if pid is None:                    # 内容损坏 → 可抢占
                stale = True
            elif not _pid_alive(pid):           # PID 已死 → 可抢占
                stale = True
            else:
                try:
                    age = time.time() - os.path.getmtime(self.lock_path)
                except OSError:
                    age = 0
                if age > self.max_age_sec:      # 超龄 → 可抢占
                    stale = True
            if not stale:
                return False                    # 被占用
        os.makedirs(os.path.dirname(os.path.abspath(self.lock_path)), exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as fh:
            fh.write(str(self.own_pid))
            fh.flush()
            os.fsync(fh.fileno())
        self._acquired = True
        return True

    def release(self):
        """仅在锁归自身时移除锁文件（正常/异常退出均可调用，幂等）。"""
        if not self._acquired:
            return
        try:
            if _read_lock_pid(self.lock_path) == self.own_pid:
                os.remove(self.lock_path)
        except OSError:
            pass
        finally:
            self._acquired = False

    def __enter__(self):
        if not self.acquire():
            raise BusyError(f"锁被占用 lock={self.lock_path} pid={_read_lock_pid(self.lock_path)}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class PidFileLock(ProcessLock):
    """上下文管理器语义（占用抛 BusyError），与 ProcessLock 共享同一套实现。"""


def with_pid_lock(lock_path, max_age_sec=3 * 3600):
    """模块级便捷入口：返回 PidFileLock 上下文管理器。"""
    return PidFileLock(lock_path, max_age_sec=max_age_sec)


def atomic_write_csv_dict(path, rows, fieldnames, encoding="utf-8-sig"):
    """原子写 DictWriter CSV（UTF-8 BOM）。registry/consolidate 收口专用（P2 扩展，旧仓无）。"""
    import csv
    import io
    # 在内存组装后经 atomic_write_text 落盘，保证与旧仓一致的原子语义
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    atomic_write_text(path, buf.getvalue(), encoding=encoding)
    return len(rows)
