# -*- coding: utf-8 -*-
"""web.server — 确认台 HTTP 服务（**纯标准库**：http.server + json + sqlite3）

安全与纪律（§8.2.2 / §8.8）：
  · **loopback-only**：默认绑 `127.0.0.1`；改绑非回环地址时打印安全告警（无鉴权 ⇒
    只应在可信局域网内使用）；
  · **只读优先**：除 `POST /api/worklist/resolve` 外全部只读；DB 直连一律 `mode=ro`；
  · **旁路降级**：本服务任何异常都以 JSON 错误页返回，**绝不影响主链**（本包不在
    链上，进程独立）；
  · **零 CDN**：前端为单文件原生 JS（无外链）；静态资源从 `web/assets/` 直接读盘。

用法：
    python -m tools.serve            # CLI 薄壳（推荐）
    python -m web.server --port 8765 # 直接跑（调试）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import paths
from bootstrap import bootstrap
from config.exitcodes import ExitCode

bootstrap("all")


from . import DEFAULT_HOST, DEFAULT_PORT, auth
from .api import ApiError, handle_request
from .sse import SseHub, format_sse

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
HUB = SseHub()
_LOOPBACK = ("127.0.0.1", "localhost", "::1")

#: 单页入口（浏览器只请求这一个文件 + 三个静态资源）
_INDEX = "index.html"


#: 认证相关路由（**免认证**：登录本身不能要求已登录）
_AUTH_ROUTES = ("/api/auth/login", "/api/auth/status", "/api/auth/logout")


def _asset(name: str) -> tuple[bytes, str]:
    """读静态资源 → `(bytes, content_type)`；防路径穿越（`..` 一律拒绝）。"""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ApiError(400, f"非法资源名 {name!r}")
    p = os.path.join(ASSETS_DIR, name)
    if not os.path.exists(p):
        raise ApiError(404, f"资源不存在 {name}")
    ctype = {".html": "text/html; charset=utf-8",
             ".js": "application/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8",
             ".svg": "image/svg+xml"}.get(os.path.splitext(name)[1],
                                          "text/plain; charset=utf-8")
    with open(p, "rb") as fh:
        return fh.read(), ctype


#: 访问分级策略（**单一事实源**在 web/policy.py：server 拦截 + api 回答 health 共用）
from .policy import (
    access_scope,
    lan_ipv4,
    route_is_local_only,
)


class Handler(BaseHTTPRequestHandler):
    server_version = "REG-Orch-Console/1.0"
    protocol_version = "HTTP/1.1"

    # 静默默认的逐请求日志（避免刷屏；--verbose 时打开）
    #
    # ⚠️ 2026-10-21（T3）：原实现写 `sys.stderr` ⇒ **开机自启动后完全丢失**
    # （pythonw 无控制台，stderr 无处可去）。改为走 `logging`（`serve` 已给
    # root logger 挂 RotatingFileHandler）⇒ 同时保留控制台输出与落盘。
    def log_message(self, fmt: str, *args) -> None:
        if not getattr(self.server, "verbose", False):
            return
        msg = "[serve] " + (fmt % args)
        sys.stderr.write(msg + "\n")      # 控制台（前台调试时看）
        logging.getLogger("web.server").info(msg)

    # -- 输出helpers -------------------------------------------------------- #
    def _send(self, status: int, body: bytes,
              ctype: str = "application/json; charset=utf-8",
              disposition: str = "", extra_headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        # 安全头（本地控制台也戴：禁嗅探/嵌入，防被外站嵌入点击劫持）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if disposition:
            # `inline` 交给浏览器原生查看器（PDF/TXT）；其余强制下载——
            # **绝不用 inline 提供 .html/.svg**：同源下会执行脚本（见 snapshots.local_file）。
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端早退（旁路：不记为错误）

    def _json(self, status: int, payload: dict,
              extra_headers: list[tuple[str, str]] | None = None) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   extra_headers=extra_headers)

    # -- 路由 --------------------------------------------------------------- #
    # -- 访问分级 ------------------------------------------------------------- #
    def _ctx(self) -> dict:
        """本次请求的可信上下文（**逐请求构造**，不放全局 —— 服务是多线程的）。"""
        from interfaces.feedback_api import header_user
        from web import auth as _auth

        ident = getattr(self, "_ident", None) or _auth.identity(
            cookies=_auth.parse_cookies(self.headers.get("Cookie") or ""),
            client_ip=self.client_address[0], headers=dict(self.headers),
            mode=getattr(self.server, "auth_mode", "off"),
            trusted_proxies=tuple(getattr(self.server, "trusted_proxies", ()) or ()),
            lan=bool(getattr(self.server, "lan", False)),
        )
        return {
            "scope": access_scope(self.client_address[0]),
            "client_ip": self.client_address[0],
            "user_agent": (self.headers.get("User-Agent") or "")[:300],
            # 访问者域账号：**已认证身份优先**（会话/可信代理）；
            # 未认证时才回落到代理头（兼容"有代理但没开会话"的部署）。
            "header_user": (ident.get("user") or "") if ident.get("authenticated")
            else header_user(dict(self.headers)),
            "identity_user": ident.get("user", ""),
            "identity_src": ident.get("src", "none"),
            "authenticated": bool(ident.get("authenticated")),
            # 审计级别：`user`（个人令牌/代理/账号）| `shared`（共享口令）
            "level": str(ident.get("level") or ""),
            # `api.handle` 需要它来区分"要不要登录"（health 公开段）
            "auth_mode": str(getattr(self.server, "auth_mode", "off") or "off"),
        }

    # -- 认证 ------------------------------------------------------------------ #
    def _auth_mode(self) -> str:
        return str(getattr(self.server, "auth_mode", "off") or "off")

    def _auth_kind(self) -> str:
        """**实际校验方式**：`auto` 在远端等同 `token`。

        为什么要单独一个函数（2026-10-23 真实故障）：`_auth_login` 原来只判
        `mode == "token"`，把默认模式改成 `auto` 之后没有任何分支命中 ⇒
        **口令正确也报"账号或口令错误"**，表现为"两个地址都进不去"。
        认证模式是"策略"，校验方式是"实现" —— 两者必须解耦，否则每加一种模式
        都会漏掉某个分支。
        """
        m = self._auth_mode()
        return "token" if m == "auto" else m

    def _identify(self) -> dict:
        from web import auth as _auth

        self._ident = _auth.identity(
            cookies=_auth.parse_cookies(self.headers.get("Cookie") or ""),
            client_ip=self.client_address[0], headers=dict(self.headers),
            mode=self._auth_mode(),
            trusted_proxies=tuple(getattr(self.server, "trusted_proxies", ()) or ()),
            lan=bool(getattr(self.server, "lan", False)),
        )
        return self._ident

    def _guard_auth(self, method: str, path: str) -> bool:
        """认证闸门 → `False` 表示已回 401/403（调用方 return）。

        范围：`auth_mode != off` 时，除 `/api/auth/*` 外的**所有 `/api/*`** 都要求已认证。
        首页与静态资源不拦（SPA 会自己渲染登录卡片）—— 页面本身不含数据，
        拦它只会让"登录失败"变成白屏。
        """
        from web import auth as _auth

        if self._auth_mode() == "off":
            self._ident = {"user": "", "src": "none", "authenticated": True,
                           "mode": "off"}
            return True
        ident = self._identify()
        # `/api/health` 也放行 —— 它是**登录前唯一可用的状态探针**：
        #   ① 页面要知道"该显示登录卡片还是数据"（mode/authenticated/need_login）；
        #   ② `tools/workbench_service status` 要知道服务是否活着。
        # 安全上不泄露内情：`api.handle` 在未认证时**只回公开子集**
        # （不含 gates 数、本机专属路由清单、client_ip 等），见 web/api.py。
        if path in _AUTH_ROUTES or path == "/api/health":
            return True
        if not path.startswith("/api/"):
            return True
        if not ident.get("authenticated"):
            self._json(401, {
                "ok": False, "error": "未认证", "need_login": True,
                "mode": self._auth_mode(),
                "hint": "请先登录（共享口令或账号）；认证方式见启动日志/README §6.9",
            })
            return False
        # CSRF：**所有写请求**必须带与 cookie 一致的 `X-CSRF`（防跨站提交）。
        # ⚠️ 2026-10-23 修：原实现只在"cookie 里有 token"时才校验 ⇒ **可信代理认证**
        # 路径没有任何 cookie，于是完全跳过校验（实测：代理身份下免 CSRF 直接写入）。
        # 现在改为"**没有 token 也拒绝**"，token 由 `/api/auth/status` 首次访问时下发。
        if method.upper() != "GET":
            need = _auth.parse_cookies(self.headers.get("Cookie") or "").get(
                _auth.COOKIE_CSRF, "")
            got = self.headers.get("X-CSRF") or ""
            import hmac as _hmac

            if not need:
                self._json(403, {"ok": False,
                                 "error": "缺少 CSRF 令牌：请先加载页面（状态接口会下发）"})
                return False
            if not _hmac.compare_digest(need, got):
                self._json(403, {"ok": False, "error": "CSRF 校验失败（缺 X-CSRF 或不匹配）"})
                return False
        return True

    def _auth_login(self, raw: bytes) -> None:
        """`POST /api/auth/login`：token 模式验口令；basic 模式验账号+口令。"""
        import json as _json

        from web import auth as _auth

        ip = self.client_address[0]
        wait = _auth.throttled(ip)
        if wait:
            self._json(429, {"ok": False,
                             "error": f"登录失败次数过多，请 {wait} 秒后再试"})
            return
        try:
            body = _json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            self._json(400, {"ok": False, "error": "请求体须为 JSON"})
            return

        user = str(body.get("user") or "").strip()
        pw = str(body.get("password") or "")
        mode = self._auth_kind()      # auto → token（回环已自动认证，不会走到这里）
        ok = False
        shown = ""
        level = "user"
        if mode == "token":
            # 个人令牌优先（可审计到人）；共享口令仍可用，但会话标 `level=shared`。
            who, lv = _auth.identify_token(pw)
            ok = bool(who)
            shown = who
            level = lv or "shared"
        elif mode == "basic":
            rec = (_auth.load_users() or {}).get(user) or {}
            ok = bool(rec) and _auth.verify_password(pw, str(rec.get("password") or ""))
            shown = user
        if not ok:
            allow, wait2 = _auth.note_failure(ip)
            _auth.audit("console_login_fail", user=user, ip=ip, note=f"mode={mode}")
            self._json(401, {"ok": False,
                             "error": ("口令错误" if mode == "token" else "账号或口令错误")
                                      + ("" if allow else f"；已限频，请 {wait2} 秒后再试")})
            return
        _auth.clear_failures(ip)
        cookie, csrf, exp = _auth.issue_session(shown or "user", src="session",
                                                level=level)
        secure = bool(getattr(self.server, "tls", False))
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", _auth.cookie_header(
            _auth.COOKIE_SESS, cookie, max_age=_auth.SESSION_TTL_S, secure=secure))
        self.send_header("Set-Cookie", _auth.cookie_header(
            _auth.COOKIE_CSRF, csrf, max_age=_auth.SESSION_TTL_S, secure=secure,
            http_only=False))   # double-submit：页面脚本必须能读到它
        self.send_header("X-CSRF", csrf)
        body_out = _json.dumps({"ok": True, "user": shown,
                                "csrf": csrf, "exp": exp},
                               ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)
        _auth.audit("console_login_ok", user=shown, ip=ip, note=f"mode={mode}")

    def _auth_logout(self) -> None:
        from web import auth as _auth

        ident = getattr(self, "_ident", None) or {}
        cookies = [
            _auth.cookie_header(_auth.COOKIE_SESS, "", max_age=0),
            _auth.cookie_header(_auth.COOKIE_CSRF, "", max_age=0),
        ]
        payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        _auth.audit("console_logout", user=str(ident.get("user") or ""),
                    ip=self.client_address[0])

    def _guard(self, method: str, path: str) -> bool:
        """分级闸门：本机全放行；内网/未知来源只放行"只读且非本机专属"的路由。

        返回 `False` 表示**已回 403**，调用方应立即 return。
        为什么要在**服务端**拦：前端隐藏页签只是"别让人误点"，不是安全边界 ——
        内网任何机器都能直接 `curl` 那些路由。
        """
        ctx = self._ctx()
        if ctx["scope"] == "local":
            return True
        if not route_is_local_only(method, path):
            return True
        p = (path or "").split("?", 1)[0]
        logging.getLogger("web.server").warning(
            "[serve] 拒绝越权访问 ip=%s scope=%s %s %s", ctx["client_ip"],
            ctx["scope"], method, p)
        self._json(403, {
            "ok": False,
            "error": "该功能仅限本机访问",
            "route": p,
            "scope": ctx["scope"],
            "client_ip": ctx["client_ip"],
            "hint": "待办处置 / 本地文件 / 宿主诊断 / 提交详情属本机专属；"
                    "内网可使用总览、数据流转、指标、政策法规、公司制度、"
                    "关系图谱、治理审计、文档检索、监管统计、留言板。",
        })
        return False

    def do_GET(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        if not self._guard("GET", path):
            return
        if not self._guard_auth("GET", path):
            return
        if path == "/api/auth/status":
            ident = getattr(self, "_ident", None) or {}
            hdrs: list[tuple[str, str]] = []
            authed = bool(ident.get("authenticated"))
            # **CSRF 令牌下发点**：已认证但还没有 `wb_csrf`（典型是可信代理认证，
            # 从没走过登录）⇒ 这里补一个，否则前端所有写请求都会 403。
            if authed:
                ck = auth.parse_cookies(self.headers.get("Cookie") or "")
                if not ck.get(auth.COOKIE_CSRF):
                    hdrs.append(("Set-Cookie", auth.cookie_header(
                        auth.COOKIE_CSRF, secrets.token_hex(16),
                        max_age=auth.SESSION_TTL_S,
                        secure=bool(getattr(self.server, "tls", False)),
                        http_only=False)))
            return self._json(200, {
                "ok": True,
                "mode": self._auth_mode(),
                "authenticated": authed,
                "user": ident.get("user", ""),
                "src": ident.get("src", "none"),
                "need_login": self._auth_mode() != "off" and not authed,
                "user_source": {"proxy": "网关认证", "session": "登录会话",
                                "none": "未认证"}.get(ident.get("src", "none"), ""),
            }, extra_headers=hdrs)
        if path in ("/", "", f"/{_INDEX}"):
            body, ctype = _asset(_INDEX)
            return self._send(200, body, ctype)
        if path == "/api/events":
            return self._sse()
        if path == "/api/local/file":
            # 本地文件由**服务端提供字节、浏览器自己打开/下载**（不拉起本机程序）：
            # 本仓 `tests/test_no_desktop_interference.py` 禁止"夺焦点"的打开方式。
            # 该校验实现在 `snapshots.local_file`（四重边界），此层只负责带正确的
            # `Content-Type` / `Content-Disposition` 把字节发出去。
            from urllib.parse import parse_qs

            from web import snapshots as _snap

            q = parse_qs(u.query)
            rel = (q.get("path") or [""])[0]
            r = _snap.local_file(rel)
            if not r.get("ok"):
                return self._json(404, {"ok": False, "reason": r.get("reason", "")})
            disp = r["disposition"]
            fname = str(r.get("filename") or "file")
            # 中文文件名需 RFC 5987 编码；ASCII 兜底名供老浏览器用
            from urllib.parse import quote

            cd = f"{disp}; filename*=UTF-8''{quote(fname)}"
            return self._send(200, r["body"], r["ctype"], disposition=cd)
        if path.startswith("/api/"):
            try:
                status, payload = handle_request("GET", self.path, ctx=self._ctx())
            except ApiError as e:
                return self._json(e.status, {"error": e.msg})
            except Exception as e:  # noqa: BLE001 旁路：任何异常都降级为 JSON
                return self._json(200, {"available": False,
                                        "reason": f"{type(e).__name__}: {str(e)[:200]}"})
            return self._json(status, payload)
        # 静态资源
        try:
            body, ctype = _asset(os.path.basename(path))
        except ApiError as e:
            return self._json(e.status, {"error": e.msg})
        return self._send(200, body, ctype)

    def do_POST(self) -> None:
        # 路径解析交给 api.handle_request（单一解析口，避免两处解析漂移）
        path = unquote(urlparse(self.path).path)
        if not self._guard("POST", path):
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        # 上限 8192 → 放宽到 64KB：留言正文最长 4000 字符（UTF-8 三字节中文 ≈ 12KB），
        # 8KB 会把中文长留言**静默截断**（服务端读到半截 JSON ⇒ 报"正文非法"）。
        raw = self.rfile.read(n) if 0 < n <= 65536 else b""
        # 认证路由必须在**读体之后**处理（登录要读口令），且先于通用鉴权闸门
        # （否则"登录"这个动作本身会被 401 挡掉）。
        if path == "/api/auth/login":
            return self._auth_login(raw)
        if path == "/api/auth/logout":
            return self._auth_logout()
        if not self._guard_auth("POST", path):
            return
        try:
            status, payload = handle_request("POST", self.path, raw_body=raw,
                                             ctx=self._ctx())
        except ApiError as e:
            return self._json(e.status, {"error": e.msg})
        except Exception as e:  # noqa: BLE001
            return self._json(200, {"available": False,
                                    "reason": f"{type(e).__name__}: {str(e)[:200]}"})
        return self._json(status, payload)

    # -- SSE ---------------------------------------------------------------- #
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = HUB.subscribe()
        try:
            self.wfile.write(format_sse("hello", json.dumps(
                {"clients": HUB.clients}, ensure_ascii=False)))
            while True:
                try:
                    event, data = q.get(timeout=15.0)
                except queue.Empty:
                    event, data = "ping", json.dumps({"t": "hb"}, ensure_ascii=False)
                self.wfile.write(format_sse(event, data))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开（旁路）
        finally:
            HUB.unsubscribe(q)


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 verbose: bool = False, *, auth_mode: str = "off",
                 trusted_proxies: tuple[str, ...] = (),
                 tls: bool = False) -> ThreadingHTTPServer:
    """构造 HTTP 服务（契约测试直接用它起在随机端口）。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.verbose = verbose  # type: ignore[attr-defined]
    # 认证上下文挂在 server 上（Handler 每请求读；不放全局 —— 多线程安全）
    srv.auth_mode = auth_mode            # type: ignore[attr-defined]
    srv.trusted_proxies = tuple(trusted_proxies)   # type: ignore[attr-defined]
    srv.tls = bool(tls)                  # type: ignore[attr-defined]
    srv.lan = host not in _LOOPBACK      # type: ignore[attr-defined]
    if host not in _LOOPBACK:
        # ⚠️ 一律走 `_log()`（logger 落盘 + 控制台），**不要**直接写 `sys.stderr`：
        # 开机自启动用 `pythonw.exe`，stderr 无去处 ⇒ 这些关键提示（绑定范围、内网地址）
        # 会**全部丢失**，而排障恰恰只有日志可看（2026-10-23 实测：日志里只有
        # "已启动：http://0.0.0.0:8765/"，内网地址一行都没有）。
        _log(f"[serve][安全告警] 绑定 {host}（非回环）——本控制台**无鉴权**，"
             "仅可在可信局域网内使用；默认请用 127.0.0.1。")
        # 非回环 = 对内网开放**只读**。写路径/本地文件/宿主诊断已由 `web/policy`
        # 按来源 IP 拒绝，这里把"开放了什么/没开放什么"写进启动日志
        # （排障时不用翻代码猜边界）。
        _log("[serve] 访问分级：内网可读（总览/流转/指标/法规/制度/图谱/审计/检索/"
             "监管统计/留言板）；"
             "仅本机可用（待办处置/留言处置/本地文件/宿主诊断/提交详情）。")
        for ip in lan_ipv4():
            _log(f"[serve] 内网访问地址：http://{ip}:{port}/")
    HUB.start()
    # 预热"重量级"快照（外部模型目录 13.8GB / 1.9 万文件，扫一遍 4s 级）。
    # **后台线程**、非阻塞：不预热的话首个 `/api/overview` 会同步扫完才返回
    # （实测冷态 22s ⇒ 撞破契约测试的 20s 超时，表现为"偶发失败"）。
    # 预热失败不影响服务（`storage_usage` 会退化为 `heavy_ready=False`）。
    try:
        from web import snapshots as _snap

        _snap.warm_storage()
        # P2 产物清单冷态 5965ms（`validate_schema` 要遍历 959MB clauses 产物）
        # ⇒ 同样预热，避免首屏该卡片转圈 6 秒。
        _snap.warm_assets()
    except Exception:  # noqa: BLE001  预热是优化，不得影响启动
        pass
    # 预热待办的"访问地址反查索引"（要扫 16 个 clean 产物共 1.6GB，冷态 6513ms）。
    # 未预热时首个 `/api/worklist` 会同步等 6.5 秒；预热后该请求立即返回，
    # 索引未就绪的条目 URL 显示"统计中…"（`warming`）而非误报"未查到"。
    try:
        from interfaces import worklist_fields as _wf

        _wf._Bridge.build_async()
    except Exception:  # noqa: BLE001
        pass
    return srv


