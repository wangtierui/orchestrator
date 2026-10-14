# -*- coding: utf-8 -*-
"""mcp_server.registry — tool 注册表 + **schema 自动生成**（W2，v2 §4.2）。

为什么 schema 必须**自动生成**（而非手写）
----------------------------------------
手写 schema 与实现分离 → 改函数忘改 schema ⇒ agent 侧拿到过期契约（**第二事实源**，
与 D-4~D-6「文档说一套、实装另一套」同构）。本模块从**类型注解 + docstring + 契约常量**
派生 JSON Schema（draft 2020-12），并由 `gates/gate_mcp_schema` 断言与
`interfaces/contract.py` / `STEP_LAYERS` 派生一致——手写必漂移，自动生成 + 门禁才闭环。

零硬依赖纪律
------------
**不 import `mcp` SDK**（本仓 `semantic` 层同款纪律：可选增强缺失不得影响主链）。
协议面（tools/list、tools/call、resources/*）由 `transport_stdio` 用标准库实现，
SDK 若日后需要可作为**可选传输**接入，不改注册表与工具层。

访问级别与审计
--------------
`access="read"`（默认）只读消费面；`access="write"` 必须声明副作用面（`side_effects`），
且 `call_tool` **强制落审计**（actor=`mcp:<client_id>`，进 `governance.db.audit_log`）——
「声明面有写权限」与「执行面留痕」是同一枚硬币。
"""
from __future__ import annotations

import inspect
import json
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: JSON Schema 方言（与 MCP `tools/list` 的 inputSchema 一致）
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_JSON_TYPES: dict[Any, dict] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list: {"type": "array"},
    dict: {"type": "object"},
}


def _schema_for(ann: Any) -> dict:
    """类型注解 → JSON Schema 子集（str/int/float/bool/list/dict/Optional/字面量）。"""
    if ann is inspect.Parameter.empty or ann is Any:
        return {}
    origin = typing.get_origin(ann)
    if origin is typing.Union or str(origin) == "types.UnionType":
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        sub = _schema_for(args[0]) if args else {}
        if len(args) > 1:
            sub = {"anyOf": [_schema_for(a) for a in args]}
        return sub
    if origin in (list, set, tuple):
        args = list(typing.get_args(ann))
        item = _schema_for(args[0]) if args else {}
        return {"type": "array", **({"items": item} if item else {})}
    if origin is dict:
        args = list(typing.get_args(ann))
        val = _schema_for(args[1]) if len(args) == 2 else {}
        return {"type": "object", **({"additionalProperties": val} if val else {})}
    if ann in _JSON_TYPES:
        return dict(_JSON_TYPES[ann])
    if isinstance(ann, str) and ann.startswith("Literal["):
        # Literal["a", "b"] → enum（受控值的机器可读形态）
        vals = [x.strip().strip("\"'") for x in ann[len("Literal["):].rstrip("]").split(",")]
        return {"enum": [v for v in vals if v]}
    if isinstance(ann, type) and issubclass(ann, str):
        return {"type": "string"}
    return {}


