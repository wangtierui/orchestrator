"""极简 CDP 客户端 —— 让"看页面"从猜变成测。

用途：本机没有 selenium/playwright/websocket 库，但装了 Edge。这里手写
WebSocket 客户端（RFC6455 最小子集）驱动 Edge 的 DevTools 协议，实现：
  · navigate / 等待 JS 跑完
  · Runtime.evaluate（等价 F12 控制台里贴一段 JS，可读 getComputedStyle / getBoundingClientRect）
  · Page.captureScreenshot（整页/视口截图，落成 PNG 供人眼复核）

用法：
  python reports/_tmp/cdp.py eval  "http://127.0.0.1:8765/#rules" "document.title"
  python reports/_tmp/cdp.py shot  "http://127.0.0.1:8765/#rules" out.png 1600 1000
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

EDGE = os.environ.get("CDP_EDGE", "")


def _find_edge() -> str:
    """按 %PROGRAMFILES(X86)% / %PROGRAMFILES% 找 Edge，不在代码里写盘符（R4）。"""
    if EDGE:
        return EDGE
    for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(key)
        if not base:
            continue
        cand = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.exists(cand):
            return cand
    return "msedge"
PORT = int(os.environ.get("CDP_PORT", "9333"))
#: 浏览器 profile 放 reports/_tmp（不要放进 tools/：会被当成源码提交，且体积 480MB）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.environ.get("CDP_PROFILE") or os.path.join(ROOT_DIR, "reports", "_tmp", "edgeprofile")


# ---------------------------------------------------------------- WebSocket
class WS:
    """最小 WebSocket 客户端（只做 text frame，够 CDP 用）。"""

    def __init__(self, url: str) -> None:
        assert url.startswith("ws://")
        rest = url[5:]
        host_port, path = rest.split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        assert b"101" in buf.split(b"\r\n")[0], buf[:120]

    def _send_frame(self, payload: bytes) -> None:
        header = bytearray([0x81])              # FIN + text
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def recv(self) -> str:
        chunks = []
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            data = self._recv_exact(ln)
            chunks.append(data)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def send(self, obj: dict) -> None:
        self._send_frame(json.dumps(obj).encode())


# ---------------------------------------------------------------- browser
def _http_json(path: str):
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("DevTools 端口没起来")


def ensure_browser() -> None:
    # 探针默认**不再自己拉起浏览器**：headless 实例会抢占 9334，
    # 于是所有检查都落在另一个 profile 上（曾据此三次误判"未登录"）。
    if os.environ.get("CDP_NO_SPAWN"):
        if not _alive():
            raise RuntimeError("9334 无实例，且 CDP_NO_SPAWN=1（请先启动带调试端口的 Edge）")
        return
    if not _alive():
        subprocess.Popen([
            _find_edge(), "--headless=old", "--disable-gpu", "--no-sandbox", "--no-first-run",
            "--disable-extensions", f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={PORT}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _http_json("/json/version")


def _alive() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


def open_session() -> tuple[WS, dict]:
    target = next(t for t in _http_json("/json") if t["type"] == "page")
    ws = WS(target["webSocketDebuggerUrl"])
    return ws, target


class Session:
    def __init__(self) -> None:
        ensure_browser()
        self.ws, self.info = open_session()
        self.seq = 0
        self.cmd("Page.enable")

    def cmd(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        mid = self.seq
        self.ws.send({"id": mid, "method": method, "params": params or {}})
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def goto(self, url: str, wait_selector: str = "", timeout: float = 25) -> None:
        self.cmd("Page.navigate", {"url": url})
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.4)
            if not wait_selector:
                time.sleep(1.2)
                return
            got = self.eval(
                f"!!document.querySelector({json.dumps(wait_selector)}) && "
                f"document.readyState === 'complete'")
            if got is True:
                time.sleep(0.4)
                return
        raise TimeoutError(f"等待 {wait_selector} 超时")

    def eval(self, expr: str):
        r = self.cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    def shot(self, path: str, width: int, height: int) -> None:
        self.cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.5)
        r = self.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        open(path, "wb").write(base64.b64decode(r["data"]))


if __name__ == "__main__":
    action = sys.argv[1]
    s = Session()
    if action == "eval":
        s.goto(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 else "")
        print(json.dumps(s.eval(sys.argv[3]), ensure_ascii=False, indent=2))
    elif action == "shot":
        s.goto(sys.argv[2], sys.argv[6] if len(sys.argv) > 6 else "")
        s.shot(sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
        print("saved", sys.argv[3])
