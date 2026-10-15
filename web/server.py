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
import os
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from bootstrap import bootstrap

bootstrap("all")


from . import DEFAULT_HOST, DEFAULT_PORT
from .api import ApiError, handle_request
from .sse import SseHub, format_sse

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
HUB = SseHub()
_LOOPBACK = ("127.0.0.1", "localhost", "::1")

#: 单页入口（浏览器只请求这一个文件 + 三个静态资源）
_INDEX = "index.html"


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
             ".svg": "image/svg+xml"}.get(os.path.splitext(name)[1], "text/plain; charset=utf-8")
    with open(p, "rb") as fh:
        return fh.read(), ctype


class Handler(BaseHTTPRequestHandler):
    server_version = "REG-Orch-Console/1.0"
    protocol_version = "HTTP/1.1"

    # 静默默认的逐请求日志（避免刷屏；--verbose 时打开）
    def log_message(self, fmt: str, *args) -> None:
        if getattr(self.server, "verbose", False):
            sys.stderr.write("[serve] " + (fmt % args) + "\n")

    # -- 输出helpers -------------------------------------------------------- #
    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 安全头（本地控制台也戴：禁嗅探/嵌入，防被外站嵌入点击劫持）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端早退（旁路：不记为错误）

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    # -- 路由 --------------------------------------------------------------- #
    def do_GET(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        if path in ("/", "", f"/{_INDEX}"):
            body, ctype = _asset(_INDEX)
            return self._send(200, body, ctype)
        if path == "/api/events":
            return self._sse()
        if path.startswith("/api/"):
            try:
                status, payload = handle_request("GET", self.path)
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
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        raw = self.rfile.read(n) if 0 < n <= 8192 else b""
        try:
            status, payload = handle_request("POST", self.path, raw_body=raw)
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
                 verbose: bool = False) -> ThreadingHTTPServer:
    """构造 HTTP 服务（契约测试直接用它起在随机端口）。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.verbose = verbose  # type: ignore[attr-defined]
    if host not in _LOOPBACK:
        sys.stderr.write(
            f"[serve][安全告警] 绑定 {host}（非回环）——本控制台**无鉴权**，"
            "仅可在可信局域网内使用；默认请用 127.0.0.1。\n")
    HUB.start()
    return srv


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *,
          open_browser: bool = False, verbose: bool = False) -> int:
    """阻塞式启动（Ctrl-C 退出）。"""
    srv = build_server(host=host, port=port, verbose=verbose)
    url = f"http://{host}:{port}/"
    print(f"[serve] 确认台已启动：{url}")
    print("[serve] 无鉴权，请勿暴露到非可信网络；Ctrl-C 退出。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n[serve] 已停止")
    finally:
        HUB.stop()
        srv.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地确认台（方案 §8；推荐经 cli.py serve 启动）")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help=f"绑定地址（默认 {DEFAULT_HOST}；非回环会告警）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--verbose", action="store_true", help="打印逐请求日志")
    a = ap.parse_args(argv)
    return serve(a.host, a.port, open_browser=a.open, verbose=a.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
