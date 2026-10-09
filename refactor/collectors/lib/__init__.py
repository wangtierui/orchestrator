# -*- coding: utf-8 -*-
"""refactor/collectors 自带的极小公共库（锁 / 缓存 / 路径）。

设计约束：本目录及上层 collectors 只依赖 Python 标准库与本包，不得 import
仓库外的 std_lib / modules / config——保证 refactor/ 是可独立运行的项目。
"""