def _log(msg: str) -> None:
    """**双写**：控制台 + logger。

    为什么不能只用 `print`：开机自启动用 `pythonw.exe`（无控制台），
    `print` 的输出无处可去 ⇒ 启动信息、错误、退出原因全部丢失，排障只能靠猜。
    `serve` 已给 root logger 挂上 `RotatingFileHandler`（见 `_setup_file_logging`），
    走 logger 即可同时落盘。
    """
    print(msg)
    logging.getLogger("web.server").info(msg)


def _bind_free_port(host: str, port: int, *, tries: int = 20) -> int:
    """端口被占用时**有限递增**找一个可用端口（返回实际端口）。

    为什么需要（2026-10-21，第一百零六批）：`cli.py serve --help` 一直写着
    「端口（默认 8765，**被占用时换端口**）」，但 `build_server` 直接
    `ThreadingHTTPServer((host, port), Handler)` ⇒ 占用即 `OSError`，
    **文档与代码不符**（用户按文档预期不会失败，结果失败了）。

    设计取舍：
      · 不用 `port=0`（让 OS 随机分配）—— 那会让每次启动的 URL 都变，
        对「开机自启动 + 用户收藏地址」极不友好；
      · 不用无限循环 —— 那会把"配置错误"（如已被别的服务长期占用）拖成挂起；
      · 取 `port, port+1, …, port+tries-1` 有限递增：既可预期（通常仍是 8765），
        又能在前一个端口恰被占用时自动续上。
    """
    for i in range(max(1, tries)):
        cand = port + i
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, cand))
            return cand
        except OSError:
            continue
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
    return port  # 全被占用 ⇒ 原样返回，由调用方报错（不静默）


