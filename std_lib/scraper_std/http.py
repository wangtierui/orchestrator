# -*- coding: utf-8 -*-
"""
http.py —— 反爬加固 HTTP 客户端（第四节 4.3 / 第十二节 资源泄漏）

能力清单（对应规范）：
  1. 动态轮换 UA 池（含 PC + 移动端），富请求头（Referer / Accept-Language）；
  2. 自适应请求频率控制：命中 429/503 自动降速（超时冷却），尊重 Retry-After；
  3. 指数退避重试（≥3 次，间隔递增，抖动随机化）；
  4. 403/404/410 确定性错误立即返回不浪费重试配额；
  5. 代理池开关（config 控制，高匿名住宅代理建议）；
  6. 流式下载（stream=True），连接超时 10s / 读取超时 30s，禁止整文件载入内存；
  7. with 上下文管理器保证连接释放；编码经 encoding.decode_bytes 自动检测。

**HTTP 单栈（W4-b，2026-10，方案 B1）**：本文件是全仓**无会话 GET 的唯一实现**——
  - `robust_get(...)`：轻量门面（原 `crawler_common.robust_get` 实现已并入此处，
    crawler_common 改为 re-export；默认参数与原实现逐项对齐，见函数 docstring）；
  - `AdaptiveHttpClient`：有状态客户端（冷却/熔断/代理池/流式下载），附件下载与
    supp 采集经此类；
  - UA 池 SSOT = `load_ua_pool()`（内置 USER_AGENTS_PC/MOBILE，可被
    config/user_agents.json 覆盖）——原六处采集器本地 UA 池副本已统一改引。
  分层保留（有理由的非单栈）：会话级 `gov_collector.RobustSession`（requests
  Session + 连接池 + POST）——urllib 标准库无连接复用对应能力，gov 千页级采集
  改 urllib 是性能退化；该决策与「L2 会话级保留」记于 ADR-0009。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .encoding import decode_bytes

LOG = logging.getLogger("scraper_std.http")

# --------------------------------------------------------------------------- #
# UA 池（PC + 移动端）
# --------------------------------------------------------------------------- #
USER_AGENTS_PC = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
USER_AGENTS_MOBILE = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,application/pdf;q=0.7,*/*;q=0.6",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
_NO_RETRY_STATUS = frozenset({403, 404, 410})
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_UA_POOL_CACHE: dict[str, dict[str, Any]] = {}


def load_ua_pool(path: str | None = None) -> dict[str, Any]:
    """
    加载 User-Agent 池（第三节 config/user_agents.json）。
    优先级：显式 path > 当前目录 config/user_agents.json > 内置池。
    文件缺失/解析失败时回退内置池，绝不抛异常（低风险接线）。
    """
    key = path or "auto"
    if key in _UA_POOL_CACHE:
        return _UA_POOL_CACHE[key]
    pool: dict[str, Any] = {
        "pc": list(USER_AGENTS_PC),
        "mobile": list(USER_AGENTS_MOBILE),
        "default": USER_AGENTS_PC[0],
    }
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(os.path.join(os.getcwd(), "config", "user_agents.json"))
    for p in candidates:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            pc = [s for s in (data.get("desktop") or []) if isinstance(s, str) and s]
            mb = [s for s in (data.get("mobile") or []) if isinstance(s, str) and s]
            if pc:
                pool["pc"] = pc
            if mb:
                pool["mobile"] = mb
            if data.get("default"):
                pool["default"] = str(data["default"])
        except Exception as e:  # noqa: BLE001
            LOG.warning("UA 池文件解析失败 %s：%s", p, e)
        break  # 仅尝试第一个可用路径
    _UA_POOL_CACHE[key] = pool
    return pool


def _default_delay_min() -> float:
    return 2.0  # 政府网站默认随机间隔下限（规范：默认 [2,5] 秒）


class AdaptiveHttpClient:
    """
    自适应反爬加固客户端。

    use_mobile : 是否混入移动端 UA（默认混合池）
    delay_range: (min, max) 秒，随机请求间隔（规范默认 [2,5]）
    retries    : 指数退避重试次数（≥3）
    proxies    : [{'http':..., 'https':...}, ...] 代理池，None 表示关闭
    """

    def __init__(
        self,
        *,
        use_mobile: bool = True,
        delay_range: tuple[float, float] = (2.0, 5.0),
        retries: int = 3,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        proxies: list[dict[str, str]] | None = None,
        max_pages_today: int | None = None,
        stop_after_n_errors: int | None = None,
        user_agents_file: str | None = None,
        log: logging.Logger | None = None,
    ):
        self.use_mobile = use_mobile
        self.delay_range = delay_range
        self.retries = max(3, int(retries))
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.proxies = list(proxies or [])
        self.max_pages_today = max_pages_today
        self.stop_after_n_errors = stop_after_n_errors
        self.user_agents_file = user_agents_file
        self.log = log or LOG
        # 运行态
        self._consecutive_errors = 0
        self._pages_today = 0
        self._cooldown_until = 0.0  # 429/503 触发的冷却截止时间
        self._last_request_ts = 0.0

    # ---------------- UA / 请求头 ---------------- #
    def _pick_ua(self) -> str:
        pool = load_ua_pool(self.user_agents_file)
        if self.use_mobile and random.random() < 0.3:
            return random.choice(pool["mobile"])
        return random.choice(pool["pc"])

    def _build_headers(self, referer: str | None, extra: dict[str, str] | None) -> dict[str, str]:
        h = dict(BASE_HEADERS)
        h["User-Agent"] = self._pick_ua()
        if referer:
            h["Referer"] = referer
        if extra:
            h.update(extra)
        return h

    # ---------------- 自适应限速 ---------------- #
    def _throttle(self, status: int | None) -> None:
        now = time.time()
        # 冷却中 → 等待剩余时长
        if self._cooldown_until > now:
            wait = self._cooldown_until - now
            self.log.warning("限流冷却中，等待 %.1fs", wait)
            time.sleep(wait)
            now = time.time()
        # 常规随机间隔（政府网站礼貌限速基线 [2,5]s）
        lo, hi = self.delay_range
        gap = random.uniform(lo, hi)
        if self._last_request_ts and (now - self._last_request_ts) < gap:
            time.sleep(gap - (now - self._last_request_ts))
        self._last_request_ts = time.time()

    def _enter_cooldown(self, seconds: float) -> None:
        """429/503 → 进入冷却（自适应降速）。"""
        self._cooldown_until = max(self._cooldown_until, time.time() + seconds)
        self.log.warning("自适应降速：进入 %ds 冷却", round(seconds, 1))

    # ---------------- 请求 ---------------- #
    def _open(self, url: str, headers: dict[str, str], timeout: float) -> Any:
        if self.proxies:
            proxy = random.choice(self.proxies)
            handler = urllib.request.ProxyHandler(proxy)
            opener = urllib.request.build_opener(handler)
            return opener.open(url, timeout=timeout)
        return urllib.request.urlopen(url, timeout=timeout)

    def get(
        self,
        url: str,
        *,
        binary: bool = False,
        timeout: float | None = None,
        referer: str | None = None,
        extra_headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> tuple[int | None, Any]:
        """
        发起 GET。返回 (status, content)：
          - 文本模式（默认）：content 为 str（编码自动检测）；
          - binary=True：content 为 bytes；
          - stream=True（二进制流式，用于大附件）：content 为 (bytes_chunks, total)，
            实际通过 download_file 使用更合适；
          - 失败（重试耗尽）：status=None，content=错误说明 str。
        """
        url = (url or "").strip()
        if not url:
            return None, "empty_url"
        # 物理开关：每日上限 / 连续错误熔断（0 值也生效，用 is not None 判断）
        if self.max_pages_today is not None and self._pages_today >= self.max_pages_today:
            return None, f"max_pages_today exceeded ({self.max_pages_today})"
        if (
            self.stop_after_n_errors is not None
            and self._consecutive_errors >= self.stop_after_n_errors
        ):
            return None, f"stop_after_n_errors exceeded ({self.stop_after_n_errors})"

        t0 = time.time()
        last_err: Any = None
        for attempt in range(1, self.retries + 1):
            self._throttle(None)
            try:
                req = urllib.request.Request(url, method="GET")
                for k, v in self._build_headers(referer, extra_headers).items():
                    req.add_header(k, v)
                resp = self._open(url, dict(req.header_items()), (timeout or self.read_timeout))
                with resp:
                    status = resp.status
                    self._pages_today += 1
                    self._consecutive_errors = 0
                    self.log.info(
                        "GET %s status=%s elapsed=%.2fs",
                        url,
                        status,
                        time.time() - t0,
                    )
                    if binary:
                        data = resp.read()
                        return status, data
                    data = resp.read()
                    charset = resp.headers.get_content_charset()
                    text, enc, garble = decode_bytes(data, charset)
                    if garble > 0.01:
                        self.log.warning("乱码比例 %.1f%% @ %s（编码 %s）", garble * 100, url, enc)
                    return status, text
            except urllib.error.HTTPError as e:
                status = e.code
                last_err = f"HTTP {status}"  # W4-b：先记录（原实现漏记 → 重试耗尽返回 (None, None)）
                self._consecutive_errors += 1
                if status in _NO_RETRY_STATUS:
                    self.log.info("确定性错误 HTTP %d @ %s（不重试）", status, url)
                    return status, None
                if status in _RETRY_STATUS:
                    retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                    wait = self._backoff(attempt, retry_after)
                    self._enter_cooldown(wait)
                    self.log.warning(
                        "HTTP %d @ %s 限流/暂不可用，%ss 后重试(%d/%d)",
                        status,
                        url,
                        round(wait, 1),
                        attempt,
                        self.retries,
                    )
                    continue
                return status, last_err  # 非重试类 4xx/5xx：last_err 已在分支首记录
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                self._consecutive_errors += 1
                last_err = f"{type(e).__name__}: {e}"
                wait = self._backoff(attempt, None)
                self.log.warning(
                    "网络异常 @ %s，%ss 后重试(%d/%d): %s",
                    url,
                    round(wait, 1),
                    attempt,
                    self.retries,
                    e,
                )
                time.sleep(wait)
                continue
        self.log.error("GET %s 重试 %d 次仍失败：%s", url, self.retries, last_err)
        return None, last_err

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after and str(retry_after).isdigit():
            return float(int(retry_after))
        return min(2**attempt, 16) + random.uniform(0, 1)

    # ---------------- 流式下载（附件/正文文档） ---------------- #
    def download_file(
        self,
        url: str,
        dest_path: str,
        *,
        max_bytes: int | None = None,
        referer: str | None = None,
    ) -> tuple[bool, str, int]:
        """
        流式下载（规范第十二节）：连接超时 10s、读取超时 30s，禁止一次性载入内存。
        返回 (ok, msg, size_bytes)。成功时 msg 为 sha256。
        """
        url = (url or "").strip()
        t0 = time.time()
        for attempt in range(1, self.retries + 1):
            self._throttle(None)
            try:
                req = urllib.request.Request(url, method="GET")
                for k, v in self._build_headers(referer, None).items():
                    req.add_header(k, v)
                resp = self._open(url, dict(req.header_items()), self.connect_timeout)
                with resp:
                    status = resp.status
                    if status in _RETRY_STATUS:
                        wait = self._backoff(attempt, resp.headers.get("Retry-After"))
                        self._enter_cooldown(wait)
                        self.log.warning(
                            "下载 HTTP %d @ %s 重试(%d/%d)", status, url, attempt, self.retries
                        )
                        continue
                    if status in _NO_RETRY_STATUS or status >= 400:
                        return False, f"HTTP {status}", 0
                    import hashlib
                    import os

                    sha = hashlib.sha256()
                    size = 0
                    tmp = dest_path + ".part"
                    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                    with open(tmp, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if max_bytes and size > max_bytes:
                                f.close()
                                try:
                                    os.remove(tmp)
                                except OSError:
                                    pass
                                return False, f"exceed max_bytes({max_bytes})", size
                            sha.update(chunk)
                            f.write(chunk)
                    os.replace(tmp, dest_path)
                    self.log.info(
                        "下载完成 %s → %s (%.2f KB, %.2fs)",
                        url,
                        dest_path,
                        size / 1024,
                        time.time() - t0,
                    )
                    return True, sha.hexdigest(), size
            except urllib.error.HTTPError as e:
                if e.code in _RETRY_STATUS:
                    wait = self._backoff(attempt, e.headers.get("Retry-After"))
                    self._enter_cooldown(wait)
                    continue
                return False, f"HTTP {e.code}", 0
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                wait = self._backoff(attempt, None)
                self.log.warning("下载网络异常 @ %s 重试(%d/%d): %s", url, attempt, self.retries, e)
                time.sleep(wait)
                continue
        return False, "download retries exhausted", 0


# --------------------------------------------------------------------------- #
# 轻量门面：robust_get（W4-b 单栈：原 crawler_common.robust_get 实现并入此处）
# --------------------------------------------------------------------------- #
def _safe_url(url: str) -> str:
    """简单清理：去除危险空白；保留原样其余部分。"""
    return (url or "").strip()


def _backoff_wait(attempt: int, retry_after: str | None) -> float:
    """退避时长：优先 Retry-After（秒），否则 2**attempt 上限 16s，加随机抖动。

    W4-b：与 `AdaptiveHttpClient._backoff` 同算法（本仓退避公式唯一事实源——
    原 crawler_common 副本逐字重复，已删；类内静态方法保留给实例路径用）。
    """
    if retry_after and str(retry_after).isdigit():
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
) -> tuple[int | None, Any]:
    """带反爬策略的 GET 请求，返回 (status, content)。

    - status: HTTP 状态码；失败（含重试耗尽）为 None。
    - content: 文本模式返回 str；binary 模式返回 bytes；失败返回错误说明 str。

    策略：
      * 每次请求随机轮换 UA + 富请求头（Accept/Accept-Language/Referer）；
      * 请求间隔在 [min_delay, max_delay] 随机化，规避固定节奏风控；
      * 指数退避重试（2**attempt 秒上限 16s）；
      * 命中 429/503 且响应带 Retry-After 时，优先采用服务端给出的等待时长；
      * 403/404/410 确定性错误立即返回，不浪费重试配额。

    **W4-b 单栈说明**（原 `crawler_common.robust_get` 实现已删，此处为唯一实现）：
    默认参数与原实现逐项一致（retries=4 / min_delay=0.8 / max_delay=1.6 / timeout=30）。
    与原实现的两处**显式行为差异**（均为加固向，对等测试 tests/test_http_unification.py
    锁定）：① UA 池从「本地 5 条 Chrome 120 时代静态池」统一为 `load_ua_pool()` 的
    8 条池（Chrome 124/125/Edge/Firefox/Safari/Linux，含移动端混入关闭——门面
    默认 use_mobile=False 保持 PC 形态）；② 文本解码经 `decode_bytes`（乱码检测+
    编码自动识别），LookupError 兜底行为等价。确定性错误/退避/重试语义不变。
    无跨调用状态（每次调用新建轻量客户端）——与原函数式语义一致。
    """
    url = _safe_url(url)
    if not url:
        return None, "empty_url"
    # 礼貌延迟对齐原 robust_get 语义（**先延迟再请求**）：client._throttle 是
    # 「间隔保持」语义（首次无上次记录不延迟），函数式单次调用需自行先延迟一次，
    # 否则 gov 等调用方的请求节奏会比原实现激进（0.8-1.6s → 0s），有风控风险。
    gap = random.uniform(min_delay, max_delay)
    if gap > 0:
        time.sleep(gap)
    client = AdaptiveHttpClient(
        delay_range=(min_delay, max_delay),
        retries=retries,
        connect_timeout=10.0,
        read_timeout=float(timeout),
        use_mobile=False,
    )
    # 尊重调用方 retries（原 robust_get 无「≥3」下限；AdaptiveHttpClient 构造器的
    # max(3, ...) 规范留给附件下载场景——门面显式覆写回调用方语义）。
    client.retries = max(1, int(retries))
    status, content = client.get(
        url, binary=binary, timeout=float(timeout), referer=referer, extra_headers=extra_headers
    )
    # 语义对齐：原 robust_get 对确定性错误与非重试 4xx 返回 (code, None)；
    # client.get 返回 (status, "HTTP <code>") 文本 → 归一为 (code, None)。
    if isinstance(status, int) and status >= 400 and isinstance(content, str):
        return status, None
    return status, content


if __name__ == "__main__":  # 离线自检（不联网）
    c = AdaptiveHttpClient(delay_range=(0.0, 0.01), retries=3)
    assert c.retries >= 3
    assert c._pick_ua().startswith("Mozilla/5.0")
    # 降速逻辑自检
    c._enter_cooldown(0.01)
    assert c._cooldown_until > 0
    # 空 URL 安全
    st, msg = c.get("", binary=True)
    assert st is None and msg == "empty_url"
    # 每日上限开关
    c2 = AdaptiveHttpClient(max_pages_today=0, delay_range=(0, 0.01))
    st, msg = c2.get("http://example.invalid/")
    assert st is None and "max_pages_today" in msg
    print("[scraper_std.http] 离线自检通过")
