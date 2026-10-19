# -*- coding: utf-8 -*-
"""refactor 统一 CLI 入口（薄分发层）。

子命令按领域分散到各包，本文件只做路由：
  init        -> refactor.init
  （crawl / clean / docgraph 等后续接入各自包）

前置步骤本身在 refactor/init/ 目录内实现与扩展。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 仓库根入路径
_ROOT = Path(__file__).resolve().parent.parent
_ROOT_STR = str(_ROOT)
if _ROOT_STR in sys.path:
    sys.path.remove(_ROOT_STR)
sys.path.insert(0, _ROOT_STR)

import refactor.init as init_pkg


def main(argv=None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv or argv[0] != "init":
        print("用法: python -m refactor init [--db-url URL] [--no-dump]")
        return 1
    # 剥掉 "init"，把剩余参数交给 init 包解析
    return init_pkg.main(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
