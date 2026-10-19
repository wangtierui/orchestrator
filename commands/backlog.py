# -*- coding: utf-8 -*-
"""commands.backlog — `cli.py backlog <sub>` 薄壳（诊断第 3 波能力的 CLI 入口）。

为什么需要这层壳（而不是让调度直接 `python -m tools.backlog_governance`）：
门禁 **S2** 要求 `schedule.yaml` 里每个作业的 `argv[0]` 是**仓内可解析目标**
（实测 `"python"` 会被解析成 `<-m>` 而报"目标不存在"）；且其余作业一律走
`cli.py <cmd>` ⇒ 统一入口便于单实例锁/退出码口径一致。

本模块**只做转发**，不含任何判据（判据唯一事实源在 `tools/backlog_governance`）。
退出码用受控枚举（`gate_runtime_hygiene` 的 ⑤ 要求 owned 层不得裸整数 return）。
"""
from __future__ import annotations

import sys

from config.exitcodes import ExitCode


def run(argv: list[str]) -> int:
    """转发到 `tools.backlog_governance.main`（参数原样透传）。

    退出码语义（与工具一致）：`OK`=未超阈值 / `FAIL`=触发告警 / `USAGE`=参数错。
    """
    from tools import backlog_governance as B

    try:
        return int(B.main(argv))
    except SystemExit as e:              # argparse 的 --help / 参数错误
        return int(e.code or ExitCode.OK)
    except Exception as e:              # noqa: BLE001  显式报错（不静默吞）
        print(f"[backlog] {type(e).__name__}: {e}", file=sys.stderr)
        return int(ExitCode.FAIL)
