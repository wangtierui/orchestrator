# -*- coding: utf-8 -*-
"""refactor/collectors/base.py — 爬虫重构的共享契约与 I/O 工具。

设计原则：薄接口 + 共享 I/O，非深继承。
- `SourceCollector` 协议：每个源只需暴露 `source_id` 与 `collect(out_dir) -> master_path`，
  内部增量/diff/站点差异由各源自行实现（不强行统一）。
- 共享工具：`atomic_write` / `write_master_json` / 运行锁。
- 边界钉死：爬虫唯一产物 = `data/raw/{master}.json`，
  结构 `{source, captured_at, count, items}`（下游 clean 的输入契约，不可变）。
"""
import json
import logging
import os
import sys
from datetime import UTC, datetime

UTC = UTC
from typing import Protocol, runtime_checkable

logger = logging.getLogger("refactor.collectors")

# orchestrator 仓库根（base.py 位于 refactor/collectors/，上两级即仓库根）。
# 各源包据此解析 std_lib 导入引导与默认 data/raw 路径。
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@runtime_checkable
class SourceCollector(Protocol):
    source_id: str

    def collect(self, out_dir: str, **opts) -> str:
        """运行本源采集，返回写入的 master JSON 路径。"""
        ...


def atomic_write(path: str, text: str) -> None:
    """先写临时文件再原子替换，避免写入中途崩溃损坏已有结果。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_master_json(path: str, source: str, items: list) -> None:
    """钉死的 raw→clean 边界：所有源统一产出的 master 结构（clean 只读这个）。"""
    atomic_write(path, json.dumps({
        "source": source,
        "captured_at": datetime.now(UTC).isoformat(),
        "count": len(items),
        "items": items,
    }, ensure_ascii=False, indent=2))


def _load_fs_lock():
    root = REPO_ROOT
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from std_lib.common_lib import fs_lock
        return fs_lock
    except ImportError:
        return None


_LOCKS = {}


def acquire_lock(lock_path, max_age_sec=3 * 3600):
    fs_lock = _load_fs_lock()
    if fs_lock is None:
        return True  # 无锁库时退化为不锁（重构期容错）
    lk = fs_lock.ProcessLock(lock_path, max_age_sec=max_age_sec)
    if lk.acquire():
        _LOCKS[lock_path] = lk
        return True
    return False


def release_lock(lock_path):
    lk = _LOCKS.pop(lock_path, None)
    if lk is not None:
        lk.release()
