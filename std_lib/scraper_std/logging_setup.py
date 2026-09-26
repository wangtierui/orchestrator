# -*- coding: utf-8 -*-
"""
logging_setup.py —— 结构化日志（第十三节 可观测性）

规范要求：每条日志包含 timestamp / project_name / task_id / url / status_code /
elapsed_time / error_type。

实现：
  - setup_logging(log_dir, project_name, level, json_lines) ：初始化根 logger
  - JsonFormatter                                       ：JSON 行格式器
  - LogContext                                          ：上下文管理器，自动携带
    project_name / task_id / url 等公共字段；用于异常捕获与结构化记录
  - snapshot_failure(html, path, meta)                  ：异常页面 HTML 快照落盘
  - 日志文件按日期滚动（logs/2026-08-19.log）
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """JSON 行格式器：每条日志输出一行合法 JSON。"""

    def __init__(self, project_name: str = "", task_id: str = ""):
        super().__init__()
        self.project_name = project_name
        self.task_id = task_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "project_name": getattr(record, "project_name", None) or self.project_name,
            "task_id": getattr(record, "task_id", None) or self.task_id,
            "url": getattr(record, "url", None),
            "status_code": getattr(record, "status_code", None),
            "elapsed_time": getattr(record, "elapsed_time", None),
            "error_type": getattr(record, "error_type", None),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(
    log_dir: str,
    project_name: str = "",
    task_id: str = "",
    *,
    level: str = "INFO",
    json_lines: bool = True,
    console: bool = True,
) -> logging.Logger:
    """初始化根 logger：控制台 + 按日期滚动的文件双输出。"""
    os.makedirs(log_dir, exist_ok=True)
    today = _dt.date.today().strftime("%Y-%m-%d")
    log_path = os.path.join(log_dir, f"{today}.log")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 清掉重复 handler（重复调用 setup 时）
    for h in list(root.handlers):
        root.removeHandler(h)

    if json_lines:
        fmt = JsonFormatter(project_name, task_id)
    else:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    return root


def snapshot_failure(html: str, snapshot_dir: str, meta: dict[str, Any] | None = None) -> str:
    """
    异常页面 HTML 快照落盘（熔断/解析失败时调用）。
    返回快照文件路径。meta 中的 url 会被哈希后用于文件名去重。
    """
    import hashlib

    os.makedirs(snapshot_dir, exist_ok=True)
    url = (meta or {}).get("url", "")
    ident = hashlib.md5(url.encode("utf-8")).hexdigest()[:12] if url else "unknown"
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(snapshot_dir, f"fail_{ident}_{ts}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html or "<html><!-- empty snapshot --></html>")
        f.write(f"\n<!-- meta: {json.dumps(meta or {}, ensure_ascii=False)} -->")
    return path


class LogContext:
    """上下文管理器：在 with 块内自动向日志注入公共字段，并支持异常快照。"""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        project_name: str = "",
        task_id: str = "",
        url: str | None = None,
        snapshot_dir: str | None = None,
    ):
        self.logger = logger
        self._fields = {
            "project_name": project_name,
            "task_id": task_id,
            "url": url,
        }
        self._old = {}
        self.snapshot_dir = snapshot_dir

    def __enter__(self) -> LogContext:
        for k, v in self._fields.items():
            if v is None:
                continue
            self._old[k] = getattr(logging, "_log_context", {}).get(k)
        if not hasattr(logging, "_log_context"):
            logging._log_context = {}
        for k, v in self._fields.items():
            if v is not None:
                logging._log_context[k] = v
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.logger.error(
                "context error: %s: %s",
                exc_type.__name__,
                exc_val,
                exc_info=(exc_type, exc_val, exc_tb),
            )
        for k in self._fields:
            logging._log_context.pop(k, None)
        return False


class _ContextFilter(logging.Filter):
    """把 LogContext 中设置的字段注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = getattr(logging, "_log_context", {})
        for k, v in ctx.items():
            if not hasattr(record, k):
                setattr(record, k, v)
        return True


# 注册上下文过滤器（模块导入即生效，供 setup 后的 logger 使用）
_CONTEXT_FILTER = _ContextFilter()
logging.getLogger().addFilter(_CONTEXT_FILTER)


if __name__ == "__main__":  # 离线自检
    import tempfile

    td = tempfile.mkdtemp()
    setup_logging(td, "test_proj", "task_1", json_lines=True, console=False)
    log = logging.getLogger("scraper_std.logging_setup")
    log.info("hello %s", "world", extra={"url": "http://x", "status_code": 200})
    files = os.listdir(td)
    assert any(f.endswith(".log") for f in files), files
    with open(os.path.join(td, [f for f in files if f.endswith(".log")][0]), encoding="utf-8") as f:
        line = json.loads(f.readline())
    assert line["message"] == "hello world"
    assert line["project_name"] == "test_proj"
    p = snapshot_failure("<html>bad</html>", td, {"url": "http://x/1"})
    assert os.path.exists(p)
    print("[scraper_std.logging_setup] 离线自检通过")
