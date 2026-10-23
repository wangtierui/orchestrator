# -*- coding: utf-8 -*-
"""web.api — 路由 → JSON（**薄层**：只做参数校验与快照委派，不含业务逻辑）

路由表（GET 除非标注 POST）：

| 路由 | 页面 | 数据源 |
|---|---|---|
| `/api/overview` | P0 | `snapshots.overview()`（6 张卡） |
| `/api/flow` | P1 桑基 | `snapshots.flow_graph()` |
| `/api/flow/step?step=` | P1 抽屉 | `snapshots.step_detail()` |
| `/api/assets` | P0（原 P2，已并入总览） | `snapshots.assets()` |
| `/api/metrics` | P3 | `snapshots.metrics()` |
| `/api/quality` | P3 | `snapshots.quality()`（六维） |
| `/api/slo` | P3 | `snapshots.slo()` |
| `/api/worklist?status=&kind=` | P4 | `snapshots.worklist_items()` |
| `/api/worklist/kinds` | P4 | `snapshots.worklist_kinds()` |
| **`POST /api/worklist/resolve`** | P4 | `snapshots.worklist_resolve()`（**唯一写路径**） |
| `/api/governance` | P5 | `snapshots.governance()` |
| `/api/search?q=` | P6 | `snapshots.search()` |
| `/api/publish` | P7 | `snapshots.publish_board()` |
| `/api/release` | P8 | `snapshots.release_stream()` |
| `/api/health` | 探活 | 版本 + 门禁总数 + 快照缓存状态 |

安全：响应统一过滤密钥形态字段（`_scrub`）；POST 只接受 `application/json` 且体长上限 4KB。
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from . import snapshots as S

#: 密钥形态字段名（小写匹配）——对齐 gate_secret_scan 的纪律：**响应里不该出现凭据**
_SECRET_HINTS = ("token", "secret", "password", "passwd", "api_key", "apikey",
                 "dsn", "cookie", "authorization", "webhook", "credential")
MAX_BODY = 4096


def _scrub(obj):
    """递归过滤密钥形态键（防把 env 注入值随 payload 带到浏览器）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(h in lk for h in _SECRET_HINTS):
                out[k] = "***redacted***"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


class ApiError(Exception):
    """请求层错误（4xx）：与 provider 的降级（200 + available=false）区别对待。"""

    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status = status
        self.msg = msg


def _one(qs: dict, key: str, default: str = "") -> str:
    v = qs.get(key)
    return (v[0] if v else default)


def _int(qs: dict, key: str, default: int) -> int:
    try:
        return int(_one(qs, key) or default)
    except (TypeError, ValueError):
        raise ApiError(400, f"参数 {key} 非整数") from None


