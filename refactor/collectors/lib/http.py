# -*- coding: utf-8 -*-
"""统一加固 HTTP 客户端（传输层）。

设计目标
--------
- 所有采集器共用**唯一**传输实现，消除各采集器私有的 net/http 副本。
- `Transport` 为抽象接口；`RequestsTransport` 是当前默认实现（基于 requests）。
- 以后要加“浏览器模仿”（TLS 伪装 / 无头浏览器）只需新增一个 `Transport` 子类
  并在 `get_default_transport()` 切换，**采集器与容错层无需改动**。
- 单请求退避/重试在本层；全局熔断/失败分类/死信在 `lib/resilience`（P1）。

向后兼容
--------
- `USER_AGENTS` / `BASE_HEADERS` / `is_attachment_url` / `robust_get` 保持原签名与语义，
  gov 等既有调用方不受影响。
"""
from __future__ import annotations

import abc
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .resilience import get_coordinator

LOG = logging.getLogger("refactor.collectors.lib.http")


class FetchError(RuntimeError):
    """请求失败异常，携带 HTTP 状态码，便于上层做失败分类/死信。"""

    def __init__(self, status: int | None, error: str | None = None):
        self.status = status
        self.error = error
        super().__init__(
            f"HTTP {status}" if status is not None else f"请求失败: {error}"
        )


def set_active_source(source: str) -> None:
    """声明当前抓取源；transport 据此把请求结果上报到对应熔断/指标桶。"""
    get_coordinator().set_source(source)

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

# 默认状态分类（P1 会把 403 也纳入重试；此处保持与旧 robust_get 一致，避免改动 gov 行为）
DEFAULT_NO_RETRY = frozenset({403, 404, 410})
DEFAULT_RETRY = frozenset({429, 500, 502, 503, 504})


@dataclass
class TransportResponse:
    """统一响应载体：status=None 表示网络层彻底失败。"""
    status: int | None
    content: bytes | None = None
    error: str | None = None
    headers: dict = field(default_factory=dict)


class Transport(abc.ABC):
    """传输层抽象接口。

    所有采集器只依赖本接口（request 方法），不关心底层是 requests 还是无头浏览器。
    子类实现 `request` 即可接入。
    """

    @abc.abstractmethod
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = 30,
        retries: int = 4,
        retry_statuses: frozenset[int] = DEFAULT_RETRY,
        no_retry_statuses: frozenset[int] = DEFAULT_NO_RETRY,
        data: bytes | None = None,
        json_body: Any | None = None,
    ) -> TransportResponse:
        ...


def _backoff_wait(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return float(int(retry_after))
    return min(2 ** attempt, 16) + random.uniform(0, 1)


class RequestsTransport(Transport):
    """基于 requests 的默认传输实现：UA 轮换 + 富请求头 + 指数退避 + 尊重 Retry-After。"""

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int = 30,
        retries: int = 4,
        retry_statuses: frozenset[int] = DEFAULT_RETRY,
        no_retry_statuses: frozenset[int] = DEFAULT_NO_RETRY,
        data: bytes | None = None,
        json_body: Any | None = None,
    ) -> TransportResponse:
        hdrs = dict(BASE_HEADERS)
        hdrs["User-Agent"] = random.choice(USER_AGENTS)
        if headers:
            hdrs.update(headers)

        # 熔断冷却：若该源被熔断，先暂停等待（对采集器透明）
        get_coordinator().pause_if_needed()

        last_err: str | None = None
        for attempt in range(1, retries + 1):
            try:
                if attempt > 1:
                    time.sleep(random.uniform(0.8, 1.6))
                resp = self._session.request(
                    method, url, params=params, json=json_body, data=data,
                    headers=hdrs, timeout=timeout,
                )
                get_coordinator().record_request(resp.status_code)
                if resp.status_code in no_retry_statuses:
                    return TransportResponse(
                        resp.status_code, resp.content, headers=dict(resp.headers)
                    )
                if resp.status_code in retry_statuses:
                    wait = _backoff_wait(attempt, resp.headers.get("Retry-After"))
                    LOG.warning(
                        "HTTP %s @ %s 限流/暂不可用，%ss 后重试(%d/%d)",
                        resp.status_code, url, round(wait, 1), attempt, retries,
                    )
                    time.sleep(wait)
                    last_err = f"HTTP {resp.status_code}"
                    continue
                return TransportResponse(
                    resp.status_code, resp.content, headers=dict(resp.headers)
                )
            except requests.RequestException as e:
                get_coordinator().record_request(None)
                last_err = f"{type(e).__name__}: {e}"
                wait = _backoff_wait(attempt, None)
                LOG.warning(
                    "网络异常 @ %s，%ss 后重试(%d/%d): %s",
                    url, round(wait, 1), attempt, retries, e,
                )
                time.sleep(wait)
                continue
        LOG.error("GET %s 重试 %d 次仍失败：%s", url, retries, last_err)
        return TransportResponse(None, None, error=last_err)