def _setup_file_logging(log_file: str) -> bool:
    """把日志同时写文件（**开机自启动后看不到控制台**，必须有落盘）。幂等。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        from logging.handlers import RotatingFileHandler

        root = logging.getLogger()
        tgt = os.path.abspath(log_file)
        for h in root.handlers:
            if (isinstance(h, RotatingFileHandler)
                    and getattr(h, "baseFilename", None) == tgt):
                return True   # 已配置过（避免重复 handler 双写）
        h = RotatingFileHandler(tgt, maxBytes=5 * 1024 * 1024, backupCount=3,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
        return True
    except Exception as e:  # noqa: BLE001  日志落盘失败不得阻断启动
        sys.stderr.write(f"[serve][warn] 日志文件配置失败（{e}），仅输出到控制台\n")
        return False


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *,
          open_browser: bool = False, verbose: bool = False,
          pid_file: str | None = None, log_file: str | None = None,
          single_instance: bool = False,
          auth_mode: str = "", trusted_proxies: tuple[str, ...] = (),
          tls_cert: str = "", tls_key: str = "") -> int:
    """阻塞式启动（Ctrl-C 退出）。

    2026-10-21（第一百零六批）为「开机自启动」补三个运行期能力：

    * `single_instance` —— 用 `fs_lock.ProcessLock` 保证**同时只有一个实例**。
      计划任务会在每次登录时触发，若无此保护，重复触发会起多个进程
      （每个都占一个端口、各自读同一份 SQLite）⇒ 互相踩踏。
      已被占用时打印明确原因并返回 **0**（幂等语义：计划任务不会因此标失败）。
    * `log_file` —— 日志同时落盘（自启动后无控制台可看，否则排障只能靠猜）。
    * `pid_file` —— 锁路径；缺省取 `data/state/workbench.lock`。
    """
    if log_file:
        _setup_file_logging(log_file)

    # 端口：先探测一个可用的（兑现 help 里"占用时换端口"的承诺，见 _bind_free_port）
    port = _bind_free_port(host, port)

    lock_path = None
    lk = None
    if single_instance:
        from std_lib.common_lib import fs_lock

        lock_path = pid_file or os.path.join(paths.DATA_DIR, "state",
                                             "workbench.lock")
        os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
        lk = fs_lock.ProcessLock(lock_path)
        if not lk.acquire():
            other = fs_lock._read_lock_pid(lock_path)
            _log(f"[serve] 已有实例在运行（lock={lock_path}, pid={other}）"
                 " ⇒ 本次不再启动（单实例模式）。")
            return 0

    # ---- 认证模式 ------------------------------------------------------------- #
    # 取值：`auto`（默认）| `off` | `token` | `basic`。
    #
    # `auto` 的语义（2026-10-23 第一百一十三批，修"两个地址都进不去"）：
    #   · **回环**（127.0.0.1）⇒ 自动认证为**本机 OS 账号**（`src=local`），不弹登录；
    #   · **内网** ⇒ 等同 `token`（必须登录）。
    # 为什么这样定：原来 `--lan` 一律升级成 `token`，连本机也要粘贴口令，而回环
    # 访问者本来就有同等的文件读权限 —— 加这道门槛不增加安全性，只制造"打不开"。
    # 反过来，内网匿名可读是**真的**风险，必须保留认证。
    _mode = (auth_mode or "").strip().lower()
    if _mode not in ("auto", "off", "token", "basic"):
        _mode = "auto"
    if host not in _LOOPBACK and _mode == "off":
        # 对外绑定绝不允许无认证（`auto` 在远端等价于 token）
        _mode = "auto"
    use_tls = bool(tls_cert and tls_key)

    try:
        srv = build_server(host=host, port=port, verbose=verbose,
                           auth_mode=_mode, trusted_proxies=tuple(trusted_proxies),
                           tls=use_tls)
    except OSError as e:
        msg = f"[serve] 绑定 {host}:{port} 失败：{e}"
        sys.stderr.write(msg + "\n")
        logging.getLogger("web.server").error(msg)
        if lk is not None:
            lk.release()
        return ExitCode.FAIL

    if use_tls:
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_cert, tls_key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    scheme = "https" if use_tls else "http"
    url = f"{scheme}://{host}:{port}/"
    _log(f"[serve] 确认台已启动：{url}")
    _log(f"[serve] pid={os.getpid()} lock={lock_path or '(未启用)'}")
    if _mode == "off":
        _log("[serve] 认证：off（仅回环，无鉴权）；Ctrl-C 退出。")
    else:
        _log(f"[serve] 认证：{_mode}；Ctrl-C 退出。")
        # ⚠️ `auto` 必须与 `token` 一起打印口令（2026-10-23 真实故障）：
        # 原判断只写了 `_mode == "token"`，而新默认是 `auto` ⇒ 内网同事拿不到口令，
        # 页面只剩一张"请输入共享口令"的卡片，而日志里根本没有那串口令。
        if _mode in ("token", "auto"):
            _log(f"[serve] 内网共享口令：{auth.access_token()}"
                 "（文件 data/state/console_token.txt；--auth-rotate 可更换）")
            if _mode == "auto":
                _log("[serve] 本机（127.0.0.1）已自动认证为当前 Windows 账号，"
                     "**无需口令**；内网访问需上面的口令")
        elif _mode == "basic":
            users = auth.load_users()
            _log(f"[serve] 账号模式：已配置 {len(users)} 个账号"
                 f"（{auth.USERS_PATH}）；新增：python -m web.auth adduser")
            if not users:
                _log("[serve][warn] 未配置任何账号 ⇒ **无人能登录**"
                     "（这比放行所有人安全；请先 adduser）")
            if not use_tls:
                _log("[serve][warn] basic 模式未启用 TLS：口令以 base64 明文过网，"
                     "请仅在内网可信网段使用，或加 --tls-cert/--tls-key")
        if trusted_proxies:
            _log(f"[serve] 受信代理：{list(trusted_proxies)}"
                 "（仅这些 IP 的 X-Remote-User 被采信）")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        _log("\n[serve] 已停止")
    finally:
        HUB.stop()
        srv.server_close()
        if lk is not None:
            lk.release()
    return ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地确认台（方案 §8；推荐经 cli.py serve 启动）")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help=f"绑定地址（默认 {DEFAULT_HOST}；非回环会告警）")
    ap.add_argument("--lan", action="store_true",
                    help="对公司内网开放（等价 --host 0.0.0.0）。**只读**："
                         "待办处置/留言处置/本地文件/宿主诊断/提交详情仍仅限本机")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="端口（默认 %d；被占用时有限递增换端口）" % DEFAULT_PORT)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印逐请求日志")
    # 开机自启动三件套（由 tools/workbench_service.py 安装的计划任务传入）
    ap.add_argument("--pid-file", default="",
                    help="单实例锁路径（默认 data/state/workbench.lock）")
    ap.add_argument("--log-file", default="",
                    help="日志文件（缺省仅控制台；开机自启动**必须**给，否则无日志可查）")
    ap.add_argument("--single-instance", action="store_true",
                    help="单实例模式：已有实例在跑则本次直接退出（幂等）")
    # ---- 认证（2026-10-23）：对内网开放时**不允许无认证** -------------------- #
    ap.add_argument("--auth", default="",
                    choices=["", "auto", "off", "token", "basic"],
                    help="认证方式：off（仅回环）/ token（共享口令）/ basic（账号）；"
                         "缺省时 --lan 自动用 token")
    ap.add_argument("--auth-rotate", action="store_true",
                    help="token 模式：重新生成共享口令")
    ap.add_argument("--trust-proxy", action="store_true",
                    help="采信受信代理注入的 X-Remote-User（集成认证）")
    ap.add_argument("--trusted-proxies", default="",
                    help="受信代理 IP 列表（逗号分隔）；配合 --trust-proxy")
    ap.add_argument("--tls-cert", default="", help="HTTPS 证书（PEM）")
    ap.add_argument("--tls-key", default="", help="HTTPS 私钥（PEM）")
    a = ap.parse_args(argv)
    host = "0.0.0.0" if a.lan else a.host
    if a.auth_rotate:
        print(f"新共享口令：{auth.access_token(rotate=True)}")
    proxies = tuple(p.strip() for p in (a.trusted_proxies or "").split(",") if p.strip())
    return serve(host, a.port, open_browser=a.open, verbose=a.verbose,
                 pid_file=a.pid_file or None, log_file=a.log_file or None,
                 single_instance=a.single_instance, auth_mode=a.auth,
                 trusted_proxies=proxies if a.trust_proxy else (),
                 tls_cert=a.tls_cert, tls_key=a.tls_key)


if __name__ == "__main__":
    raise SystemExit(main())
