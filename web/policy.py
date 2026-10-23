# -*- coding: utf-8 -*-
"""web.policy — 确认台的**访问分级策略**（内网只读 / 本机可写）。

为什么单独成模块（2026-10-23，第一百一十一批）
=============================================
确认台原来只绑 `127.0.0.1`，"谁能访问"这个问题不存在。现在要对公司内网开放
（让同事只读看数据），于是"哪些功能只能本机用"成为**安全边界**，必须：

1. **单一事实源**：`server` 用它拦截，`api` 用它回答 `/api/health`（前端据此隐藏页签）。
   两处各写一份必然分叉 —— 分叉的后果是"前端以为能用 / 后端其实拒绝"，
   或者更糟：**前端以为不能写 / 后端其实放行**。
2. **不引循环导入**：`server` 与 `api` 互相引用（server→api 派发、api→policy 判权），
   策略放第三方模块，两边都只依赖它。

安全设计的三个硬约束
--------------------
- **deny by default**：非 GET 一律只限本机，只有 `REMOTE_POST_ALLOW` 显式列出的例外
  （当前仅留言板）。将来新增写接口忘了加白名单，后果是"内网用不了"，而不是"内网能写"。
- **来源只信 TCP 对端**：绝不看 `X-Forwarded-For` / `X-Real-IP`（客户端可任意伪造，
  用它判来源等于把开关交给攻击者）。将来接入反向代理时，必须改为"只信任代理自身 IP"。
- **前端隐藏 ≠ 安全**：隐藏页签只是避免误点；真正的边界在服务端返回 403。
"""

from __future__ import annotations

#: **仅本机**可用的路由（前缀匹配，含子路径）。
#:
#: | 路由 | 为什么必须只限本机 |
#: |---|---|
#: | `/api/worklist/resolve` | 处置写路径（改治理库、留审计）——内网任何人可改就等于没有审计主体 |
#: | `/api/feedback/resolve` | 留言处置（同上：状态流转属管理动作） |
#: | `/api/local/file` | 直接吐出仓内文件字节（制度原件/清洗产物）——内网可下 = 资料外泄 |
#: | `/api/diagnose` | 宿主诊断（路径/环境/进程），属主机指纹 |
#: | `/api/commit` | `git show` 原始输出（提交者/分支/历史），属研发内部信息 |
LOCAL_ONLY_ROUTES: tuple[str, ...] = (
    "/api/worklist/resolve",
    "/api/feedback/resolve",
    "/api/local/file",
    "/api/diagnose",
    "/api/commit",
)

#: 允许**内网**调用 POST 的路由。
#:
#: - `/api/feedback`：留言板（任何使用者都要能提交问题）
#: - `/api/auth/login` / `/api/auth/logout`：登录/登出**必须内网可达**，否则
#:   "开了认证"等于"内网谁也登录不进来"（实测踩到：登录被本机专属闸门挡成 403）。
#:   它们的保护不靠来源 IP，靠**口令校验 + 失败限频**（见 `web/auth.py`）。
REMOTE_POST_ALLOW: tuple[str, ...] = (
    "/api/feedback",
    "/api/auth/login",
    "/api/auth/logout",
)


def _norm(ip: str) -> str:
    """IPv4-mapped IPv6（`::ffff:127.0.0.1`）按 IPv4 处理。

    不处理会出现一种很隐蔽的误判：某些 Windows 环境下 `client_address[0]` 是
    `::ffff:127.0.0.1`，`is_loopback` 为 False ⇒ **本机用户被当成内网用户**，
    于是"仅本机"的功能自己都用不了。
    """
    s = (ip or "").strip()
    if s.lower().startswith("::ffff:"):
        return s[7:]
    return s


def access_scope(client_ip: str) -> str:
    """来源 IP → `"local"` / `"lan"` / `"unknown"`。"""
    import ipaddress

    s = _norm(client_ip)
    if not s:
        return "unknown"
    try:
        addr = ipaddress.ip_address(s)
    except ValueError:
        return "unknown"
    if addr.is_loopback:
        return "local"
    if addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return "unknown"
    return "lan"


def route_is_local_only(method: str, path: str) -> bool:
    """该请求是否**只允许本机**。

    顺序：① 命中 `LOCAL_ONLY_ROUTES` ⇒ 是；② 非 GET 且不在 `REMOTE_POST_ALLOW` ⇒ 是；
    ③ 其余只读 GET ⇒ 否（内网可看）。
    """
    p = (path or "").split("?", 1)[0]
    for r in LOCAL_ONLY_ROUTES:
        if p == r or p.startswith(r + "/"):
            return True
    if (method or "GET").upper() != "GET":
        return p not in REMOTE_POST_ALLOW
    return False


def may_access(method: str, path: str, client_ip: str) -> tuple[bool, str]:
    """合并判定 → `(是否放行, 来源范围)`。放行则不看范围。"""
    scope = access_scope(client_ip)
    if scope == "local":
        return True, scope
    return (not route_is_local_only(method, path)), scope


def lan_ipv4() -> list[str]:
    """本机对内网可达的 IPv4（用于"同事该访问哪个地址"的提示）。

    纯标准库 `getaddrinfo(主机名)`；**不做**"UDP 连一下 8.8.8.8 看出口 IP"
    那种探测 —— 无路由环境下会阻塞数秒，只为一个提示文案不值得。
    失败返回 `[]`（仅少几行提示，不影响启动）。
    """
    import socket

    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip.startswith("127.") or ip.startswith("169.254.") or ip in out:
                continue
            out.append(ip)
    except OSError:
        return []
    return out
