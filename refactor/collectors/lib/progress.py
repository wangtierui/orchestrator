# -*- coding: utf-8 -*-
"""统一采集进度日志（四源共用唯一出口）。

职责边界：
  - ``CollectorPipeline`` 在列表分流的固定位置打点（扫描数 / new·known·dup 分流 /
    产出记录数 / ok·非 ok / 异常数），并负责节流、定时心跳、开始与结束汇总；
  - 各源只在自己的列表翻页处上报 **位置**：``ctx.progress.batch(label)`` 与
    ``ctx.progress.page(n, total)``。业务与解析逻辑不感知日志实现。
  - 共享层不因源差异开分支：四源都边翻页边 yield 条目，因此字段集合、
    速率口径、节流规则只有一套；源侧行为差异不得泄漏到这里。

输出形态（logger ``progress.<source>``，由 lib/cli.configure_logging 统一格式化）::

    [progress.pbc] START mode=incremental out=data/raw every_items=20 heartbeat=30s
    [progress.pbc] page page=3/12 batch=行政处罚 scan=120 ... elapsed=2m31s speed=0.80/s
    [progress.pbc] heartbeat page=3/12 batch=行政处罚 scan=120 ...
    [progress.pbc] done page=... ...

节流与心跳互相配合：每 ``every_items`` 条或每 ``interval`` 秒打一条进度行；
另起 daemon 线程每 ``interval`` 秒打一条心跳，因此卡在详情页/网络请求时也有输出。
间隔可用环境变量 ``REFACTOR_PROGRESS_INTERVAL``（秒）覆盖。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .fetch_status import derive_from_record, is_ok

_DEFAULT_INTERVAL = 30.0
_DEFAULT_EVERY_ITEMS = 20


def _env_interval() -> float:
    try:
        return max(float(os.environ.get("REFACTOR_PROGRESS_INTERVAL", _DEFAULT_INTERVAL)), 1.0)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}s"


def _short(value, limit: int = 36) -> str:
    """把当前条目（URL/标题）压成单行短标签，避免撑爆日志。"""
    text = " ".join(str(value).split())
    return text[:limit]


class NullProgress:
    """空实现：单测/手工构造 CollectorContext 时兜底，调用方无需判空。"""

    def start(self) -> None: pass
    def finish(self) -> None: pass
    def page(self, page, total=None) -> None: pass
    def batch(self, label) -> None: pass
    def scan(self, label=None) -> None: pass
    def branch(self, kind) -> None: pass
    def result(self, rec) -> None: pass
    def error(self) -> None: pass
    def snapshot(self, tag="progress") -> str: return ""


NULL_PROGRESS = NullProgress()


def progress_of(ctx):
    """从任意 ctx（含单测的假 ctx）取进度对象，缺失时回落到空实现。

    各源与其子采集器只经此函数上报页码/批次，不必感知 ctx 的具体类型。
    """
    return getattr(ctx, "progress", NULL_PROGRESS) or NULL_PROGRESS


class ProgressLogger:
    """线程安全的采集进度计数器 + 节流/心跳日志。"""

    def __init__(self, source: str, mode: str = "incremental", *,
                 out_dir: str = "", every_items: int = _DEFAULT_EVERY_ITEMS,
                 interval: float | None = None, heartbeat: bool = True):
        self.source = source
        self.mode = mode
        self.out_dir = out_dir
        self.every_items = max(int(every_items), 1)
        self.interval = float(interval if interval is not None else _env_interval())
        self._heartbeat_enabled = heartbeat and self.interval > 0
        self.log = logging.getLogger(f"progress.{source}")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()
        self._last_log = self._t0
        self._started = False
        self._finished = False
        self._page = None
        self._page_total = None
        self._batch = ""
        self._cur = ""
        self._c = dict(scan=0, new=0, known=0, dup=0, rec=0, ok=0, non_ok=0, err=0)

    # ---- 生命周期 ----
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._t0 = time.monotonic()
            self._last_log = self._t0
        self.log.info("START mode=%s out=%s every_items=%d interval=%gs",
                      self.mode, self.out_dir, self.every_items, self.interval)
        if self._heartbeat_enabled:
            self._thread = threading.Thread(
                target=self._heartbeat_loop, name=f"progress-{self.source}", daemon=True)
            self._thread.start()

    def finish(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._lock:
            if self._finished:
                return
            self._finished = True
            elapsed = time.monotonic() - self._t0
        self._emit("done", elapsed=elapsed)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._emit("heartbeat")

    # ---- 位置上报（各源调用） ----
    def batch(self, label) -> None:
        with self._lock:
            self._batch = _short(label, 24)
            self._page = None
            self._page_total = None
        self._emit("batch")

    def page(self, page, total=None) -> None:
        with self._lock:
            self._page = page
            if total:
                self._page_total = total
        self._emit("page")

    # ---- 计数打点（基类调用） ----
    def scan(self, label=None) -> None:
        with self._lock:
            self._c["scan"] += 1
            if label is not None:
                self._cur = _short(label)

    def branch(self, kind) -> None:
        # 节流点放在分流之后：保证进度行里 scan 与 new/known/dup 是同一批条目，
        # 不会出现「scan=5 new=4」这种半更新快照。
        now = time.monotonic()
        with self._lock:
            if kind in self._c:
                self._c[kind] += 1
            hit_items = self._c["scan"] % self.every_items == 0
            hit_time = now - self._last_log >= self.interval
        if hit_items or hit_time:
            self._emit("progress")

    def result(self, rec) -> None:
        # rec 是采集器原始输出，fetch_status 要等 canonicalize 入库时才补；
        # 用同一推导口径，保证日志的 ok 与最终落库状态一致。
        good = is_ok(derive_from_record(rec))
        with self._lock:
            self._c["rec"] += 1
            self._c["ok" if good else "non_ok"] += 1

    def error(self) -> None:
        with self._lock:
            self._c["err"] += 1

    # ---- 输出 ----
    def snapshot(self, tag="progress", elapsed: float | None = None) -> str:
        with self._lock:
            return self._snapshot_locked(tag, elapsed)

    def _snapshot_locked(self, tag: str, elapsed: float | None = None) -> str:
        elapsed = time.monotonic() - self._t0 if elapsed is None else elapsed
        c = self._c
        if self._page is None:
            page = "-"
        elif self._page_total:
            page = f"{self._page}/{self._page_total}"
        else:
            page = str(self._page)
        # 刚启动不足 1s 时不报速率，避免除出失真的巨大数字。
        speed = f"{c['rec'] / elapsed:.2f}/s" if elapsed >= 1 else "-"
        parts = [
            tag,
            f"page={page}",
            f"batch={self._batch or '-'}",
            f"scan={c['scan']}",
            f"new={c['new']} known={c['known']} dup={c['dup']}",
            f"rec={c['rec']} ok={c['ok']} non_ok={c['non_ok']} err={c['err']}",
            f"elapsed={_fmt_elapsed(elapsed)}",
            f"speed={speed}",
        ]
        if self._cur:
            parts.append(f"cur={self._cur}")
        return " ".join(parts)

    def _emit(self, tag: str, elapsed: float | None = None) -> None:
        with self._lock:
            line = self._snapshot_locked(tag, elapsed)
            self._last_log = time.monotonic()
        self.log.info("%s", line)
