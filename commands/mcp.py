# -*- coding: utf-8 -*-
"""commands.mcp — `cli.py mcp serve|tools|resources|selftest`（W2，v2 §4）。

薄壳纪律：实现全在 `mcp_server/`，本文件只做参数声明与 ExitCode 映射（与其余命令同构）。
"""
from __future__ import annotations


def run(argv):
    """mcp serve | tools | resources | selftest。"""
    from config.exitcodes import ExitCode

    action = argv[0] if argv else "tools"
    if action == "selftest":
        from mcp_server import transport_stdio

        return ExitCode.OK if transport_stdio.selftest() == 0 else ExitCode.FAIL
    if action == "tools":
        from mcp_server.registry import list_tools

        rows = list_tools()
        print(f"[mcp] 已注册 tool {len(rows)} 个：")
        for t in rows:
            print(f"  {t.name:<18} {t.access:<5} v{t.version}  {t.description[:56]}")
        return ExitCode.OK
    if action == "resources":
        from mcp_server.resources import list_resources

        for r in list_resources():
            print(f"  {r['uri']:<28} v{r['version']}  {r['description'][:48]}")
        return ExitCode.OK
    if action == "serve":
        from mcp_server.transport_stdio import serve

        no_auth = "--no-auth" in argv  # 仅本机调试用；默认强制 token（§4.4）
        if no_auth:
            print("[mcp] ⚠️ --no-auth：未校验 token（仅限本机一次性调试）")
        print(f"[mcp] stdio 服务启动（协议 {__import__('mcp_server.transport_stdio', fromlist=['x']).PROTOCOL_VERSION}）；Ctrl-C 退出")
        return serve(require_token=not no_auth)
    print("用法：orchestrator mcp {serve [--no-auth] | tools | resources | selftest}")
    return ExitCode.USAGE
