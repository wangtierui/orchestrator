# -*- coding: utf-8 -*-
"""gates.__main__ — `python -m gates` 入口（2026-10-13 问题项 P-4 修复）。

`gates/__init__.py:5` docstring 明载「运行方式：python -m gates 或 orchestrator gates」，
但 `__main__.py` 一直缺失 ⇒ 前者实际以「'gates' is a package and cannot be directly
executed」失败——文档声明的入口不可用（声明面执行者存在性缺口，同 D 类病灶）。

实现纪律：仅薄壳转发 `gates.main()`；判据与输出格式以 `gates/__init__.py` 为唯一事实源，
本文件不承载任何逻辑（避免第二事实源）。
"""

from gates import main

if __name__ == "__main__":
    raise SystemExit(main())
