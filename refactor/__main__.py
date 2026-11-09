# -*- coding: utf-8 -*-
"""历史兼容层；独立项目的统一入口是 ``python -m refactorctl``。

以 refactor/ 为项目根时，顶层包是 collectors / clean / init / ...，而不是
``refactor``：``python -m refactor`` 与 ``python -m __main__`` 都不成立。
本文件只在被直接执行或显式 import 时做一次转发，不注入 sys.path。
"""
from refactorctl import main

if __name__ == "__main__":
    raise SystemExit(main())
