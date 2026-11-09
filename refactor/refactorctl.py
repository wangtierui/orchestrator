# -*- coding: utf-8 -*-
"""refactor 独立项目的统一 CLI（项目根 = refactor/）。

所有命令都在 refactor/ 目录下执行：

    python -m refactorctl --help
    python -m refactorctl collect  --source gov
    python -m refactorctl clean    --project gov
    python -m refactorctl init     [--db-url URL] [--no-dump]
    python -m refactorctl stats    [--json]
    python -m refactorctl docgraph --db-url URL [--dry-run]
    python -m refactorctl rag      [...]
    python -m refactorctl mcp                       # stdio 服务，会阻塞

设计约束：
  - 不向 ``sys.path`` 注入任何目录；模块解析只依赖 cwd=refactor
    （见 pyproject.toml 的 ``pythonpath = ["."]``）。
  - 每个子命令**延迟导入**：某个子命令的可选依赖缺失，不影响其它子命令
    与 ``--help``。
  - 只做路由，不重复各包自己的 argparse 语义；各包原有的 ``python -m <包>``
    入口保持可用。
"""
from __future__ import annotations

import sys

_USAGE = """用法: python -m refactorctl <子命令> [...]

子命令:
  collect   采集      [--source all|pbc|nfra|gov|mof] [--mode ...] ...
  clean     清洗      --project gov|mof|nfra|pbc [--raw ...]
  init      建表      [--db-url URL] [--no-dump]
  stats     采集统计  [--phase collect] [--json]
  docgraph  引用图    --db-url URL [--dry-run] [--limit N]
  rag       RAG 入库  (参数见 python -m refactorctl rag --help)
  mcp       MCP 服务  (stdio，会阻塞)
"""


def _with_argv(fn, rest: list[str]) -> int:
    """把子命令参数交给只读 sys.argv 的 main()（传入前后恢复现场）。"""
    saved = sys.argv
    sys.argv = [saved[0], *rest]
    try:
        return fn() or 0
    finally:
        sys.argv = saved


def main(argv=None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv:
        print(_USAGE, end="")
        return 1
    if argv[0] in ("-h", "--help", "help"):
        print(_USAGE, end="")
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "collect":
        from collectors.__main__ import main as fn
        return _with_argv(fn, rest)
    if cmd == "clean":
        from clean.run_clean import main as fn
        return _with_argv(fn, rest)
    if cmd == "init":
        from init.__main__ import main as fn
        return fn(rest) or 0
    if cmd == "stats":
        from scripts.stats import main as fn
        return _with_argv(fn, rest)
    if cmd == "docgraph":
        from docgraph.extract import main as fn
        return fn(rest) or 0
    if cmd == "rag":
        from rag.connector import main as fn
        return fn(rest) or 0
    if cmd == "mcp":
        from mcp_server.server import main as fn
        return fn() or 0

    print(f"未知子命令: {cmd}\n{_USAGE}", end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
