# -*- coding: utf-8 -*-
"""mcp_server — 把各项能力（含 RAG）以 MCP 协议暴露给 Agent 的薄壳门面。

原则：本包不写业务逻辑。能力实现在 mcp_server/tools/*，且都是「不依赖 MCP 的纯函数」；
registry 是它们的注册表（name→schema→handler），可被 MCP / REST / 原生 Agent 复用。
"""
from __future__ import annotations

from .server import build_server, main
from .registry import ToolSpec, register, tool, get_tools, discover

__all__ = ["build_server", "main", "ToolSpec", "register", "tool", "get_tools", "discover"]
