# -*- coding: utf-8 -*-
"""mof 源：网络层 + 共享工具（重构版；逻辑与旧 mof_attachments / mof_collector 等价）。

仅依赖 Python 标准库 + 本包内 lib（自包含，不触碰外部 std_lib / modules）。
"""
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
from datetime import datetime, timezone
UTC = timezone.utc

# 以下为原 std_lib.scraper_std.crawler_common 中 mof 实际用到的几个函数的最小本地实现，
# 使本包不依赖外部 std_lib（自包含、零副本）。
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

from ..lib.paths import docs_root
from ..lib.text_utils import html_to_text, normalize_date

# refactor 项目根（mof/net.py 位于 refactor/collectors/mof/，上两级即 refactor/）。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


def _request(method, path, payload=None, *, rate=None, timeout=30, max_retries=4):
    """带重试/退避/UA 轮换的请求（纯实时，HTTP 响应缓存已移除）。

    统一走 lib/http（requests 底座），自动接入容错编排层（熔断/死信/指标）；
    重试与 RateLimiter 惩罚逻辑保留（transport 仅做单次请求，重试由本循环控制）。
    """
    from ..lib.http import http_request

    url = HOST + path
    headers = dict(DEFAULT_HEADERS)
    if method == "POST":
        headers["Content-Type"] = "application/json;charset=UTF-8"
    headers["User-Agent"] = random.choice(CC_USER_AGENTS)
    if rate is not None:
        rate.wait()
    last_err = None
    for attempt in range(1, max_retries + 1):
        resp = http_request(
            method, url, json_body=payload, headers=headers, timeout=timeout, retries=0,
        )
        status = resp.status
        if status is None:  # 网络层失败
            last_err = resp.error
            backoff = min(2 ** attempt, 15) + random.uniform(0, 1)
            logger.warning("网络异常 @ %s (尝试 %d/%d)：%s，%s 秒后重试",
                           path, attempt, max_retries, last_err, round(backoff, 1))
            time.sleep(backoff)
            continue
        if status in (429, 503, 502, 500):
            backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
            if rate is not None:
                rate.penalize(backoff)
            logger.warning("HTTP %s @ %s (尝试 %d/%d)，%s 秒后重试",
                           status, path, attempt, max_retries, round(backoff, 1))
            time.sleep(backoff)
            continue
        if 200 <= status < 300:
            try:
                return json.loads(resp.content.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                raise RuntimeError(f"JSON 解析失败：{path}")
        # 其它状态码（含 404/410）：按永久失败抛出，由 pipeline 跳过
        raise urllib.error.HTTPError(url, status, "HTTP %d" % status, None, None)
    raise RuntimeError(f"请求失败（已达最大重试）：{path} -> {last_err}")







def safe_filename(name, ext, fallback):
    name = _cc_safe_filename(name or fallback, "", 120)
    if ext and not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name
