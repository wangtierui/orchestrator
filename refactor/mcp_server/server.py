# -*- coding: utf-8 -*-
"""MCP server 骨架 — 薄壳，只把 registry 里的 tool 暴露成 MCP 协议。

本文件不写任何业务逻辑；tool 实现都在 mcp_server/tools/*（且都是不依赖 MCP 的纯函数）。
mcp SDK 的 import 故意放在函数内（懒加载），这样 registry / tools 即使没装 mcp 也能被单测/复用。

当前 mcp SDK 的底层 Server 用 add_request_handler("tools/list" | "tools/call", fn) 注册
（旧版 @server.list_tools() 装饰器已废弃），handler 直接返回 mcp.types 的结果对象。
"""
from __future__ import annotations

import asyncio
import json


def build_server():
    from mcp.server import Server
    from mcp.types import (
        Tool,
        TextContent,
        ListToolsResult,
        CallToolResult,
        PaginatedRequestParams,
        CallToolRequestParams,
    )
    from .registry import discover

    specs = discover()
    server = Server("refactor-regulatory")

    # 本 SDK 调用约定：handler(ctx, typed_params)
    async def _list_tools(ctx, params):
        tools = [
            Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
            for s in specs.values()
        ]
        return ListToolsResult(tools=tools)

    async def _call_tool(ctx, params):
        name = params.name
        arguments = getattr(params, "arguments", None) or {}
        spec = specs.get(name)
        if spec is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"未知 tool: {name}")],
                isError=True,
            )
        # handler 是同步阻塞（embedding / PG / LLM），丢线程池避免卡事件循环
        result = await asyncio.to_thread(spec.handler, arguments)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, default=str),
                )
            ]
        )

    server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)
    return server


def main():
    import mcp.server.stdio
    server = build_server()

    async def _run():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
