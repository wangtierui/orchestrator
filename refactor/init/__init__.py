# -*- coding: utf-8 -*-
"""refactor 项目初始化（前置步骤）包。

不限于数据库：凡是"项目运行前的准备动作"都归此处。
目前含存储层 DDL（init/ddl.py）；后续可扩展环境检查、目录脚手架、
外部服务探活、向量库初始化等——届时直接在 init/ 内加模块即可。
"""
from .__main__ import cmd_init, main  # noqa: F401  — 暴露包级 API，供 `python -m refactor init` 路由调用