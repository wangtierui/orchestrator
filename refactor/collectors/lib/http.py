# -*- coding: utf-8 -*-
"""本地 HTTP 加固客户端（移植自 std_lib.scraper_std.crawler_common 第 1 节）。

仅依赖标准库（urllib）。提供：UA 轮换、富请求头、随机延时、指数退避、尊重 Retry-After。
"""
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("refactor.collectors.lib.http")

# 桌面浏览器 UA 池（轮换以规避脚本识别）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,application/pdf;q=0.7,*/*;q=0.6",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_NO_RETRY_STATUS = frozenset({403, 404, 410})
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _safe_url(url: str) -> str:
    return (url or "").strip()


def _backoff_wait(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return float(int(retry_after))
    return min(2**attempt, 16) + random.uniform(0, 1)


def robust_get(
    url: str,
    *,
    binary: bool = False,
    timeout: int = 30,
    retries: int = 4,
    min_delay: float = 0.8,
    max_delay: float = 1.6,
    referer: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    """带反爬策略的 GET，返回 (status, content)。

    status: HTTP 状态码；失败（含重试耗尽）为 None。
    content: 文本模式返回 str；binary 返回 bytes；失败返回错误说明 str。
    """
    url = _safe_url(url)
    last_err = None
    for attempt in range(1, retries + 1):
        gap = random.uniform(min_delay, max_delay)
        time.sleep(gap)
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", random.choice(USER_AGENTS))
            for k, v in BASE_HEADERS.items():
                req.add_header(k, v)
            if referer:
                req.add_header("Referer", referer)
            if extra_headers:
                for k, v in extra_headers.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                data = resp.read()
                if binary:
                    return status, data
                enc = resp.headers.get_content_charset() or "utf-8"
                try:
                    return status, data.decode(enc, errors="replace")
                except LookupError:
                    return status, data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in _NO_RETRY_STATUS:
                return e.code, None
            if e.code in _RETRY_STATUS:
                wait = _backoff_wait(attempt, e.headers.get("Retry-After"))
                LOG.warning("HTTP %s @ %s 限流/暂不可用，%ss 后重试(%d/%d)",
                            e.code, url, round(wait, 1), attempt, retries)
                time.sleep(wait)
                continue
            return e.code, None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
            wait = _backoff_wait(attempt, None)
            LOG.warning("网络异常 @ %s，%ss 后重试(%d/%d): %s",
                        url, round(wait, 1), attempt, retries, e)
            time.sleep(wait)
            continue
    LOG.error("GET %s 重试 %d 次仍失败：%s", url, retries, last_err)
    return None, last_err


_ATTACH_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".wps", ".ceb", ".rtf", ".zip", ".rar")


def is_attachment_url(url: str, text: str = "") -> bool:
    """判断链接是否指向文档下载。"""
    if not url:
        return False
    low = url.split("?")[0].lower()
    if low.endswith(_ATTACH_EXT):
        return True
    kw = ("下载", "download", "word", "pdf", "全文", "附件", "doc", "xlsx", "excel")
    return any(k in (text or "").lower() for k in kw)
