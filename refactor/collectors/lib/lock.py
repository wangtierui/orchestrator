# -*- coding: utf-8 -*-
"""进程级文件锁（Unix fcntl）。自包含，无外部依赖。

用于爬虫并发互斥：同一 out_dir 同时只允许一个抓取进程。仅 Unix 可用
（本项目运行环境为 macOS / Linux）。
"""
import fcntl
import os


class ProcessLock:
    """基于 fcntl 的排他锁；锁文件内记录持有者 PID，便于过期回收。"""

    def __init__(self, lock_path, max_age_sec=3 * 3600):
        self.lock_path = lock_path
        self.max_age_sec = max_age_sec
        self._fd = None

    def _is_stale(self):
        """锁文件存在但记录进程已消亡 → 视为过期可抢。"""
        try:
            with open(self.lock_path, "r") as fh:
                pid = int((fh.read() or "").strip() or 0)
        except (OSError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return False  # 仍存活
        except ProcessLookupError:
            return True   # 进程不存在 → 过期
        except PermissionError:
            return False  # 无权限探活，保守视为被持有

    def acquire(self):
        d = os.path.dirname(self.lock_path)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.exists(self.lock_path) and self._is_stale():
            try:
                os.remove(self.lock_path)
            except OSError:
                pass
        try:
            self._fd = open(self.lock_path, "a+")
        except OSError:
            return False
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                self._fd.close()
            except OSError:
                pass
            self._fd = None
            return False
        self._fd.seek(0)
        self._fd.truncate()
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return True

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fd.close()
            except OSError:
                pass
            self._fd = None
