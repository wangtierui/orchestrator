# -*- coding: utf-8 -*-
"""web.sse — SSE（Server-Sent Events）Hub：服务端每 2s diff 快照，有变更才推送。

为什么是 SSE 而不是 WebSocket（§8.4）：单向推送足够，标准库可手写，断线自动降级
为前端轮询（前端已实现 `EventSource` 失败 → `setInterval` 兜底）。

为什么不做「每次变更都推」：DB 读有成本；`snapshots.CACHE` 已做 2s 防抖，本 Hub 只比较
**轻量指纹**（各 provider 的 `fetched_at` + 关键计数字段），变化才广播。
"""
from __future__ import annotations

import json
import queue
import threading
import time

from . import snapshots as S

#: 客户端断线重连间隔提示（前端 EventSource 自动重连，这里给的是事件级间隔）
HEARTBEAT_S = 15.0
#: 指纹 diff 间隔（§8.3：每 2s diff）
DIFF_INTERVAL_S = S.CACHE_TTL_S


def _fingerprint() -> str:
    """轻量指纹：运行台账/gate/worklist/新鲜度/告警的计数字段（避免整包 hash）。"""
    parts = []
    for fn in (S.run_health, S.gate_status, S.worklist_summary, S.freshness, S.alerts):
        d = fn()
        parts.append(f"{fn.__name__}:{d.get('available')}:"
                     f"{len(d.get('counts') or d.get('by_kind') or {})}:"
                     f"{d.get('run_id') or d.get('oldest_open_days') or d.get('count') or 0}")
    return "|".join(parts)


class SseHub:
    """极简广播中心：每个客户端一个队列；一个后台线程做 diff 与心跳。"""

    def __init__(self, interval: float = DIFF_INTERVAL_S) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._interval = interval
        self._last = ""
        self._thread: threading.Thread | None = None
        self._events_sent = 0

    # -- 客户端 ------------------------------------------------------------- #
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=8)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def events_sent(self) -> int:
        return self._events_sent

    # -- 广播 --------------------------------------------------------------- #
    def broadcast(self, event: str, payload: dict | None = None) -> None:
        """向所有客户端广播（队列满则丢最旧——前端有轮询兜底，不阻塞服务端）。"""
        msg = (event, json.dumps(payload or {}, ensure_ascii=False))
        with self._lock:
            targets = list(self._clients)
        for q in targets:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except queue.Empty:
                    pass
        self._events_sent += 1

    # -- 后台 diff 线程 ----------------------------------------------------- #
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                fp = _fingerprint()
                if fp != self._last:
                    self._last = fp
                    self.broadcast("changed", {"at": time.strftime("%H:%M:%S")})
                else:
                    self.broadcast("ping", {"n": self._events_sent})
            except Exception:  # noqa: BLE001 旁路：diff 线程永不让服务端死
                pass
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="web-sse", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


def format_sse(event: str, data: str) -> bytes:
    """按 SSE 线格式编码（含 `retry:` 提示，前端断线 3s 重连）。"""
    return (f"event: {event}\n"
            f"data: {data}\n"
            f"retry: 3000\n\n").encode()
