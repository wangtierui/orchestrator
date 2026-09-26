# -*- coding: utf-8 -*-
"""
commands/schedule — 调度事实源的查看 / 安装 / 校验（v2 §3.13.6，P2-6）

`config/schedule.yaml` 是调度唯一事实源；本命令是其操作面（不再让人手工建计划任务）：

    python cli.py schedule print      # 打印 crontab 片段（含手册生成段同源）
    python cli.py schedule list       # 列出任务名/时间/命令
    python cli.py schedule install    # 生成 Task Scheduler XML 并注册
    python cli.py schedule verify     # 比对已安装任务与 yaml（不一致 → rc=1）
    python cli.py schedule remove     # 删除本工具注册的任务
"""

from __future__ import annotations

import argparse
import sys

from bootstrap import bootstrap

ACTIONS = ("print", "list", "install", "verify", "remove")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py schedule", description="调度事实源操作面（v2 §3.13.6）"
    )
    ap.add_argument(
        "action",
        nargs="?",
        default="verify",
        choices=list(ACTIONS),
        help="print 打印 crontab | list 列任务 | install 注册 | verify 校验 | remove 删除",
    )
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bootstrap("all", include_tools=True)
    import install_schedule  # noqa: PLC0415

    if args.action == "install":
        return install_schedule.main(["--install"])
    if args.action == "remove":
        return install_schedule.main(["--remove"])
    if args.action == "print":
        return install_schedule.main(["--print"])
    if args.action == "list":
        return install_schedule.main(["--list"])
    return install_schedule.main(["--verify"])


def run(argv=None) -> int:
    return main(argv)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
