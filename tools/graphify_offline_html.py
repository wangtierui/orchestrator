# -*- coding: utf-8 -*-
"""
tools/graphify_offline_html — 把 graphify-out/graph.html 的 vis-network CDN 依赖本地化。

背景（2026-09-20 排障）：
    graphify 生成的 graph.html 在 <head> 硬编码
        https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js （带 SRI + crossorigin）
    见 graphify/exporters/html.py:606，**无离线开关**。两类失败模式：
      ① unpkg 不可达（本机 SSL 连接失败）→ `vis` 未定义 → 首个 `new vis.DataSet(...)` 抛错
         → 整个内联 <script> 中断 → 页面只剩深色空壳（白屏）；
      ② 即便换成**本地**文件，若保留 `integrity`/`crossorigin`，`file://` 直开时页面是
         opaque origin，SRI 校验按 CORS 口径执行必失败 → 同样白屏（实测）。

本工具（幂等，可反复执行）：
    1) 确保 graphify-out/vendor/vis-network.min.js 存在；缺失则从可达镜像下载，
       **SHA-384 必须等于 graphify 声明的 SRI**（保证与官方发布字节一致），否则拒绝落盘；
       每次运行都会复验本地副本哈希（等价于把 SRI 校验搬到工具侧）；
    2) 把 vis-network 的 <script> 统一改写为极简本地引用（**不带 integrity/crossorigin**，
       否则 file:// 直开仍被拦）；
    3) 注入 `vis` 未定义的可见告警，避免再次"白屏且无任何提示"。

用法：
    python tools/graphify_offline_html.py                 # 本地化 graphify-out/graph.html
    python tools/graphify_offline_html.py --check         # 只检查不写盘（rc=1 表示需要修复）
    python tools/graphify_offline_html.py --html <path>   # 指定 HTML 文件

graphify 重新生成 graph.html（`graphify extract` / `cluster-only` / `update`）后，
再跑一次本工具即可恢复离线可用。
"""

import argparse
import base64
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
GRAPH_DIR = os.path.join(ROOT, "graphify-out")
VENDOR_REL = "vendor/vis-network.min.js"

# graphify/exporters/html.py:607 声明的 SRI —— 本地副本必须与其字节一致
INTEGRITY = "sha384-Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1"
# unpkg 在本机不可达，jsdelivr 可达；两者均分发同一 npm tarball，哈希一致
MIRRORS = (
    "https://cdn.jsdelivr.net/npm/vis-network@9.1.6/standalone/umd/vis-network.min.js",
    "https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js",
)

GUARD_ID = "graphify-offline-guard"
GUARD_JS = (
    '<script id="%s">\n'
    "// vis-network 未加载时给出可见提示，避免白屏且无任何反馈\n"
    "if (typeof vis === 'undefined') {\n"
    "  window.addEventListener('DOMContentLoaded', function () {\n"
    "    var el = document.getElementById('graph');\n"
    "    if (!el) return;\n"
    "    el.innerHTML = '<div style=\"padding:24px;font:14px/1.7 -apple-system,sans-serif;"
    "color:#ffb4b4\">'"
    " + '<b>vis-network 未加载，图形无法渲染。</b><br>'"
    " + '请确认 <code>vendor/vis-network.min.js</code> 与本页在同一目录树下，'"
    " + '或重新执行 <code>python tools/graphify_offline_html.py</code>。</div>';\n"
    "  });\n"
    "}\n"
    "</script>"
) % GUARD_ID

# 任何指向 vis-network.min.js 的 <script>（CDN 或旧版补丁产物，含任意属性，可跨行）
PAT_ANY = re.compile(
    r'<script\s+src="[^"]*%s"[^>]*>\s*</script>' % re.escape(os.path.basename(VENDOR_REL)),
    re.S,
)
# 极简本地引用：不带 integrity/crossorigin（file:// opaque origin 下二者都会导致加载失败）
MIN_TAG = '<script src="%s"></script>' % VENDOR_REL


def _sha384_b64_file(path: str) -> str:
    h = hashlib.sha384()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha384-" + base64.b64encode(h.digest()).decode("ascii")


def _download(url: str, dst: str) -> tuple:
    """下载到 dst 并校验 SRI；返回 (ok, msg)。失败不留半成品。"""
    tmp = dst + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        return False, "%s -> %s" % (url, exc)
    with open(tmp, "wb") as fh:
        fh.write(data)
    got = _sha384_b64_file(tmp)
    if got != INTEGRITY:
        os.remove(tmp)
        return False, "SRI 不匹配（%s）：got %s" % (url, got)
    os.replace(tmp, dst)
    return True, "%s -> %s (%d bytes)" % (url, dst, len(data))


def ensure_vendor(html_path: str) -> tuple:
    """确保本地副本存在且与官方 SRI 一致。返回 (ok, vendor_path_or_None, msg)。"""
    base_dir = os.path.dirname(os.path.abspath(html_path))
    dst = os.path.join(base_dir, *VENDOR_REL.split("/"))  # 与 HTML 里的相对路径保持一致
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst):
        if _sha384_b64_file(dst) == INTEGRITY:
            return True, dst, "已存在且 SRI 一致"
        return False, None, "本地副本 SRI 不匹配：%s" % dst
    for url in MIRRORS:
        ok, msg = _download(url, dst)
        if ok:
            return True, dst, msg
    return False, None, "全部镜像下载失败"


def patch_html(html_path: str) -> tuple:
    """统一 vis-network 引用为极简本地标签并注入 guard。返回 (changed, msg)。"""
    with open(html_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    m = PAT_ANY.search(text)
    if not m:
        return False, "未找到 vis-network 的 <script> 引用，文件结构可能已变化"

    changed = False
    if m.group(0) == MIN_TAG:
        msg = "已是极简本地引用"
    else:
        text = text[: m.start()] + MIN_TAG + text[m.end():]
        changed = True
        msg = "vis-network 引用 -> %s（去 integrity/crossorigin）" % VENDOR_REL

    if GUARD_ID not in text:
        anchor = text.index(MIN_TAG) + len(MIN_TAG)
        text = text[:anchor] + "\n" + GUARD_JS + text[anchor:]
        changed = True
        msg += "；注入 %s" % GUARD_ID

    if changed:
        tmp = html_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, html_path)
    return changed, msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="graphify graph.html 依赖本地化")
    ap.add_argument("--html", default=os.path.join(GRAPH_DIR, "graph.html"),
                    help="目标 HTML（默认 graphify-out/graph.html）")
    ap.add_argument("--check", action="store_true", help="只检查不写盘")
    args = ap.parse_args(argv)

    html_path = args.html
    if not os.path.isfile(html_path):
        print("[FAIL] 未找到 %s —— 先跑 graphify extract . --code-only 生成图谱" % html_path)
        return 1

    ok, vendor, vmsg = ensure_vendor(html_path)
    if not ok:
        print("[FAIL] vis-network 本地副本不可用：%s" % vmsg)
        return 1
    print("[OK] vendor: %s (%s)" % (vendor, vmsg))

    if args.check:
        with open(html_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        m = PAT_ANY.search(text)
        if not m or m.group(0) != MIN_TAG or GUARD_ID not in text:
            print("[NEED-FIX] %s 仍依赖 CDN/带 SRI 属性或缺少 guard" % html_path)
            return 1
        print("[OK] %s 已本地化" % html_path)
        return 0

    changed, msg = patch_html(html_path)
    print("[%s] %s: %s" % ("CHANGED" if changed else "OK", html_path, msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
