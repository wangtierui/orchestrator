# -*- coding: utf-8 -*-
"""commands.draft — orchestrator 命令：draft（自 cli.py 迁移，2026-09-13 审查 P3）。"""

from __future__ import annotations

import sys

import paths
from config.exitcodes import ExitCode


def run(argv):
    """draft [--ipn IPN-xxx] —— 条款级对照素材端到端编排（P8 收口）。
    输入 merged_view + internal processed（R21 clauses）；输出 drafter/data/draft_clause/。
    供六件套之「条款对照表/立法依据」起草打底；退出码 0=成功 1=数据缺（merged 未生成）。"""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    script = os.path.join(
        paths.ROOT, "modules", "internal_policy_drafter", "scripts", "build_draft_clause_view.py"
    )
    if not os.path.exists(script):
        print(f"[draft] 脚本缺失: {script}")
        return ExitCode.FAIL
    return subprocess.run([sys.executable, "-X", "utf8", script] + argv, timeout=3600).returncode
