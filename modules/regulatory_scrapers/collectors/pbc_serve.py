#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地静态服务器 —— 用于预览 pbc_law_scraper 产出（index.html + pbc_laws.json + attachments/）。

为什么不直接双击 index.html？
  index.html 通过 fetch('pbc_laws.json') 读取数据、PDF.js 通过 XHR 拉取 PDF，
  这些在 file:// 协议下会被浏览器同源策略拦截。必须经 HTTP 提供。

能力：
  1. 多线程（ThreadingHTTPServer）—— 浏览器会并发拉取 JSON、多个附件与 PDF 分片，单线程会串行卡顿。
  2. 支持 HTTP Range 请求 —— PDF.js 多页预览、拖拽定位必须；标准库 SimpleHTTPRequestHandler 默认不支持，已补上。
  3. 正确的 MIME 类型 —— 覆盖 .json/.html/.doc/.docx/.pdf/.xls/.xlsx/.csv/.txt，避免浏览器误判。
  4. 路径穿越防护 —— 任何 .. 或绝对路径尝试返回 403。
  5. 目录回退 —— 访问 / 自动返回 index.html。
  6. 中文路径兼容 —— 用 urllib 安全转义，中文附件名可正常下载。

用法：
  python serve.py                 # 默认 http://127.0.0.1:8000
  python serve.py --port 9000     # 指定端口
  python serve.py --host 0.0.0.0  # 允许局域网访问（仅可信网络使用）

合规说明：本服务仅在本机/内网提供已抓取的公开法规数据，不对外暴露。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import os
import re
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")

# 显式 MIME 映射，覆盖标准库未识别或识别错误的类型
EXTRA_MIME = {
    ".json": "application/json; charset=utf-8",
    ".csv":  "text/csv; charset=utf-8",
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".wps":  "application/vnd.ms-works",
    ".rtf":  "application/rtf",
    ".ceb":  "application/octet-stream",
    ".html": "text/html; charset=utf-8",
    ".txt":  "text/plain; charset=utf-8",
}

def resolve_safe(url_path: str):
    """将 URL 路径解析为 ROOT 内的绝对磁盘路径；越界（含绝对盘符路径 / .. 上溯）返回 None。"""
    raw = url_path.split("?", 1)[0].split("#", 1)[0]
    raw = urllib.parse.unquote(raw)
    # 拼接到 ROOT。若 raw 自带盘符绝对路径（含盘符的 Windows 路径），
    # os.path.join 会丢弃 ROOT 前缀，但下面的包含性校验会拦下。
    rel = raw.lstrip("/").lstrip("\\")
    candidate = os.path.normpath(os.path.join(ROOT, rel))
    root_abs = os.path.abspath(ROOT)
    cand_abs = os.path.abspath(candidate)
    # 包含性校验：最终路径必须落在 ROOT 之内（防绝对路径逃逸与 .. 上溯）
    if cand_abs != root_abs and not cand_abs.startswith(root_abs + os.sep):
        return None
    return cand_abs

class RangeHTTPRequestHandler(SimpleHTTPRequestHandler):
    server_version = "PBC-Law-Server/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def translate_path(self, path):
        # 完全自管解析，不走父类（避免双重解码/前缀逻辑差异）
        r = resolve_safe(path)
        return r if r is not None else os.path.join(ROOT, "__NOPE__")

    def guess_type(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in EXTRA_MIME:
            return EXTRA_MIME[ext]
        return super().guess_type(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def _resolve(self):
        """返回最终要发送的磁盘路径（含目录→index.html 回退）。"""
        cand = resolve_safe(self.path)
        if cand is None:
            return None  # 越界（绝对路径逃逸 / .. 上溯）
        if os.path.isdir(cand):
            idx = os.path.join(cand, "index.html")
            return idx if os.path.isfile(idx) else None  # 不允许目录列表
        if os.path.isfile(cand):
            return cand
        return None

    def do_GET(self):
        try:
            fpath = self._resolve()
            if fpath is None:
                return self.send_error(404, "Not found")
            size = os.path.getsize(fpath)
            ctype = self.guess_type(fpath)
            rng = self.headers.get("Range", "").strip()
            m = re.match(r"bytes=(\d*)-(\d*)", rng)

            if m and (m.group(1) or m.group(2)):
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else size - 1
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                if self.command != "HEAD":
                    with open(fpath, "rb") as f:
                        f.seek(start)
                        self.wfile.write(f.read(length))
                return

            # 整文件
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if self.command != "HEAD":
                with open(fpath, "rb") as f:
                    while True:
                        buf = f.read(64 * 1024)
                        if not buf:
                            break
                        self.wfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端中断（如下载取消），忽略，避免日志噪声
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self.send_error(500, f"Server error: {e}")
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass

    do_HEAD = do_GET  # HEAD 复用同一逻辑（do_GET 内已对 HEAD 跳过 body）

def main():
    ap = argparse.ArgumentParser(description="pbc_law_scraper 本地预览服务器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    handler = partial(RangeHTTPRequestHandler)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"央行法规库预览服务已启动：{url}")
    print(f"  根目录：{ROOT}")
    print("  按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        httpd.server_close()

if __name__ == "__main__":
    main()
