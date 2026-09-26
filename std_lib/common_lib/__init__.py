# -*- coding: utf-8 -*-
"""std_lib.common_lib — 跨域通用层（fs_lock/io_atomic/norm/index_store/audit）。

P0 提供 fs_lock 与 io_atomic（自旧仓 fs_lock.py 与既有原子写实现移植）；
norm/index_store/audit 逐步补齐。
"""

from __future__ import annotations
