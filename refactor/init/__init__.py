# -*- coding: utf-8 -*-
"""refactor 项目初始化（前置步骤）包。

不限于数据库：凡是"项目运行前的准备动作"都归此处。
目前含存储层 DDL（init/ddl.py）；后续可扩展环境检查、目录脚手架、
外部服务探活、向量库初始化等——届时直接在 init/ 内加模块即可。
"""
def __getattr__(name):
    """惰性暴露包级 API（避免 `python -m init` 时预导入 __main__ 触发 RuntimeWarning）。"""
    if name in ("cmd_init", "main"):
        from . import __main__ as _m
        return getattr(_m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")