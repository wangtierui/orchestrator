# -*- coding: utf-8 -*-
"""commands/serve.py — **CLI 薄壳**：本地确认台启动（方案 §8）

分层纪律：命令只做参数解析与委派，实现全在 `web/`（与 `commands/run.py` 薄壳 +
`tools/run_production_refresh.py` 实现同构）。命令**不触发主链**（执行权归 run/计划任务，
§8.2.1 非目标第 1 条）。
"""
from __future__ import annotations

import argparse

from bootstrap import bootstrap

bootstrap("all")


def run(argv: list[str] | None = None) -> int:
    """`cli.py serve [--host] [--port] [--open]`。"""
    argv = list(argv if argv is not None else [])
    ap = argparse.ArgumentParser(
        prog="orchestrator serve",
        description="本地确认台（待办闭环 + 数据总览；loopback-only、无鉴权）",
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="绑定地址（默认回环；非回环会打印安全告警——控制台无鉴权）")
    ap.add_argument("--port", type=int, default=8765, help="端口（默认 8765，被占用时换端口）")
    ap.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印逐请求日志")
    a = ap.parse_args(argv)

    import web

    # `open_browser`/`verbose` 均显式透传（web.serve 的关键字参数名）
    return web.serve(host=a.host, port=a.port,
                    open_browser=a.open, verbose=a.verbose)