_DEFAULT_TRANSPORT: Transport = RequestsTransport()


def get_default_transport() -> Transport:
    """返回当前默认传输实例。

    TODO(T3)：在此按配置切换 RequestsTransport / ImpersonateTransport /
    PlaywrightTransport，采集器与容错层无需改动。
    """
    return _DEFAULT_TRANSPORT


# ---------------------------------------------------------------------------
# 向后兼容 API（gov 等既有调用方）
# ---------------------------------------------------------------------------
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
    content: 文本模式返回 str；binary 返回 bytes；no_retry 状态返回 None。
    """
    time.sleep(random.uniform(min_delay, max_delay))
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    resp = _DEFAULT_TRANSPORT.request(
        "GET", url, headers=headers, timeout=timeout, retries=retries,
        retry_statuses=DEFAULT_RETRY, no_retry_statuses=DEFAULT_NO_RETRY,
    )
    if resp.status is None:
        return None, resp.error
    if resp.status in DEFAULT_NO_RETRY:
        return resp.status, None
    if binary:
        return resp.status, resp.content
    return resp.status, resp.content.decode("utf-8", errors="replace")


def http_get_json(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = 25,
    retries: int = 4,
    retry_statuses: frozenset[int] = DEFAULT_RETRY,
    no_retry_statuses: frozenset[int] = DEFAULT_NO_RETRY,
    extra_headers: dict[str, str] | None = None,
    referer: str | None = None,
):
    """共享 JSON GET：成功返回解析后的 dict；失败抛 RuntimeError。

    默认 403 不重试（与旧 robust_get 一致）；采集器可按需在调用处放宽
    （如 nfra 将 403 也纳入重试，保留其原有行为）。
    """
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    resp = _DEFAULT_TRANSPORT.request(
        "GET", url, params=params, headers=headers, timeout=timeout, retries=retries,
        retry_statuses=retry_statuses, no_retry_statuses=no_retry_statuses,
    )
    if resp.status is None:
        raise FetchError(None, resp.error)
    if resp.status != 200:
        raise FetchError(resp.status)
    try:
        return json.loads(resp.content.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FetchError(None, "JSON 解析失败: %s" % e)


def http_request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    retries: int = 0,
    retry_statuses: frozenset[int] = DEFAULT_RETRY,
    no_retry_statuses: frozenset[int] = DEFAULT_NO_RETRY,
    referer: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    """通用请求：返回 TransportResponse（status=None 表示网络层彻底失败）。

    retries 默认 0：由调用方自行控制重试循环（如 mof 的 RateLimiter/penalize）。
    """
    hdrs: dict[str, str] = {}
    if referer:
        hdrs["Referer"] = referer
    if extra_headers:
        hdrs.update(extra_headers)
    return _DEFAULT_TRANSPORT.request(
        method, url, params=params, json_body=json_body, data=data, headers=hdrs,
        timeout=timeout, retries=retries, retry_statuses=retry_statuses,
        no_retry_statuses=no_retry_statuses,
    )


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