def handle(method: str, path: str, qs: dict | None = None,
           body: dict | None = None, ctx: dict | None = None) -> tuple[int, dict]:
    """返回 `(http_status, payload)`。

    `ctx`：**请求上下文**（`scope` / `client_ip` / `user_agent`），由 `web/server` 逐请求
    构造后透传。为什么用参数而不是全局/线程局部：服务是 `ThreadingHTTPServer`，
    全局变量会在并发请求间串味（A 的内网 IP 记到 B 的留言上）。
    """
    qs = qs or {}
    ctx = ctx or {}
    scope = str(ctx.get("scope") or "local")
    if path == "/api/health":
        from gates import ALL_GATES
        from web.policy import LOCAL_ONLY_ROUTES, REMOTE_POST_ALLOW

        return 200, {
            "ok": True,
            "gates_registered": len(ALL_GATES),
            "snapshot_ttl_s": S.CACHE_TTL_S,
            # 前端据此隐藏「待办闭环」（仅本机可见的功能不该让内网误点）
            "scope": scope,
            "client_ip": ctx.get("client_ip", ""),
            "local_only": scope == "local",
            "local_only_routes": list(LOCAL_ONLY_ROUTES),
            "remote_post_allow": list(REMOTE_POST_ALLOW),
        }
    if path == "/api/overview":
        return 200, S.overview()
    if path == "/api/flow":
        return 200, S.flow_graph()
    if path == "/api/flow/step":
        step = _one(qs, "step")
        if not step:
            raise ApiError(400, "缺参数 step")
        return 200, S.step_detail(step)
    if path == "/api/assets":
        return 200, S.assets()
    if path == "/api/metrics":
        return 200, S.metrics()
    if path == "/api/quality":
        return 200, S.quality()
    if path == "/api/slo":
        return 200, S.slo()
    if path == "/api/worklist":
        status = _one(qs, "status", "open")
        if status not in ("open", "resolved", "dismissed", ""):
            raise ApiError(400, f"status 非法（open/resolved/dismissed）：{status!r}")
        # 2026-10-16：`limit` 默认 0=全部（此前硬编码 200 ⇒ 641 条只显示 200，
        # 用户报"列表不完整"）；传 `offset` 支持分页，`total` 由 snapshots 回填。
        return 200, S.worklist_items(status=status or "open", kind=_one(qs, "kind"),
                                     limit=_int(qs, "limit", 0),
                                     offset=_int(qs, "offset", 0))

    # ---- 诊断（第 4 波-A，2026-10-19）------------------------------------
    # 复用 `mcp_server` 的同一份实现（MCP 与确认台口径必然一致，不复制判据）。
    # **只读**：不提供处置入口——处置仍走 P4 的 POST /api/worklist/resolve
    # （需人工填理由，C1 审计在写口内）。
    if path == "/api/diagnose":
        kind = (_one(qs, "kind") or "gate").strip()
        name = (_one(qs, "name") or "").strip()
        try:
            from mcp_server.tools import diagnose_gate, diagnose_step

            if kind == "step":
                return 200, diagnose_step(name)
            return 200, diagnose_gate(name)
        except Exception as e:  # noqa: BLE001  降级必须可辨（不伪装成无数据）
            return 200, {"available": False,
                         "note": f"诊断不可用：{type(e).__name__}: {e}"}

    if path == "/api/diagnose/backlog":
        try:
            from mcp_server.resources import _backlog_health

            return 200, _backlog_health()
        except Exception as e:  # noqa: BLE001
            return 200, {"available": False,
                         "note": f"积压诊断不可用：{type(e).__name__}: {e}"}

    if path == "/api/worklist/kinds":
        return 200, S.worklist_kinds()
    if path == "/api/worklist/resolve":
        if method != "POST":
            raise ApiError(405, "处置只接受 POST")
        b = body or {}
        item_id = str(b.get("item_id") or "").strip()
        resolution = str(b.get("resolution") or "").strip()
        status = str(b.get("status") or "resolved").strip()
        by = str(b.get("by") or "confirm_console").strip()[:60]
        if not item_id:
            raise ApiError(400, "缺 item_id")
        if not resolution:
            raise ApiError(400, "缺 resolution（处置理由必填）")
        if status not in ("resolved", "dismissed"):
            raise ApiError(400, f"status 非法（resolved|dismissed）：{status!r}")
        out = S.worklist_resolve(item_id, resolution, by=by, status=status)
        return (200 if out.get("ok") else 400), out
    if path == "/api/governance":
        return 200, S.governance()
    if path == "/api/search":
        return 200, S.search(_one(qs, "q"), kind=_one(qs, "kind", "records"),
                             limit=min(100, _int(qs, "limit", 20)))
    if path == "/api/records":
        # 「政策法规」页签：**服务端**分页/筛选/排序（16607 行不能整包下发）
        return 200, S.records_page(
            q=_one(qs, "q"), q_title=_one(qs, "q_title"),
            q_docno=_one(qs, "q_docno"), q_rfn=_one(qs, "q_rfn"),
            source=_one(qs, "source"),
            theme=_one(qs, "theme"), timeliness=_one(qs, "timeliness"),
            sort=_one(qs, "sort", "publish_date"),
            desc=_one(qs, "desc", "1") not in ("0", "false", ""),
            page=_int(qs, "page", 1), size=_int(qs, "size", 50))
    if path == "/api/records/facets":
        return 200, S.records_facets()
    if path == "/api/rules":
        # 「公司制度」页签：861 行，同样走服务端分页（口径与政策法规一致）
        return 200, S.rules_page(
            q=_one(qs, "q"), q_title=_one(qs, "q_title"),
            q_docno=_one(qs, "q_docno"), q_ipn=_one(qs, "q_ipn"),
            theme=_one(qs, "theme"),
            sort=_one(qs, "sort", "title"),
            desc=_one(qs, "desc", "0") not in ("0", "false", ""),
            page=_int(qs, "page", 1), size=_int(qs, "size", 50))
    if path == "/api/rules/facets":
        return 200, S.rules_facets()
    if path == "/api/relations":
        # 「关系图谱」页签：以 ref（RFN/IPN）为中心的一跳邻域
        return 200, S.relation_graph(_one(qs, "ref"))
    if path == "/api/feedback":
        if method == "POST":
            # 留言板是**内网唯一允许的写口**（见 web/policy.REMOTE_POST_ALLOW）：
            # 只追加、不改既有数据；账号/来源字段由本层从 ctx 取，前端传什么都不采信。
            from interfaces import feedback_api as FB

            out = FB.add(body or {},
                         ip=str(ctx.get("client_ip") or ""),
                         user_agent=str(ctx.get("user_agent") or ""),
                         header_account=str(ctx.get("header_user") or ""))
            return (200 if out.get("ok") else 400), out
        return 200, S.feedback_items(status=_one(qs, "status"),
                                     kind=_one(qs, "kind"),
                                     limit=min(500, _int(qs, "limit", 200)))
    if path == "/api/feedback/resolve":
        if method != "POST":
            raise ApiError(405, "留言处置只接受 POST")
        from interfaces import feedback_api as FB

        b = body or {}
        out = FB.resolve(str(b.get("fid") or ""),
                         handling=str(b.get("handling") or ""),
                         by=str(b.get("by") or ""),
                         status=str(b.get("status") or "closed"))
        return (200 if out.get("ok") else 400), out
    if path == "/api/regstats":
        return 200, S.regstats()
    if path == "/api/publish":
        return 200, S.publish_board()
    if path == "/api/release":
        # `limit=0`（默认）= **全量**（用户要求"变更记录应展示全部，不应仅近 20 条"）
        return 200, S.release_stream(limit=_int(qs, "limit", 0))
    if path == "/api/commit":
        sha = _one(qs, "sha").strip()
        if not sha:
            raise ApiError(400, "缺参数 sha")
        return 200, S.commit_detail(sha)
    # 本地文件**不在此层**：`GET /api/local/file?path=` 由 `web/server.py` 直接
    # 返回字节（带 `Content-Type`/`Content-Disposition`），因为它不是 JSON。
    # 校验逻辑在 `snapshots.local_file`（四重边界），本层不重复实现。
    raise ApiError(404, f"未知路由 {path}")


def handle_request(method: str, raw_path: str, raw_body: bytes = b"",
                   ctx: dict | None = None) -> tuple[int, dict]:
    """把原始请求转成 `(status, payload)`；内部异常统一降级为 200 + available=false。

    **旁路纪律**：确认台自身出错只影响本请求，绝不冒泡成主链故障。
    """
    from urllib.parse import urlparse

    u = urlparse(raw_path)
    body: dict | None = None
    if method == "POST":
        if len(raw_body) > MAX_BODY:
            raise ApiError(413, f"请求体过大（>{MAX_BODY}B）")
        try:
            parsed = json.loads(raw_body.decode("utf-8") or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("body 须为 JSON 对象")
            body = parsed
        except (UnicodeDecodeError, ValueError) as e:
            raise ApiError(400, f"body 解析失败：{e}") from e
    status, payload = handle(method, u.path, parse_qs(u.query), body, ctx=ctx)
    return status, _scrub(payload)


