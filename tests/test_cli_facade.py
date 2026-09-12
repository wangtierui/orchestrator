# -*- coding: utf-8 -*-
"""test_cli_facade.py —— cli 门面单测（审查 P1-2，2026-09-12）。

覆盖：命令注册完整性、未知命令 rc=1、help rc=0、analysis 帮助路径、
classify 的 --no-analysis 参数存在性（逃生阀防丢失）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cli  # noqa: E402


class TestCommandRegistry:
    def test_expected_commands_registered(self):
        for cmd in ("gates", "source", "internal", "classify", "timeliness",
                    "draft", "rfn", "base", "analysis", "ping"):
            assert cmd in cli.COMMANDS, f"命令 {cmd} 未注册"

    def test_parser_lists_commands(self):
        p = cli.build_parser()
        # 解析一个最小命令不抛错
        ns = p.parse_args(["analysis"])
        assert ns.command == "analysis"


class TestDispatch:
    def test_unknown_command_rc1(self, capsys):
        rc = cli.main(["no-such-command"])
        assert rc == 1
        assert "未知命令" in capsys.readouterr().out

    def test_help_rc0(self, capsys):
        rc = cli.main(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "analysis" in out and "gates" in out

    def test_analysis_no_action_usage(self, capsys):
        rc = cli.main(["analysis"])
        assert rc == 1
        assert "用法" in capsys.readouterr().out


class TestClassifyEscapeHatch:
    def test_no_analysis_flag_exists(self):
        """--no-analysis 为 classify 自动刷新交付库的逃生阀（F-L01 收口），防回归丢失。

        2026-09-13（审查 P3）：命令实现迁至 commands/ 包（cli.py 薄壳化），
        逃生阀断言改读 commands.classify.run 源码。
        注意：不得把 commands/ 自身插入 sys.path（gates/base 等模块名会与顶层包
        同名遮蔽，2026-09-13 实证 ImportError）。
        """
        import inspect

        from commands import classify as _cl  # noqa: PLC0415

        src = inspect.getsource(_cl.run)
        assert "--no-analysis" in src
        assert "gen_analysis_deliveries" in src
        # 薄壳保持注册（cli 仍暴露 analysis/classify）
        assert "classify" in cli.COMMANDS
