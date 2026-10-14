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
from typing import IO


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
    """跨平台存活探测（Windows 兼容 2026-09-09）。

    Windows 上 os.kill(pid, 0) 实为 TerminateProcess 语义，实测对部分 PID
    抛 OSError WinError 87（参数错误）→ 误判存活进程为死 → 锁被抢占/重入失效。
    改为 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 探测：
      - 返回句柄        → 进程存在 → True
      - error 5 (拒绝)  → 进程存在但无查询权 → True（保守，防误抢占）
      - error 87 (无效参数)/句柄空且非5 → 进程不存在 → False
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # GetLastError：5=拒绝访问（进程存在）→ True；87=参数错误（不存在）→ False
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        return err == 5
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:  # noqa: BLE001
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
            if pid is None:  # 内容损坏 → 可抢占
                stale = True
            elif not _pid_alive(pid):  # PID 已死 → 可抢占
                stale = True
            else:
                try:
                    age = time.time() - os.path.getmtime(self.lock_path)
                except OSError:
                    age = 0
                if age > self.max_age_sec:  # 超龄 → 可抢占
                    stale = True
            if not stale:
                return False  # 被占用
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


class WinFileLock:
    """msvcrt 阻塞式文件锁（Windows，兼容 rfn/registry 原 _lock 语义）。

    P4 收口：registry 的 msvcrt.locking 直用改为本类，使 gate_no_duplicate_libs
    不再检到业务仓直用 msvcrt。与原实现一致为「阻塞直到获取」：
      lock = WinFileLock(lock_file); lock.acquire(); try:... finally: lock.release()
    """

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        # N-73（2026-09-28）：显式可空文件句柄（`acquire()` 前为 None；原裸 `= None`
        # 使 mypy 把字段定型为 None → 下方 `fileno()` 与赋值双双报错）。
        self._fh: IO[str] | None = None

    def acquire(self):
        import msvcrt

        os.makedirs(os.path.dirname(os.path.abspath(self.lock_path)), exist_ok=True)
        self._fh = open(self.lock_path, "a+")
        msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        return self._fh

    def release(self):
        import msvcrt

        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


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


# W4（方案 §6.3.3）：.tmp 生命周期的**启动侧兜底**。
# 写入侧保证 = atomic_write_* 的 except 清理 + os.replace（正常路径零残留，实测全仓
# `.tmp_*` 0 个）；但进程在 mkstemp 与 replace **之间**被杀（断电/强杀/OOM）时临时
# 文件成孤儿——发布目录的单体原子写可达数百 MB（external_records.jsonl 289MB），
# 孤儿文件既占磁盘又无主。本函数在链启动时清理超过 max_age_hours 的残留，
# **删除前打印清单**（可审计）；apply=False 只披露不删。
_TMP_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "external",
        "graphify-out",
        "node_modules",
        ".venv",
        "venv",
        "archive",
        "corpus",
    }
)


def sweep_stale_tmp(root, *, max_age_hours: int = 24, apply: bool = False) -> dict:
    """清理超过 `max_age_hours` 的原子写临时文件（`.tmp_*` / `*.tmp`）。

    返回 {scanned_dirs, found, removed_bytes, failed, files[]}；found>0 时 files 为
    清单（相对路径 + 年龄 + 体积）。删除失败（占用中等）不抛错——记入 failed 披露。
    """
    import datetime

    now = datetime.datetime.now().timestamp()
    cutoff = now - max_age_hours * 3600
    files: list[dict] = []
    failed: list[str] = []
    removed_bytes = 0
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        scanned += 1
        dirnames[:] = [d for d in dirnames if d not in _TMP_SKIP_DIRS]
        for fn in filenames:
            if not (fn.startswith(".tmp_") or fn.endswith(".tmp")):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                continue  # 24h 内的临时文件可能是并发写入方正在用（链外采集）
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            if apply:
                try:
                    os.remove(fp)
                    removed_bytes += st.st_size
                except OSError as e:
                    failed.append(f"{rel}: {type(e).__name__}")
            files.append(
                {
                    "file": rel,
                    "age_hours": round((now - st.st_mtime) / 3600, 1),
                    "size": st.st_size,
                }
            )
    return {
        "scanned_dirs": scanned,
        "found": len(files),
        "removed_bytes": removed_bytes,
        "failed": failed,
        "files": files,
    }