@dataclass(frozen=True)
class ToolSpec:
    """一个 tool 的声明面（**机器可读**，直接喂 `tools/list`）。"""

    name: str
    description: str
    access: str                      # read | write
    fn: Callable[..., Any]
    input_schema: dict
    output_schema: dict
    version: str = "1"
    side_effects: str = ""           # access=write 必填（审计与门禁断言）
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_listing(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "version": self.version,
            "access": self.access,
            **({"sideEffects": self.side_effects} if self.side_effects else {}),
            **({"tags": list(self.tags)} if self.tags else {}),
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    _name: str | None = None,
    *,
    access: str = "read",
    version: str = "1",
    output: dict | None = None,
    side_effects: str = "",
    tags: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册一个 tool（`@tool` / `@tool("name", access="write")`）。

    · 入参 schema 从**类型注解**派生，参数默认值转 `default`；
    · 描述取 docstring 首段（docstring 即人读契约，不另写 description 副本）；
    · `access="write"` 必须给 `side_effects`（否则注册即拒——防止「无声明的写能力」）。
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = _name or fn.__name__
        if access not in ("read", "write"):
            raise ValueError(f"tool {name}: access 须为 read|write（得到 {access!r}）")
        if access == "write" and not side_effects.strip():
            raise ValueError(f"tool {name}: access=write 必须声明 side_effects")
        sig = inspect.signature(fn)
        # `from __future__ import annotations` ⇒ 运行期注解是**字符串**，须先解析为类型
        #   （否则 schema 恒空——「有 schema 但全是 {}」比没有 schema 更危险）。
        try:
            hints = typing.get_type_hints(fn)
        except Exception:  # noqa: BLE001  解析失败（如前向引用不可解）→ 退回原注解
            hints = {}
        props: dict = {}
        required: list[str] = []
        for pname, p in sig.parameters.items():
            if pname in ("self", "cls") or p.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            ann = hints.get(pname, p.annotation)
            s = _schema_for(ann)
            if p.default is not inspect.Parameter.empty:
                s["default"] = p.default
            else:
                required.append(pname)
            props[pname] = s
        input_schema: dict = {"$schema": SCHEMA_DIALECT, "type": "object", "properties": props}
        if required:
            input_schema["required"] = required
        doc = (inspect.getdoc(fn) or "").strip()
        spec = ToolSpec(
            name=name,
            description=doc.split("\n\n")[0].replace("\n", " ").strip() or name,
            access=access,
            fn=fn,
            input_schema=input_schema,
            output_schema=output or {"type": "object"},
            version=version,
            side_effects=side_effects,
            tags=tags,
        )
        if name in _REGISTRY:
            raise ValueError(f"tool 重名：{name}（注册表是唯一事实源，不允许覆盖）")
        _REGISTRY[name] = spec
        return fn

    return deco


def _ensure_tools_loaded() -> None:
    """惰性导入 tool 定义模块（装饰器注册的固有陷阱：模块未导入 ⇒ 注册表为空）。

    在**唯一入口**（list_tools / call_tool / get_tool）兜住，避免"忘了 import 就静默空表"。
    """
    import importlib

    importlib.import_module("mcp_server.tools")


def list_tools() -> list[ToolSpec]:
    """全部已注册 tool（按名排序，保证 `tools/list` 顺序稳定可测）。"""
    _ensure_tools_loaded()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def get_tool(name: str) -> ToolSpec:
    _ensure_tools_loaded()
    if name not in _REGISTRY:
        raise KeyError(name)
    return _REGISTRY[name]


def validate_arguments(spec: ToolSpec, arguments: dict) -> dict:
    """按 `input_schema` 校验实参（**类型/必填/枚举**）——错误信息面向 agent 可读。"""
    props = spec.input_schema.get("properties") or {}
    required = set(spec.input_schema.get("required") or [])
    unknown = sorted(set(arguments) - set(props))
    if unknown:
        raise ValueError(f"未知参数 {unknown}；可用：{sorted(props)}")
    missing = sorted(required - set(arguments))
    if missing:
        raise ValueError(f"缺少必填参数 {missing}")
    out: dict = {}
    for k, v in arguments.items():
        s = props.get(k) or {}
        want = s.get("type")
        _TYPE_MAP: dict[str, type] = {
            "string": str, "integer": int, "number": float,
            "boolean": bool, "array": list, "object": dict,
        }
        py = _TYPE_MAP.get(want or "")
        if want == "number":
            ok_num = isinstance(v, (int, float)) and not isinstance(v, bool)
            if not ok_num:
                raise ValueError(f"参数 {k} 类型应为 number（得到 {type(v).__name__}）")
        elif py and not isinstance(v, py):
            raise ValueError(f"参数 {k} 类型应为 {want}（得到 {type(v).__name__}）")
        if want in ("integer", "number") and isinstance(v, bool):
            # bool 是 int 子类：显式拒绝，否则 agent 传 true 被静默当 1（校验形同虚设）
            raise ValueError(f"参数 {k} 类型应为 {want}，不接受 bool")
        if "enum" in s and v not in s["enum"]:
            raise ValueError(f"参数 {k} 取值须 ∈ {s['enum']}（得到 {v!r}）")
        out[k] = v
    return out


def call_tool(name: str, arguments: dict | None = None, *, client_id: str = "local") -> dict:
    """统一调用入口：校验 → 执行 → **写级审计** → 结构化返回。

    返回 `{ok, data|error, audit_id}`；异常一律转成可读 error（不把栈打到 stdout，
    传输层是 JSON-RPC，栈会污染协议流）。
    """
    from config.exitcodes import ExitCode

    try:
        spec = get_tool(name)
    except KeyError:
        return {"ok": False, "error": f"未知 tool：{name}"}
    try:
        args = validate_arguments(spec, arguments or {})
    except ValueError as e:
        # 校验失败也是**协议内**的业务错误：必须转成 {ok:False}，否则异常会击穿传输层
        # （W2 实测：tools/call 参数缺失导致 JSON-RPC 无响应 ✗）。
        return {"ok": False, "error": str(e), "tool": name}
    audit_id = ""
    try:
        data = spec.fn(**args)
    except Exception as e:  # noqa: BLE001  工具异常转协议错误（不中断 server）
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "tool": name}
    if spec.access == "write":
        audit_id = _audit(f"mcp:{client_id}", name, args, ExitCode.OK)
    return {"ok": True, "tool": name, "data": data, **({"audit_id": audit_id} if audit_id else {})}


def _audit(actor: str, tool_name: str, args: dict, rc: int) -> str:
    """写级审计落 `governance.db.audit_log`（治理库不可用时降级为控制台可见，**不阻断**）。

    actor 口径：`mcp:<client_id>`（与主链内部 run_id 区分——§4.4 要求 actor 可辨来源）。
    `target` 存参��摘要（截断）、`target_key` 存入参 sha256 前 16 位（可对账）。
    """
    import hashlib

    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    try:
        from std_lib.common_lib import governance_store as gs

        gs.log_audit(
            action=f"mcp_tool:{tool_name}",
            target=payload[:200],
            target_key=digest,
            actor=actor,
            basis="mcp write tool",
        )
    except Exception as e:  # noqa: BLE001  审计是旁路：失败必须可见但不得阻断调用
        print(f"[mcp] WARN 审计落档失败（调用已生效，不得静默）: {type(e).__name__}: {e}")
    return digest
