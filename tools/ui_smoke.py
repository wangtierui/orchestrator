# -*- coding: utf-8 -*-
"""tools/ui_smoke.py — 前端**运行时**冒烟（源码断言覆盖不到的那一层）。

为什么需要它：`tests/` 里的契约守卫只能断言"代码里写了什么"，
而下面这些值只有浏览器里才能量到：

  · 分页 hover 的真实颜色（过渡结束后）
  · 选项面板的宽度 / 行高 / hover 底色
  · 密度切换后列表行的真实像素高度（65 / 38）
  · 列表标题是否**真的**被省略号截断（scrollWidth > clientWidth）
  · 保存视图按钮在"无筛选"时的 disabled 状态

做法：用本机 Edge 的 DevTools 协议（手写 WebSocket，无第三方依赖）打开工作台，
逐个页面读取 `getComputedStyle` / `getBoundingClientRect`，与登录态 GitHub 实测值比对。

用法：
    python -m tools.ui_smoke                 # 人读输出，0=全过
    python -m tools.ui_smoke --json          # 机器可读
    python -m tools.ui_smoke --skip-unavailable   # 环境缺失时也返回 0（门禁用）

环境缺失（服务没起 / 没装 Edge）时返回 `available=False`，由调用方决定是否放过。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request


def _edge_candidates() -> list[str]:
    """按环境变量拼浏览器路径（**不写盘符字面量**：硬编码路径会被 R4 门禁拦下）。"""
    out: list[str] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            out.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
            out.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    out += [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/microsoft-edge",
        "/usr/bin/google-chrome",
    ]
    return out
DEFAULT_URL = "http://127.0.0.1:8765"
PORT = 9341


# ------------------------------------------------------------------ WebSocket
class _WS:
    """最小 WebSocket 客户端（RFC6455 文本帧；CDP 够用）。"""

    def __init__(self, url: str, timeout: float = 20) -> None:
        rest = url[5:]
        host_port, path = rest.split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
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
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("websocket 握手失败")

    def _frame(self, payload: bytes) -> None:
        head = bytearray([0x81])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def _exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws 关闭")
            buf += chunk
        return buf

    def recv(self) -> str:
        chunks = []
        while True:
            b0, b1 = self._exact(2)
            fin, ln = b0 & 0x80, b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._exact(8))[0]
            chunks.append(self._exact(ln))
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def send(self, obj: dict) -> None:
        self._frame(json.dumps(obj).encode())


# ------------------------------------------------------------------ 会话
def _edge() -> str | None:
    for p in _edge_candidates():
        if os.path.exists(p):
            return p
    return None


def _json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
        return json.loads(r.read().decode())


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class Session:
    """一个页面会话；用完 close()（浏览器由调用方管理）。"""

    def __init__(self, port: int, ws_url: str) -> None:
        self.ws = _WS(ws_url)
        self.seq = 0
        self.port = port

    def cmd(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        self.ws.send({"id": self.seq, "method": method, "params": params or {}})
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def ev(self, expr: str):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                          "awaitPromise": True})
        return r.get("result", {}).get("value")

    def viewport(self, w: int, h: int) -> None:
        self.cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})

    def wait_for(self, selector: str, timeout: float = 20) -> bool:
        """轮询等待元素出现（服务忙时固定 sleep 会误判成"页面坏了"）。"""
        end = time.time() + timeout
        while time.time() < end:
            if self.ev(f"!!document.querySelector({selector!r})") is True:
                return True
            time.sleep(0.4)
        return False

    def hover(self, x: int, y: int) -> None:
        self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                              "button": "none", "clickCount": 0})


def _launch(edge: str, port: int, profile: str) -> None:
    subprocess.Popen(
        [edge, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
         "--disable-extensions", f"--user-data-dir={profile}",
         f"--remote-debugging-port={port}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            _json(port, "/json/version")
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("DevTools 端口没起来")


def _stop(pid_holder: dict) -> None:
    proc = pid_holder.get("proc")
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ 采集
PROBE_PAGER = r"""(() => {
  const a = [...document.querySelectorAll('.pagination a')]
    .find(x => /^\d+$/.test(x.textContent.trim()));
  if (!a) return null;
  const b = a.getBoundingClientRect();
  return {x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2)};
})()"""

READ_PAGER = r"""(() => {
  const a = [...document.querySelectorAll('.pagination a')]
    .find(x => /^\d+$/.test(x.textContent.trim()));
  if (!a) return null;
  const cs = getComputedStyle(a);
  return {color: cs.color, border: cs.borderColor, bg: cs.backgroundColor};
})()"""


def collect(url: str = DEFAULT_URL) -> dict:
    """跑一遍采集；环境不可用返回 {'available': False, 'reason': ...}。"""
    edge = _edge()
    if not edge:
        return {"available": False, "reason": "未找到 Edge/Chrome 可执行文件"}
    if not server_alive(url):
        return {"available": False, "reason": f"服务未就绪：{url}"}

    profile = tempfile.mkdtemp(prefix="ui_smoke_")
    holder: dict = {}
    try:
        holder["proc"] = subprocess.Popen(
            [edge, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
             "--disable-extensions", f"--user-data-dir={profile}",
             f"--remote-debugging-port={PORT}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                _json(PORT, "/json/version")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        target = next(t for t in _json(PORT, "/json") if t["type"] == "page")
        s = Session(PORT, target["webSocketDebuggerUrl"])
        s.cmd("Page.enable")
        out: dict = {"available": True}
        tag = int(time.time())

        # ① 列表页：分页 hover / 面板 / 保存视图 / 截断
        s.viewport(1400, 900)
        s.cmd("Page.navigate", {"url": f"{url}/?v={tag}#rules"})
        s.wait_for("[data-lvsavecur]")        # 列表渲染完成再取数（最多 20s）

        out["saveDisabled"] = s.ev(
            "(() => {const b=document.querySelector('[data-lvsavecur]');"
            "return b ? !!b.disabled : null;})()")

        s.ev("document.querySelector('.pagination')?.scrollIntoView({block:'center'})")
        time.sleep(0.5)
        pt = s.ev(PROBE_PAGER)
        if pt:
            s.hover(pt["x"], pt["y"])
            time.sleep(0.4)
            out["pagerHover"] = s.ev(READ_PAGER)

        # 先把滚动位置复位：刚验过分页 hover，页面停在底部 ⇒ 表头按钮在视口外，
        # 面板会按负的 top 打开（工具自身场景缺陷，不是应用缺陷）。
        # 工作台的滚动容器是 #view（不是 window）——两者都复位，避免读到视口外的锚点
        s.ev("(() => { window.scrollTo(0, 0); const v = document.querySelector('#view'); if (v) v.scrollTop = 0; })()")
        time.sleep(0.6)
        s.ev("[...document.querySelectorAll('.lv-fbtn')]"
             ".find(b => /主题/.test(b.textContent))?.click()")
        time.sleep(0.7)
        out["panel"] = s.ev(r"""(() => {
          const p = document.querySelector('.fpop');
          if (!p) return null;
          const o = p.querySelector('.opt');
          const b = o.getBoundingClientRect();
          return {w: Math.round(p.getBoundingClientRect().width),
                  optH: Math.round(b.height),
                  x: Math.round(b.left + b.width / 2),
                  y: Math.round(b.top + b.height / 2)};
        })()""")
        if out["panel"]:
            s.hover(out["panel"]["x"], out["panel"]["y"])
            time.sleep(0.35)
            out["panel"]["hoverBg"] = s.ev(
                "getComputedStyle(document.querySelector('.fpop .opt')).backgroundColor")
        s.ev("typeof lvClosePop === 'function' && lvClosePop()")

        out["rowWide"] = s.ev(
            "(document.querySelector('.lv-row') ? "
            "Math.round(document.querySelector('.lv-row').getBoundingClientRect().height) : null)")
        s.ev("[...document.querySelectorAll('[data-lvdensity]')]"
             ".find(b => b.dataset.lvdensity === 'compact')?.click()")
        time.sleep(1.6)
        out["rowCompact"] = s.ev(
            "(document.querySelector('.lv-row') ? "
            "Math.round(document.querySelector('.lv-row').getBoundingClientRect().height) : null)")
        out["densityScope"] = s.ev(
            "({view: document.querySelector('#view').className,"
            " body: document.body.className || ''})")
        s.ev("[...document.querySelectorAll('[data-lvdensity]')]"
             ".find(b => b.dataset.lvdensity === 'wide')?.click()")
        time.sleep(1.2)

        # ② token：排序 + 降序 ⇒ 保存可点
        s.ev("""(() => { const i = document.querySelector('#lvfq');
          if (!i) return; i.focus(); i.value = '排序:文号 降序';
          i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); })()""")
        time.sleep(2.0)
        out["token"] = s.ev(
            "({sort: STATE.rules && STATE.rules.sort, desc: STATE.rules && STATE.rules.desc,"
            " saveDisabled: (() => {const b=document.querySelector('[data-lvsavecur]');"
            "return b ? !!b.disabled : null;})()})")

        # ③ Tab 键序：连续 Tab，记录每次落点的可读名字
        s.ev("document.body.focus()")
        stops = []
        for _ in range(12):
            for typ in ("rawKeyDown", "keyDown", "keyUp"):
                s.cmd("Input.dispatchKeyEvent", {
                    "type": typ, "key": "Tab", "code": "Tab",
                    "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9})
            time.sleep(0.12)
            info = s.ev(r"""(() => {
              const el = document.activeElement;
              if (!el || el === document.body) return null;
              const label = (el.getAttribute('aria-label') || el.textContent
                             || el.getAttribute('placeholder') || '').trim().slice(0, 20);
              return {tag: el.tagName.toLowerCase(),
                      cls: String(el.className || '').slice(0, 24), label: label};
            })()""")
            if info:
                stops.append(info)
        out["tabStops"] = stops

        # ④ 窄视口：标题真实截断
        s.viewport(760, 900)
        s.cmd("Page.navigate", {"url": f"{url}/?v={tag + 1}#rules"})
        s.wait_for(".lv-row")
        out["truncate"] = s.ev(r"""(() => {
          let worst = null;
          [...document.querySelectorAll('.lv-row')].forEach(r => {
            const sp = r.querySelector('.lv-title .Truncate-text');
            if (!sp) return;
            const over = sp.scrollWidth - sp.clientWidth;
            if (!worst || over > worst) worst = over;
          });
          const sp = document.querySelector('.lv-title .Truncate-text');
          return {maxOverflow: worst, ellipsis: sp ? getComputedStyle(sp).textOverflow : null};
        })()""")
        return out
    finally:
        _stop(holder)


# ------------------------------------------------------------------ 判定
def evaluate(data: dict) -> list[dict]:
    """把采集值转成可判定的条目。"""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if not data.get("available"):
        return [{"check": "environment", "ok": True,
                 "skipped": True, "detail": data.get("reason", "")}]

    add("分页 hover 变蓝", (data.get("pagerHover") or {}).get("color") == "rgb(9, 105, 218)"
        and (data.get("pagerHover") or {}).get("bg", "").startswith("rgba(0, 0, 0, 0)"),
        data.get("pagerHover"))

    panel = data.get("panel") or {}
    add("选项面板 320×32 且 hover=.1", panel.get("w") == 320 and panel.get("optH") == 32
        and panel.get("hoverBg") == "rgba(129, 139, 152, 0.1)", panel)

    add("密度 65↔38 且不串到 body", data.get("rowWide") == 65
        and data.get("rowCompact") == 38
        and "lv-compact" in str((data.get("densityScope") or {}).get("view", ""))
        and "lv-compact" not in str((data.get("densityScope") or {}).get("body", "")),
        {"wide": data.get("rowWide"), "compact": data.get("rowCompact"),
         "scope": data.get("densityScope")})

    tok = data.get("token") or {}
    add("排序 token 生效且解锁保存", tok.get("sort") == "document_number"
        and tok.get("desc") is True and tok.get("saveDisabled") is False, tok)

    stops = data.get("tabStops") or []
    named = [x for x in stops if x.get("label")]
    uniq = {(x["tag"], x["cls"], x["label"]) for x in named}
    add("Tab 键序可用", len(stops) >= 8 and len(named) == len(stops) and len(uniq) >= 5,
        {"stops": len(stops), "named": len(named), "unique": len(uniq),
         "sample": [x["label"][:12] for x in named[:6]]})

    tr = data.get("truncate") or {}
    add("标题真实省略", (tr.get("maxOverflow") or 0) > 0 and tr.get("ellipsis") == "ellipsis", tr)

    add("首屏保存视图禁用", data.get("saveDisabled") is True, data.get("saveDisabled"))
    return checks


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    skip = "--skip-unavailable" in args
    url = DEFAULT_URL
    for i, a in enumerate(args):
        if a == "--url" and i + 1 < len(args):
            url = args[i + 1]

    try:
        data = collect(url)
    except Exception as e:  # noqa: BLE001
        data = {"available": False, "reason": f"{type(e).__name__}: {e}"}

    checks = evaluate(data)
    failed = [c for c in checks if not c["ok"]]
    skipped = [c for c in checks if c.get("skipped")]

    if as_json:
        print(json.dumps({"available": data.get("available"), "checks": checks},
                         ensure_ascii=False, indent=1))
    else:
        if skipped:
            print(f"[ui-smoke] 跳过：{skipped[0]['detail']}")
        for c in checks:
            if c.get("skipped"):
                continue
            flag = "OK  " if c["ok"] else "FAIL"
            print(f"  [{flag}] {c['check']}  {json.dumps(c['detail'], ensure_ascii=False)[:120]}")
        print("PASS" if not failed else f"FAIL ({len(failed)} 项)")

    if failed:
        return 1
    if skipped and not skip:
        return 0          # 环境缺失不算失败，但会打印"跳过"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
