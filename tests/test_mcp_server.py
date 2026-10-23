# -*- coding: utf-8 -*-
"""tests/test_mcp_server.py — MCP 门面（W2，v2 §4）：schema 派生 / 调用 / 资源 / 传输 / 鉴权。

纪律：与门禁 `gate_mcp_schema` 互补——门禁守「声明面」，本文件守「行为面」：
  · schema 由类型注解**自动派生**（改注解 → schema 变，杜绝手写副本）；
  · 参数校验（未知/缺必填/类型错/枚举错）→ agent 侧可读错误；
  · 写级 tool 强制审计（注入假写 tool 验证 `_audit` 被调）；
  · E-4 资源版本随内容变化（agent 判断缓存有效性的依据）；
  · stdio JSON-RPC 序列（initialize → tools/list → tools/call → resources/*）；
  · 鉴权：无 token 拒绝启动、错误 token 拒绝初始化。
"""
from __future__ import annotations

import io
import json
import os

import pytest

from mcp_server import auth, registry, transport_stdio

# ---- schema 自动派生 ----


def test_input_schema_derived_from_annotations():
    """入参 schema 来自类型注解（不是手写副本）：必填/默认/类型齐全。"""

    s = registry.get_tool("base_search").input_schema
    assert s["properties"]["text"] == {"type": "string"}
    assert "text" in s["required"]
    assert s["properties"]["limit"]["default"] == 10
    assert s["properties"]["base"]["default"] == "external"


def test_schema_changes_with_annotation(tmp_path, monkeypatch):
    """改注解 → schema 变（自动派生的核心证据）。"""
    @registry.tool("probe_schema_demo")
    def demo(a: str, b: int = 3) -> dict:
        """演示 tool。"""
        return {"a": a, "b": b}

    try:
        s = registry.get_tool("probe_schema_demo").input_schema
        assert s["properties"]["a"] == {"type": "string"}
        assert s["properties"]["b"] == {"type": "integer", "default": 3}
        assert s["required"] == ["a"]
    finally:
        registry._REGISTRY.pop("probe_schema_demo", None)


def test_write_tool_requires_side_effects():
    """access=write 必须声明 side_effects（无声明的写能力在注册期即拒）。"""
    with pytest.raises(ValueError, match="side_effects"):
        @registry.tool("probe_bad_write", access="write")
        def bad() -> dict:
            """缺 side_effects 的写 tool。"""
            return {}

    with pytest.raises(ValueError, match="side_effects"):
        @registry.tool("probe_bad_write2", access="write", side_effects="   ")
        def bad2() -> dict:
            """空白 side_effects。"""
            return {}


def test_duplicate_tool_name_rejected():
    """注册表是唯一事实源：重名即拒（不静默覆盖）。"""
    with pytest.raises(ValueError, match="重名"):
        @registry.tool("chain_status")
        def dup() -> dict:
            """重名 tool。"""
            return {}


def test_access_must_be_read_or_write():
    with pytest.raises(ValueError, match="access"):
        @registry.tool("probe_bad_access", access="admin")
        def bad() -> dict:
            """非法 access。"""
            return {}


# ---- 参数校验与调用 ----


def test_validate_arguments_rejects_unknown_and_missing():
    spec = registry.get_tool("base_search")
    with pytest.raises(ValueError, match="未知参数"):
        registry.validate_arguments(spec, {"text": "x", "nope": 1})
    with pytest.raises(ValueError, match="缺少必填"):
        registry.validate_arguments(spec, {})


def test_validate_arguments_type_and_range():
    spec = registry.get_tool("base_search")
    with pytest.raises(ValueError, match="类型"):
        registry.validate_arguments(spec, {"text": 123})
    # limit 是 int，bool 在 Python 里是 int 子类——显式拒绝（避免 agent 传 true 变 1）
    with pytest.raises(ValueError, match="类型"):
        registry.validate_arguments(spec, {"text": "x", "limit": True})


def test_call_unknown_tool_returns_error():
    out = registry.call_tool("no_such_tool", {})
    assert out["ok"] is False and "未知 tool" in out["error"]


def test_call_tool_success_shape():
    out = registry.call_tool("sources_list", {})
    assert out["ok"] is True and out["tool"] == "sources_list"
    assert isinstance(out["data"], list)


def test_write_tool_is_audited(monkeypatch):
    """写级 tool 调用后必须留痕（`_audit` 被调；摘要可对账）。"""
    seen: dict = {}

    def fake_log_audit(action, target, **kw):
        seen["action"] = action
        seen["target"] = target
        seen.update(kw)

    @registry.tool(
        "probe_write", access="write", side_effects="测试写：改内存字典"
    )
    def w(x: str) -> dict:
        """测试写 tool。"""
        return {"ok": True, "x": x}

    import std_lib.common_lib.governance_store as gs

    monkeypatch.setattr(gs, "log_audit", fake_log_audit)
    try:
        out = registry.call_tool("probe_write", {"x": "v"}, client_id="probe-client")
        assert out["ok"] is True
        assert seen["action"] == "mcp_tool:probe_write"
        assert seen["actor"] == "mcp:probe-client"
        assert out["audit_id"] == seen["target_key"]
    finally:
        registry._REGISTRY.pop("probe_write", None)


