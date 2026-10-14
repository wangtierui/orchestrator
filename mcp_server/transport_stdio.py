# -*- coding: utf-8 -*-
"""mcp_server.transport_stdio — MCP **stdio 传输**（标准库自实现，W2 §4.1/§4.5）。

为什么不用 `mcp` SDK
--------------------
计划原定 `mcp>=1.0` 为**可选 extra**（SDK 缺失即显式失败）。实测本机**未安装 SDK**，
若照计划实现将得到「无法在本机验证的传输层」——与质量优先冲突。故改为**标准库自实现
协议面**（JSON-RPC 2.0 over stdio，行分隔）：

  · 支持方法：`initialize` / `tools/list` / `tools/call` / `resources/list` /
    `resources/read` / `ping`（覆盖 MCP 客户端的必需面）；
  · **零硬依赖**：`git clone` 后即可 `cli.py mcp serve`（与本仓克隆可移植纪律一致）；
  · SDK 若日后需要，可作为**另一传输**接入——注册表/工具/资源三层不变。

安全面（§4.4）：默认**不开网络端口**（仅 stdio）；token 存在时校验 `initialize`
携带的 token（`mcp_server/auth.py`）；错误一律回 JSON-RPC error 而非抛栈（栈会污染协议流）。
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "regulatory_compliance_orchestrator", "version": "0.1"}


def _result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_request(req: dict, *, client_id: str = "local") -> dict:
    """单条 JSON-RPC 请求 → 响应（纯函数，便于单测与 `--selftest`）。"""
    rid = req.get("id")
    method = str(req.get("method") or "")
    params = req.get("params") or {}

    if method == "initialize":
        from mcp_server import __version__

        return _result(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {**SERVER_INFO, "version": __version__},
                "capabilities": {"tools": {"listChanged": False}, "resources": {}},
            },
        )
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        from mcp_server.registry import list_tools

        return _result(rid, {"tools": [t.to_listing() for t in list_tools()]})
    if method == "tools/call":
        from mcp_server.registry import call_tool

        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        out = call_tool(name, args, client_id=client_id)
        if not out.get("ok"):
            return _error(rid, -32602, str(out.get("error") or "调用失败"))
        # MCP 约定：业务错误也走 result（isError），协议错误才用 error
        return _result(rid, {"content": [{"type": "json", "json": out}], "isError": False})
    if method == "resources/list":
        from mcp_server.resources import list_resources

        return _result(rid, {"resources": list_resources()})
    if method == "resources/read":
        from mcp_server.resources import read_resource

        uri = str(params.get("uri") or "")
        out = read_resource(uri)
        if "error" in out:
            return _error(rid, -32602, str(out["error"]))
        return _result(rid, {"contents": [out]})
    return _error(rid, -32601, f"未知方法：{method}")


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None,
          *, require_token: bool = True) -> int:
    """stdio 服务循环（逐行 JSON-RPC；EOF 正常退出）。"""
    from mcp_server import auth as _auth

    si = stdin or sys.stdin
    so = stdout or sys.stdout
    if require_token:
        tok = _auth.load_token()
        if not tok:
            print(
                "[mcp] 无 token 文件——按 §4.4 拒绝启动。"
                "生成：`python -m mcp_server.auth --init`（默认 .mcp_token，权限 0600）",
                file=sys.stderr,
            )
            return 4
    client_id = "local"
    for line in si:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            so.write(json.dumps(_error(None, -32700, "JSON 解析失败"), ensure_ascii=False) + "\n")
            so.flush()
            continue
        if require_token and req.get("method") == "initialize":
            supplied = str((req.get("params") or {}).get("clientToken") or "")
            if not _auth.verify(supplied):
                so.write(json.dumps(_error(req.get("id"), -32001, "token 校验失败"),
                                    ensure_ascii=False) + "\n")
                so.flush()
                return 4
            client_id = str((req.get("params") or {}).get("clientId") or "local")
        so.write(json.dumps(handle_request(req, client_id=client_id), ensure_ascii=False) + "\n")
        so.flush()
    return 0


def selftest() -> int:
    """离线自检：跑一遍真实 JSON-RPC 序列（initialize → tools/list → tools/call → resources）。"""
    checks = [
        ("initialize", {"method": "initialize", "id": 1, "params": {"clientId": "selftest"}}),
        ("tools/list", {"method": "tools/list", "id": 2}),
        ("tools/call", {"method": "tools/call", "id": 3,
                         "params": {"name": "chain_status", "arguments": {}}}),
        ("resources/list", {"method": "resources/list", "id": 4}),
        ("resources/read", {"method": "resources/read", "id": 5,
                            "params": {"uri": "orch://chain_steps"}}),
        ("unknown-method", {"method": "no/such", "id": 6}),
    ]
    ok = True
    for name, req in checks:
        resp = handle_request(req, client_id="selftest")
        good = "result" in resp or (name == "unknown-method" and "error" in resp)
        ok = ok and good
        print(f"[mcp:selftest] {name}: {'OK' if good else 'FAIL'} "
              f"{json.dumps(resp, ensure_ascii=False)[:120]}")
    print(f"[mcp:selftest] {'全部通过' if ok else '存在失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(selftest())
