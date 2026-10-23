# -*- coding: utf-8 -*-
"""nfra.attachment_fetch — **取数层**（E5 第二层，2026-10-19）

职责：附件 URL 构造 + 带重试的二进制 GET。**只负责"怎么把字节拿回来"**，
不解析、不落盘（那是 process/report 的事）。

函数体自 E5 起**逐字保留**（含原注释），仅改依赖来源。
依赖方向：fetch ← process ← fetch_attachments（本层不依赖任何层）。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：路径派生常量计算需要它
del _p, _os, _sys

import random
import ssl
import time
import urllib.error
import urllib.request

BASE = "https://www.nfra.gov.cn"

# ⚠️ 2026-10-23（TD-28）：`_SSL_CTX` / `DEFAULT_HEADERS` 曾在此**重复三份**
# （拆分时的粘贴残留）。重复的后果不是"多几行"，而是**改一份忘改另一份**：
# 改 UA 只改了第一份，后面覆盖回旧值，排障时看到的 UA 与源码不一致。
# 保留**唯一一份**，并由守卫 `test_no_duplicate_module_constants` 固定。
_SSL_CTX = ssl.create_default_context()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/cn/view/pages/ItemDetail.html",
}


def http_get_bytes(url, timeout=30, max_retries=4):
    """带超时与退避重试的二进制 GET；非 200 抛异常由调用方冷却处理。"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                status = resp.getcode()
                if status != 200:
                    raise urllib.error.HTTPError(url, status, "HTTP %d" % status, None, None)  # type: ignore[arg-type]
                return resp.read()
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print("      [att retry %d/%d] %s，%.1fs 后重试" % (attempt, max_retries, e, wait),
                      flush=True)
                time.sleep(wait)
    raise RuntimeError("附件下载失败（重试 %d 次）: %s" % (max_retries, last_err))


def build_attachment_url(att):
    """拼接附件绝对下载 URL。优先 urlOtherName，回退 attachmentUrl+attachmentName。"""
    uo = (att.get("urlOtherName") or "").strip()
    if uo:
        return BASE + (uo if uo.startswith("/") else "/" + uo)
    au = (att.get("attachmentUrl") or "").strip().lstrip("/")
    name = (att.get("attachmentName") or "").strip()
    if au and name:
        return BASE + "/" + au.rstrip("/") + "/" + name
    if name:
        return BASE + "/" + name
    return ""





# ----------------- Office 文档抽取（零依赖：zipfile + xml）-----------------
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"








# 高频常用中文字白名单：用于「全流扫描回收」的精度闸门，过滤二进制误解码乱码
# （mojibake 多由罕见生僻字构成，几乎不含下列常用字）。覆盖监管/金融/表格常用词。
# ---- E5 拆分（2026-10-16，D-遗留第五批）：纯判定层 → nfra/attachment_quality ----
# 职责：附件质量标志的**纯计算**（零触网/零落盘/零磁盘读）⇒ 可被纯函数回归完整锁住。
# 本层原在下方（_COMMON_CN + quality_flags），函数体**逐字搬运未改**。
# 双形态 import（包形态相对/ 脚本形态裸名）+ 显式赋值 re-export（保持外部调用面）。
try:
    from . import attachment_quality as _qual
except ImportError:  # pragma: no cover - 脚本形态
    import attachment_quality as _qual  # type: ignore[no-redef]

_COMMON_CN = _qual._COMMON_CN
quality_flags = _qual.quality_flags


