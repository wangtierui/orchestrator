# -*- coding: utf-8 -*-
"""tool registry — 能力注册表（与暴露层解耦，零依赖）。

每个能力 = 一个 ToolSpec：
  - name:          工具名（MCP tool name）
  - description:   给 Agent 看的能力说明
  - input_schema:  JSON Schema（对应 MCP tool.inputSchema）
  - handler:       「不依赖任何暴露框架」的纯函数 (args: dict) -> 可 JSON 序列化结果

registry 本身不 import mcp / fastapi / llama_index，因此：
  - 既能被 MCP server 读取并注册成 MCP tool；
  - 也能被 REST / 原生 Agent 复用同一份能力，互不绑架。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]


_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool 已注册: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def tool(name: str, description: str, input_schema: Dict[str, Any]):
    """装饰器：把函数注册为一个 tool。函数签名 (args: dict) -> Any。"""
    def deco(fn: Callable[[Dict[str, Any]], Any]) -> Callable[[Dict[str, Any]], Any]:
        register(ToolSpec(name=name, description=description,
                         input_schema=input_schema, handler=fn))
        return fn
    return deco


def get_tools() -> Dict[str, ToolSpec]:
    return dict(_REGISTRY)


def discover() -> Dict[str, ToolSpec]:
    """导入 tools 包下所有模块（触发 @tool 注册），再返回注册表。"""
    import importlib
    import pkgutil
    from . import tools as _tools_pkg
    for mod in pkgutil.iter_modules(_tools_pkg.__path__):
        importlib.import_module(f"{_tools_pkg.__name__}.{mod.name}")
    return get_tools()
