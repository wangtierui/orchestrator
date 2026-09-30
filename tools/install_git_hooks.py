# -*- coding: utf-8 -*-
"""tools.install_git_hooks — 版本化 git 钩子的**安装与核验**（N-164，2026-09-30）

**本文件只是 CLI 薄壳**：核验与安装逻辑在 `std_lib/common_lib/git_hooks.py`
（那样 CLI 与 `tools/audit_health.py` 才能共用**同一份**判据，且避免 mypy 把同一文件
解析为两个模块名 —— 详见该模块 docstring）。

用法
----
    python -m tools.install_git_hooks            # 安装/修复（幂等）
    python -m tools.install_git_hooks --check    # 只核验：rc=0 就绪；rc=ExitCode.FAIL 未就绪
"""

from __future__ import annotations

import io
import sys

# 引导：**必须经 `bootstrap`（唯一引导点，v2 §3.1.2）** —— `gate_import_bootstrap` 断言
# tools/ 层的 `sys.path.insert` **只减不增**，新代码不得自写插入。
# 运行方式相应为 `python -m tools.install_git_hooks`（仓根位于 sys.path[0] 才能 import bootstrap）。
from bootstrap import bootstrap

bootstrap("all")

from config.exitcodes import ExitCode
from std_lib.common_lib import git_hooks


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--check" in argv:
        problems = git_hooks.check()
        if problems:
            print("[FAIL] 版本化 git 钩子未就绪：")
            for x in problems:
                print(f"  - {x}")
            print("  处置：python -m tools.install_git_hooks")
            return int(ExitCode.FAIL)
        print("[OK] 版本化 git 钩子已就绪（core.hooksPath=.githooks；自动推送 + 图同步）")
        return int(ExitCode.OK)

    bad = git_hooks.install()
    if bad < 0:
        print(f"[FAIL] 未找到 {git_hooks.HOOKS_DIR_REL}/ —— 该目录应随仓入库")
        return int(ExitCode.ENV)
    print(f"[OK] core.hooksPath = {git_hooks.HOOKS_DIR_REL}")
    if bad:
        print("[FAIL] 仍未就绪：")
        for x in git_hooks.check():
            print(f"  - {x}")
        return int(ExitCode.FAIL)
    print("[OK] 钩子已就绪（自动推送 + 图同步）")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
