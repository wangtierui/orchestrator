# -*- coding: utf-8 -*-
"""mof 源：网络层 + 共享工具（重构版；逻辑与旧 mof_attachments / mof_collector 等价）。

仅依赖标准库 + std_lib.scraper_std（cache_store / crawler_common）。
"""
import os as _os
import sys as _sys

# 仓库引导：refactor/collectors/mof/ → 上溯 3 级到仓库根，使 std_lib 可导入
_GUIDE_ROOT = _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..")
)
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

import html
import json
import logging
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
UTC = timezone.utc

try:
    from std_lib.scraper_std.crawler_common import (
        USER_AGENTS as CC_USER_AGENTS,
        extract_document_text,
        rich_object_fields,
        safe_filename as _cc_safe_filename,
        structured_table_fields,
    )
except ImportError:  # pragma: no cover
    CC_USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]

    def extract_document_text(data, name="", **kw):
        return {"text": "", "kind": "unknown", "extracted": False,
                "extract_status": "library_missing", "sha256": "",
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data)}

    def structured_table_fields(data, name="", *, kind=None):
        return {}

    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):
        return {}

    def _cc_safe_filename(name, ext, max_len=120):
        return re.sub(r'[\\/:*?"<>|]', "_", str(name or "")).strip()[:max_len]

try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
except ImportError:  # pragma: no cover
    class OfflineMiss(Exception):
        pass

    def bind_source_cache(*a, **k):
        raise OfflineMiss("cache_store unavailable")

try:
    from std_lib.scraper_std.cache_store import docs_root
except ImportError:  # pragma: no cover
    def docs_root(*a, **k):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "docs", *a)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = "http://fgk.mof.gov.cn"
BASE = "/dev"  # /dev/lawFile/list 等
ATTACHMENTS_DIR = docs_root("mof", "attachments")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": HOST,
    "Referer": HOST + "/ui/start/",
}

logger = logging.getLogger("mof_scraper")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


class RateLimiter:
    """请求频率限制 + 限流自适应降温（penalize）。"""

    def __init__(self, min_delay=0.3, max_delay=0.7, max_cooldown=180.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_cooldown = max_cooldown
        self.cooldown = 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            target = random.uniform(self.min_delay, self.max_delay) + self.cooldown
            elapsed = now - self._last
            if elapsed < target:
                time.sleep(target - elapsed)
            self._last = time.time()
            self.cooldown = max(0.0, self.cooldown * 0.5)

    def penalize(self, extra):
        if extra <= 0:
            return
        with self._lock:
            self.cooldown = min(self.max_cooldown, self.cooldown + extra)


_RESP = None
_OfflineMiss = OfflineMiss


def _init_cache(path=None, offline=False):
    global _RESP
    _RESP = bind_source_cache("mof", "json", root=path)
    if offline and _RESP is not None:
        _RESP.set_offline(True)


def set_cache_dir(path):
    _init_cache(path)


def set_offline(flag):
    if _RESP is not None:
        _RESP.set_offline(flag)


_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


def _mof_cache_key(method, path, payload):
    full_url = HOST + path
    if method == "GET" and "?" in path:
        ep, q = path.split("?", 1)
        params = dict(urllib.parse.parse_qsl(q))
        endpoint = HOST + ep
    elif method == "POST":
        endpoint = full_url
        params = dict(payload or {})
    else:
        endpoint = full_url
        params = {}
    flat = {}
    for k, v in params.items():
        flat[k] = json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return endpoint, flat


def _request(method, path, payload=None, *, rate=None, timeout=30, max_retries=4):
    endpoint, params = _mof_cache_key(method, path, payload)
    if _RESP is not None:
        cp = _RESP.path(endpoint, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:  # noqa: BLE001
                pass
        if _RESP.offline:
            raise _OfflineMiss("%s" % endpoint)
    url = HOST + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = dict(DEFAULT_HEADERS)
    if method == "POST":
        headers["Content-Type"] = "application/json;charset=UTF-8"
    headers["User-Agent"] = random.choice(CC_USER_AGENTS)
    if rate is not None:
        rate.wait()
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            if _RESP is not None and isinstance(data, dict) and data.get("code") == 200:
                try:
                    _RESP.put(endpoint, params, data)
                except Exception:  # noqa: BLE001
                    pass
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 502, 500):
                backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning("HTTP %s @ %s (尝试 %d/%d)，%s 秒后重试",
                               e.code, path, attempt, max_retries, round(backoff, 1))
                time.sleep(backoff)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            backoff = min(2 ** attempt, 15) + random.uniform(0, 1)
            logger.warning("网络异常 @ %s (尝试 %d/%d)：%s，%s 秒后重试",
                           path, attempt, max_retries, e, round(backoff, 1))
            time.sleep(backoff)
            continue
    raise RuntimeError(f"请求失败（已达最大重试）：{path} -> {last_err}")


def html_to_text(s):
    if not s or not isinstance(s, str):
        return ""
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def normalize_date(v):
    if v is None:
        return None
    if isinstance(v, list) and len(v) >= 3:
        try:
            return f"{int(v[0]):04d}-{int(v[1]):02d}-{int(v[2]):02d}"
        except Exception:  # noqa: BLE001
            return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "None", "null", "[]"):
            return None
        m = re.match(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return v.split(" ")[0] if " " in v else v
    return str(v)


def safe_filename(name, ext, fallback):
    name = _cc_safe_filename(name or fallback, "", 120)
    if ext and not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name
