# -*- coding: utf-8 -*-
"""
common_lib.logging — 日志设施统一入口（v2 §3.6 X3，2026-09-26）

背景（v2 §2.4.3）
----------------
本仓已有**统一日志设施** `scraper_std.logging_setup`（`setup_logging` + `JsonFormatter`
+ `LogContext` + `snapshot_failure` + 按日滚动），但**生产脚本全部用 `print`**：
`tools/extract_relations.py` / `tools/governance_sync.py` / `apply_timeliness_to_cleaned.py`
合计 30+ 处，`logging` 0 处 → 编排器只能靠 `subprocess` 的 stdout/stderr tail 判失败，
**结构化日志在主链上完全未启用**。

本模块职责
----------
1. **re-export** 既有实现（不重造轮子、不做第二次实现）；
2. `get_logger()`：取模块级 logger 的惯用入口；
3. `setup_cli_logging()`：CLI 场景的默认配置——**人类可读 → stdout**（保留既有可读输出），
   `json_lines=True` 或环境 `REG_ORCH_JSON_LOGS=1` → **JSON lines**（机器可读运行审计）。

使用约定（P1-5 起）
------------------
- 状态/进度/告警行 → `LOG.info` / `LOG.warning`（进日志文件与 JSON 轨）；
- **机器可读载荷**（如 `json.dumps` 汇总、多列表格行）→ **保留 `print`**：它们是 stdout
  契约，加日志前缀会破坏下游可解析性（编排器 `_run` 会 tail stdout 判定）；
- 纪律由 `gate_runtime_hygiene` 判据④断言（生产脚本须有 LOG 使用下限，print 只减不增）。
"""
from __future__ import annotations

import logging
import os
import sys

# ---- re-export：唯一实现仍在 scraper_std.logging_setup（本模块不复制实现）----
from scraper_std.logging_setup import (  # noqa: F401
    JsonFormatter,
    LogContext,
    setup_logging,
    snapshot_failure,
)

__all__ = ["JsonFormatter", "LogContext", "setup_logging", "snapshot_failure",
           "get_logger", "json_logs_enabled", "setup_cli_logging"]

JSON_LOGS_ENV = "REG_ORCH_JSON_LOGS"


def get_logger(name: str = "") -> logging.Logger:
    """取 logger（生产脚本统一入口；`name` 传 `__name__` 即可）。"""
    return logging.getLogger(name or "orchestrator")


def json_logs_enabled(json_logs: bool | None = None) -> bool:
    """是否启用 JSON lines：显式参数优先，其次环境变量 `REG_ORCH_JSON_LOGS`。"""
    if json_logs is not None:
        return bool(json_logs)
    return os.environ.get(JSON_LOGS_ENV, "").strip() not in ("", "0", "false", "False")


def setup_cli_logging(name: str = "", *, log_dir: str = "", json_logs: bool | None = None,
                      level: str = "INFO") -> logging.Logger:
    """CLI/脚本的日志初始化（幂等；重复调用会重置 handler）。

    - 默认（未显式要求 JSON 且环境未置位）→ 人类可读格式写 **stdout**（保留 CLI 可读输出，
      与改造前的 `print` 观感接近：`时间 级别 名称 消息`）；
    - `json_logs=True` 或 `REG_ORCH_JSON_LOGS=1` → `JsonFormatter`（机器可读运行审计）；
    - 同时按日滚动写文件（`log_dir` 缺省为 `<仓根>/reports/_tmp/logs`）。
    """
    if not log_dir:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_dir = os.path.join(root, "reports", "_tmp", "logs")
    return setup_logging(log_dir, project_name="regulatory_compliance_orchestrator",
                         task_id=name or "cli",
                         level=level, json_lines=json_logs_enabled(json_logs), console=True)


def fatal(msg: str, code: int = 2) -> int:
    """记录错误日志并返回退出码（供脚本 `return fatal(...)` 惯用；避免裸整数语义分歧）。"""
    get_logger("fatal").error("%s", msg)
    if not sys.stderr.isatty():   # 重定向场景下同时落 stderr，保证编排器 tail 可见
        print(msg, file=sys.stderr, flush=True)
    return code
