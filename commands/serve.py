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
    """`cli.py serve [--host] [--port] [--open] [--verbose] [--pid-file] [--log-file]
    [--single-instance]`。

    后三个是**开机自启动**用的（`tools/workbench_service.py` 的计划任务会传）：
      · `--single-instance` 同时只跑一个实例（计划任务每次登录都触发）；
      · `--log-file` 日志落盘（无控制台时唯一可查）；
      · `--pid-file` 锁路径（默认 data/state/workbench.lock）。
    """
    argv = list(argv if argv is not None else [])
    ap = argparse.ArgumentParser(
        prog="orchestrator serve",
        description="本地确认台（待办闭环 + 数据总览；loopback-only、无鉴权）",
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="绑定地址（默认回环；非回环会打印安全告警——控制台无鉴权）")
    ap.add_argument("--lan", action="store_true",
                    help="对公司内网开放（等价 --host 0.0.0.0）。**只读**："
                         "待办处置/留言处置/本地文件/宿主诊断/提交详情按来源 IP 拒绝"
                         "（见 web/policy.py）")
    ap.add_argument("--port", type=int, default=8765,
                    help="端口（默认 8765；被占用时有限递增换端口）")
    ap.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印逐请求日志")
    ap.add_argument("--pid-file", default="",
                    help="单实例锁路径（默认 data/state/workbench.lock）")
    ap.add_argument("--log-file", default="",
                    help="日志文件（缺省仅控制台；开机自启动**必须**给，否则无日志可查）")
    ap.add_argument("--single-instance", action="store_true",
                    help="单实例模式：已有实例在跑则本次直接退出（幂等）")
    a = ap.parse_args(argv)

    import web

    host = "0.0.0.0" if a.lan else a.host
    # `open_browser`/`verbose` 均显式透传（web.serve 的关键字参数名）
    return web.serve(host=host, port=a.port, open_browser=a.open,
                     verbose=a.verbose, pid_file=a.pid_file or None,
                     log_file=a.log_file or None, single_instance=a.single_instance)
