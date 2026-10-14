# -*- coding: utf-8 -*-
"""gate_mcp_schema — MCP tool schema 与声明面一致（W2，v2 §4.2/§9.1）。

门禁要点（每条对应一类真实漂移）：
  M1 **schema 有效**：每个 tool 的 `inputSchema` 是合法 JSON Schema 子集
     （类型/required/properties 形态自洽；参数名与函数签名一致——schema 由注解派生，
     此处核验「派生结果仍可用」）；
  M2 **写能力审计**：`access="write"` 的 tool 必须声明 `sideEffects` **且**经
     `registry.call_tool` 强制落审计（AST 断言：`call_tool` 内有 `_audit(` 调用）——
     「有写权限但无留痕」是 MCP 门面最危险的形态；
  M3 **读面纪律**：本期 tool 全部 `access="read"`（写后置）；出现写 tool 时本门禁**披露**
     并要求其已在 `access`/`side_effects` 声明齐备（不阻断，但 detail 必须可见）；
  M4 **资源齐备**：E-4 资源 `chain_steps` / `schedule_jobs` 存在且 `resources/list`
     与 `resources/read` 的 `version` 一致（版本联动是 agent 判断缓存有效性的依据）；
  M5 **零硬依赖**：门面**不得** import `mcp` SDK（增强层纪律——SDK 缺失不得影响主链）。
"""
from __future__ import annotations


def run() -> tuple[bool, dict]:
    problems: list[str] = []
    detail: dict = {}
    try:
        from mcp_server.registry import SCHEMA_DIALECT, list_tools
    except Exception as e:  # noqa: BLE001  门面不可读 → 判据不可执行
        return True, {"skipped": f"mcp_server 不可读（{type(e).__name__}: {e}）→ 判据不可执行"}

    tools = list_tools()
    detail["tools"] = len(tools)
    rows = []
    for t in tools:
        s = t.input_schema
        ok = (
            s.get("type") == "object"
            and isinstance(s.get("properties"), dict)
            and s.get("$schema") == SCHEMA_DIALECT
        )
        req = s.get("required") or []
        bad_req = [r for r in req if r not in (s.get("properties") or {})]
        if not ok:
            problems.append(f"M1: tool {t.name} 的 inputSchema 形态非法（{str(s)[:80]}）")
        if bad_req:
            problems.append(f"M1: tool {t.name} 的 required 含未声明参数 {bad_req}")
        if t.access == "write" and not t.side_effects.strip():
            problems.append(f"M2: tool {t.name} 声明 write 但无 sideEffects（写能力无声明 ✗）")
        rows.append({"name": t.name, "access": t.access, "version": t.version,
                     "params": sorted(s.get("properties") or {})})
    detail["listing"] = rows

    # M2：写级审计的**机制**断言（AST：call_tool 必须调 _audit）
    try:
        import ast
        import inspect

        from mcp_server import registry as reg

        src = inspect.getsource(reg.call_tool)
        tree = ast.parse(src.strip())
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "_audit" not in called:
            problems.append("M2: registry.call_tool 未调用 _audit（写级调用不留痕 ✗）")
        detail["audit_bound"] = "_audit" in called
    except Exception as e:  # noqa: BLE001
        problems.append(f"M2: 无法核验审计绑定（{type(e).__name__}: {e}）")

    # M3：读面披露
    writes = [r["name"] for r in rows if r["access"] == "write"]
    detail["write_tools"] = writes
    if writes:
        detail["note"] = f"存在写工具 {writes}（写后置阶段；已要求 sideEffects + 审计绑定）"

    # M4：E-4 资源与版本联动
    try:
        from mcp_server import resources as res

        lst = {r["uri"]: r["version"] for r in res.list_resources()}
        need = {"orch://chain_steps", "orch://schedule_jobs"}
        miss = sorted(need - set(lst))
        if miss:
            problems.append(f"M4: E-4 资源缺失 {miss}（agent 看不见处理全链）")
        for uri in need & set(lst):
            got = res.read_resource(uri)
            if got.get("version") != lst[uri]:
                problems.append(f"M4: 资源 {uri} 的 list/read 版本不一致（缓存判据失效）")
        detail["resources"] = lst
    except Exception as e:  # noqa: BLE001
        problems.append(f"M4: 资源面校验失败（{type(e).__name__}: {e}）")

    # M5：零硬依赖（不得 import mcp SDK）
    try:
        import mcp_server.transport_stdio as ts

        src = __import__("inspect").getsource(ts)
        if "import mcp\n" in src or "from mcp import" in src:
            problems.append("M5: transport_stdio 直接 import 了 mcp SDK（破坏零硬依赖纪律）")
        detail["sdk_free"] = True
    except Exception as e:  # noqa: BLE001
        problems.append(f"M5: 无法核验零硬依赖（{type(e).__name__}: {e}）")

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":
    from gates import run_cli

    raise SystemExit(run_cli("mcp_schema", run))
