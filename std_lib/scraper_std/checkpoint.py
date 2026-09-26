# -*- coding: utf-8 -*-
"""
checkpoint.py —— 断点续抓与优雅退出（第十五节 紧急制动 + 第十八节 断点续抓）

能力：
  - 信号量捕获（SIGINT / SIGTERM）→ 优雅退出：完成当前 URL、保存已抓数据、
    状态写入 data/checkpoint.json；
  - 断点续抓：重新启动时读取 checkpoint.json，跳过已成功抓取的 URL；
  - GracefulRunner 上下文管理器：包装主循环，异常/信号均保证 checkpoint 落盘。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger("scraper_std.checkpoint")

_GRACE = object()  # 优雅退出哨兵


class Checkpoint:
    """
    基于 JSON 的断点状态。结构：
      {
        "schema": 1,
        "project": str,
        "updated_at": str,
        "done_urls": {url: {"ts":..., "status":...}},
        "failed_urls": {url: {"error":...}},
        "page_count": int,
      }
    """

    def __init__(self, path: str, project: str = ""):
        self.path = path
        self.project = project
        self.data: dict[str, Any] = {
            "schema": 1,
            "project": project,
            "updated_at": "",
            "done_urls": {},
            "failed_urls": {},
            "page_count": 0,
        }
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update({k: v for k, v in loaded.items() if k in self.data})
            except Exception as e:
                LOG.warning("断点文件解析失败，从零开始：%s", e)

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    # ---------------- 断点接口 ---------------- #
    def is_done(self, url: str) -> bool:
        return url in self.data["done_urls"]

    def mark_done(self, url: str, status: str = "ok") -> None:
        self.data["done_urls"][url] = {"ts": time.time(), "status": status}
        self.data["page_count"] = len(self.data["done_urls"])
        self.save()

    def mark_failed(self, url: str, error: str) -> None:
        self.data["failed_urls"][url] = {"ts": time.time(), "error": str(error)[:300]}
        self.save()

    def done_urls(self) -> set[str]:
        return set(self.data["done_urls"].keys())

    def pending(self, urls: list[str]) -> list[str]:
        done = self.done_urls()
        return [u for u in urls if u not in done]


class GracefulRunner:
    """
    优雅退出包装器：
      with GracefulRunner(checkpoint, on_interrupt=None) as runner:
          for url in urls:
              runner.check_grace()
              ... 执行抓取 ...
              checkpoint.mark_done(url)
    收到 SIGINT/SIGTERM → 设置 grace 标志；check_grace() 抛 _GRACE 哨兵
    由调用方捕获并跳出循环；退出时强制落盘。
    """

    def __init__(
        self, checkpoint: Checkpoint | None = None, on_interrupt: Callable[[], None] | None = None
    ):
        self.checkpoint = checkpoint
        self.on_interrupt = on_interrupt
        self.grace = False
        self._orig_handlers: dict[int, Any] = {}
        self._installed = False

    def __enter__(self) -> GracefulRunner:
        self._install()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._restore()
        if self.checkpoint:
            self.checkpoint.save()
        if exc_val is _GRACE:
            LOG.info("优雅退出完成（信号中断）")
            return True
        return False

    def _install(self) -> None:
        if self._installed:
            return

        def _handler(signum, frame):
            LOG.warning("收到信号 %s，进入优雅退出（完成当前 URL 后停止）", signum)
            self.grace = True
            if self.on_interrupt:
                try:
                    self.on_interrupt()
                except Exception as e:
                    LOG.error("中断回调失败：%s", e)

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
            try:
                self._orig_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass
        self._installed = True
        atexit.register(self._restore)

    def _restore(self) -> None:
        for sig, h in self._orig_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
        self._installed = False

    def check_grace(self) -> None:
        """调用方在每轮循环中调用；收到信号时抛 _GRACE 哨兵。"""
        if self.grace:
            raise _GRACE


if __name__ == "__main__":  # 离线自检
    import tempfile

    td = tempfile.mkdtemp()
    cp = Checkpoint(os.path.join(td, "checkpoint.json"), "test")
    cp.mark_done("http://a")
    cp.mark_failed("http://b", "timeout")
    cp2 = Checkpoint(os.path.join(td, "checkpoint.json"), "test")
    assert cp2.is_done("http://a")
    assert cp2.pending(["http://a", "http://c"]) == ["http://c"]
    with GracefulRunner(cp) as r:
        assert not r.grace
        r.check_grace()
    assert cp.data["page_count"] == 1
    print("[scraper_std.checkpoint] 离线自检通过")