def test_read_tool_not_audited(monkeypatch):
    """读级 tool 不写审计（避免日志噪音）。"""
    called = {"n": 0}

    def fake_log_audit(*a, **k):
        called["n"] += 1

    import std_lib.common_lib.governance_store as gs

    monkeypatch.setattr(gs, "log_audit", fake_log_audit)
    registry.call_tool("sources_list", {})
    assert called["n"] == 0


# ---- E-4 资源 ----


def test_resources_list_has_e4_resources():
    from mcp_server.resources import list_resources

    uris = {r["uri"] for r in list_resources()}
    assert {"orch://chain_steps", "orch://schedule_jobs"} <= uris


def test_chain_steps_resource_reflects_layers():
    """E-4：chain_steps 派生自 STEP_LAYERS（带层/顺序/kind）。"""
    from mcp_server.resources import read_resource

    r = read_resource("orch://chain_steps")
    assert "error" not in r
    c = r["contents"]
    assert c["available"] is True
    # 第一百一十三批：32 → 33（新增 relations:ext，紧跟 relations:gen）
    assert c["count"] == 33
    first = c["steps"][0]
    assert {"order", "name", "layer", "kind", "depends_on", "produces"} <= set(first)
    assert first["layer"], "步骤必须带层归属"


def test_resource_version_changes_with_content(monkeypatch):
    """版本随内容变化（agent 判断缓存有效性的依据）。"""
    from mcp_server import resources as res

    v1 = res.read_resource("orch://schedule_jobs")["version"]
    v2 = res.read_resource("orch://schedule_jobs")["version"]
    assert v1 == v2, "同内容版本必须稳定"
    orig = res._schedule_jobs

    def patched():
        d = orig()
        d["patched"] = True
        return d

    monkeypatch.setitem(res._RESOURCES, "schedule_jobs", ("patched", patched))
    v3 = res.read_resource("orch://schedule_jobs")["version"]
    assert v3 != v1, "内容变化必须递增版本"


def test_list_and_read_versions_agree():
    from mcp_server.resources import list_resources, read_resource

    for r in list_resources():
        got = read_resource(r["uri"])
        assert got["version"] == r["version"], r["uri"]


def test_unknown_resource_error():
    from mcp_server.resources import read_resource

    assert "error" in read_resource("orch://nope")


# ---- stdio 传输（JSON-RPC）----


def test_jsonrpc_initialize_and_tools_list():
    r = transport_stdio.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["serverInfo"]["name"] == "regulatory_compliance_orchestrator"
    r2 = transport_stdio.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(r2["result"]["tools"]) >= 10


def test_jsonrpc_tools_call_wraps_result():
    r = transport_stdio.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "sources_list", "arguments": {}}}
    )
    assert r["result"]["isError"] is False
    assert r["result"]["content"][0]["type"] == "json"


def test_jsonrpc_unknown_method_is_protocol_error():
    r = transport_stdio.handle_request({"jsonrpc": "2.0", "id": 9, "method": "x/y"})
    assert r["error"]["code"] == -32601


def test_jsonrpc_bad_arguments_is_invalid_params():
    r = transport_stdio.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "base_search", "arguments": {}}}
    )
    assert r["error"]["code"] == -32602


def test_stdio_serve_roundtrip_and_bad_json():
    """真实 stdin → stdout 往返；坏 JSON 回协议错误而非抛栈。"""
    lines = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        "{not json",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
    ]) + "\n"
    out = io.StringIO()
    rc = transport_stdio.serve(io.StringIO(lines), out, require_token=False)
    assert rc == 0
    resp = [json.loads(x) for x in out.getvalue().strip().splitlines()]
    assert len(resp) == 3
    assert resp[1]["error"]["code"] == -32700


def test_selftest_passes():
    assert transport_stdio.selftest() == 0


# ---- 鉴权 ----


def test_token_missing_rejects_start(monkeypatch, tmp_path):
    monkeypatch.setenv("REG_ORCH_MCP_TOKEN_FILE", str(tmp_path / "none.json"))
    assert auth.load_token() == ""
    assert auth.verify("anything") is False


def test_token_verify_and_init(monkeypatch, tmp_path):
    p = tmp_path / "tok"
    monkeypatch.setenv("REG_ORCH_MCP_TOKEN_FILE", str(p))
    tok = auth.init_token("secret-value")
    assert tok == "secret-value"
    assert auth.load_token() == "secret-value"
    assert auth.verify("secret-value") is True
    assert auth.verify("wrong") is False
    assert auth.verify("") is False


def test_serve_requires_token_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("REG_ORCH_MCP_TOKEN_FILE", str(tmp_path / "none.json"))
    rc = transport_stdio.serve(io.StringIO(""), io.StringIO(), require_token=True)
    assert rc == 4, "无 token 必须拒绝启动（不得无认证裸奔）"


def test_zero_hard_dependency_no_sdk_import():
    """零硬依赖：门面不得 import mcp SDK（源码级断言）。"""
    import mcp_server.transport_stdio as ts

    src = open(ts.__file__, encoding="utf-8").read()
    assert "\nimport mcp\n" not in src
    assert "from mcp import" not in src


def test_mcp_package_not_in_required_deps():
    """pyproject 基础依赖不含 mcp（可选门面不得变成硬依赖）。"""
    import paths

    txt = open(os.path.join(paths.ROOT, "pyproject.toml"), encoding="utf-8").read()
    base = txt.split("[project.optional-dependencies]")[0]
    assert "mcp" not in base.lower(), "mcp 不得出现在基础依赖段"
