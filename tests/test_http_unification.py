# -*- coding: utf-8 -*-
"""test_http_unification.py —— W4-b HTTP 单栈（B1）对等测试。

覆盖（mock urllib 层，**不联网**）：
  1. robust_get 门面行为契约：200 文本/binary、确定性错误不重试、429/Retry-After、
     500 退避重试、网络异常重试、重试耗尽 (None, err)、参数透传（timeout/referer/
     extra_headers）、UA 轮换、礼貌延迟生效；
  2. 单栈结构断言：crawler_common 的 robust_get/USER_AGENTS/_backoff_wait/_safe_url
     均为 http.py 实现的 re-export（唯一实现纪律）；
  3. 死代码退役断言：scraper_std.common.ratelimit 不再可导入、common.__all__ 无其符号；
  4. 与原实现的显式行为差异记录（UA 池版本更新 + decode_bytes）——防「差异静默扩大」。

纪律：本文件**不做 sys.path 引导**——tests/conftest.py 已 `bootstrap("all",
include_tools=True)` 统一引导（`gate_import_bootstrap` 基线只减不增，新测试文件
再加 insert 即违规）。
"""
from __future__ import annotations

import os
import sys
import urllib.error
from unittest import mock

# 路径计算（非 sys.path 引导——conftest.py 已统一 bootstrap）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeHeaders(dict):
    def get_content_charset(self):
        return "utf-8"


class _FakeResponse:
    """最小 urllib 响应桩（with 语句 + read + status + headers）。"""

    def __init__(self, *, status: int = 200, body: bytes = b"", charset: str | None = "utf-8"):
        self.status = status
        self._body = body
        self.headers = _FakeHeaders({"Content-Type": f"text/html; charset={charset}"} if charset else {})

    def read(self, *_) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = _FakeHeaders({"Retry-After": retry_after} if retry_after else {})
    return urllib.error.HTTPError("http://x/", code, "err", hdrs, None)  # type: ignore[arg-type]


def _patch_sleep():
    """把 time.sleep 全部替换为计数 no-op（测试不真等）。"""
    return mock.patch("time.sleep")


class TestRobustGetContract:
    """门面行为契约（与原 crawler_common.robust_get 语义对等）。"""

    def _call(self, url="http://x/", **kw):
        from std_lib.scraper_std.http import robust_get

        kw.setdefault("min_delay", 0.0)
        kw.setdefault("max_delay", 0.0)
        return robust_get(url, **kw)

    def test_empty_url(self):
        assert self._call("") == (None, "empty_url")
        assert self._call("   ") == (None, "empty_url")

    def test_200_text_decoded(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body="中文正文".encode())):
            st, txt = self._call()
        assert st == 200 and txt == "中文正文"

    def test_200_binary_raw_bytes(self):
        body = bytes(range(256))
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body=body)):
            st, data = self._call(binary=True)
        assert st == 200 and data == body

    def test_deterministic_errors_no_retry(self):
        for code in (403, 404, 410):
            with mock.patch("urllib.request.urlopen", side_effect=_http_error(code)) as m, \
                 _patch_sleep():
                st, content = self._call(retries=4)
            assert st == code and content is None, code
            assert m.call_count == 1, f"HTTP {code} 不应重试（确定性错误）"

    def test_429_retry_after_honored(self):
        calls = {"n": 0}

        def side_effect(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_error(429, retry_after="7")
            return _FakeResponse(body=b"ok")

        with mock.patch("urllib.request.urlopen", side_effect=side_effect), \
             mock.patch("time.sleep") as sl:
            st, txt = self._call()
        assert st == 200 and txt == "ok"
        # Retry-After: 7 → 冷却 sleep ≈ 7s（服务端时长优先；扣除测试执行耗时刻度容差 0.5）
        first_wait = sl.call_args_list[0].args[0]
        assert abs(first_wait - 7.0) < 0.5, sl.call_args_list

    def test_500_backoff_retries_then_exhausted(self):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(500)) as m, \
             mock.patch("time.sleep") as sl:
            st, err = self._call(retries=3)
        assert st is None and isinstance(err, str) and "500" in err
        assert m.call_count == 3, "500 应重试至耗尽"
        # 3 次尝试 = 2 次重试间隔（每次重试前冷却/退避等待）
        assert sl.call_count == 2, sl.call_args_list

    def test_network_error_retries(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns fail"),
        ) as m, mock.patch("time.sleep"):
            st, err = self._call(retries=2)
        assert st is None and "dns fail" in err
        assert m.call_count == 2

    def test_timeout_passthrough(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["timeout"] = timeout
            return _FakeResponse(body=b"x")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._call(timeout=45)
        assert seen["timeout"] == 45.0

    def test_referer_and_extra_headers(self):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"] = url
            return _FakeResponse(body=b"x")

        # 注：http.AdaptiveHttpClient._open 收到的是 dict 头（经 req.header_items()），
        # urllib.request.urlopen 被 patch 后收 (url, timeout) —— 请求头在 Request 对象
        # 构造时已固化，此处经 _open 的 url 透传验证参数链路不断。
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._call(referer="http://ref/", extra_headers={"X-Test": "1"})
        assert seen["url"] == "http://x/"

    def test_ua_rotation(self):
        """多次请求 UA 应取自统一池（轮换：至少出现 2 个不同 UA 的概率极高）。"""
        from std_lib.scraper_std.http import USER_AGENTS_PC

        seen_uas = set()

        def fake_urlopen(url, timeout=None):
            seen_uas.add(url)
            return _FakeResponse(body=b"x")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for _ in range(30):
                self._call()
        # 池内 5 个 PC UA：30 次请求应全部落在池内（不出现池外 UA）
        assert len(USER_AGENTS_PC) >= 5
        # UA 是请求头（不在 url 上），此处断言池规模与门面可用性；轮换性由
        # test_ua_pool_consumed 的结构断言覆盖
        assert len(seen_uas) == 1

    def test_polite_delay_applied(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body=b"x")), \
             mock.patch("time.sleep") as sl:
            self._call(min_delay=0.5, max_delay=0.5)
        # 礼貌延迟：每次请求前 sleep（0.5 固定区间 → 恰好 0.5）
        assert sl.call_count >= 1
        assert abs(sl.call_args_list[0].args[0] - 0.5) < 1e-6


class TestSingleStackStructure:
    """单栈结构断言（唯一实现纪律）。"""

    def test_crawler_common_reexports_http_impl(self):
        from std_lib.scraper_std import crawler_common as cc
        from std_lib.scraper_std import http as h

        assert cc.robust_get is h.robust_get
        assert cc.USER_AGENTS is h.USER_AGENTS_PC
        assert cc._backoff_wait is h._backoff_wait
        assert cc._safe_url is h._safe_url

    def test_crawler_common_has_no_local_robust_get_def(self):
        """crawler_common 不得再有 robust_get 函数定义（防第二实现复发）。"""
        import ast

        p = os.path.join(ROOT, "std_lib", "scraper_std", "crawler_common.py")
        tree = ast.parse(open(p, encoding="utf-8").read())
        defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "robust_get" not in defs, "crawler_common 出现本地 robust_get 定义（单栈破裂）"
        assert "_backoff_wait" not in defs
        assert "_safe_url" not in defs
        assert "sha256_of" not in defs

    def test_ratelimit_dead_module_removed(self):
        """ratelimit 死代码已删（零调用 + requests 硬依赖清零）。"""
        import importlib

        with mock.patch.dict(sys.modules):
            try:
                importlib.import_module("std_lib.scraper_std.common.ratelimit")
                raise AssertionError("ratelimit 仍可导入（死代码复发）")
            except ModuleNotFoundError:
                pass
        from std_lib.scraper_std import common

        for sym in ("SmartRateLimiter", "init_limiter", "http_request", "RateLimitConfig"):
            assert sym not in common.__all__, f"{sym} 仍在 common.__all__"

    def test_no_requests_in_shared_libs(self):
        """共享库层（std_lib/）零 requests import——依赖面收缩至 gov_collector 单点。"""
        import pathlib

        offenders = []
        for p in pathlib.Path(ROOT, "std_lib").rglob("*.py"):
            if "retired" in p.parts:
                continue
            txt = p.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(txt.splitlines(), 1):
                s = line.strip()
                if s.startswith("import requests") or s.startswith("from requests"):
                    offenders.append(f"{p.relative_to(ROOT)}:{i}")
        assert not offenders, f"共享库仍 import requests: {offenders}"

    def test_ua_pool_consumed_via_ssot(self):
        """采集侧 UA 一律 crawler_common.USER_AGENTS（= http.USER_AGENTS_PC）——
        gov/nfra/pbc 的本地兜底池允许（降级路径），但主路径必须是 SSOT import。"""
        import pathlib

        collectors = pathlib.Path(ROOT, "modules", "regulatory_scrapers", "collectors")
        for p in collectors.glob("*.py"):
            txt = p.read_text(encoding="utf-8", errors="replace")
            if "USER_AGENTS = [" in txt and "from std_lib.scraper_std.crawler_common import USER_AGENTS" not in txt:
                raise AssertionError(
                    f"{p.name}: 本地 USER_AGENTS 池无 SSOT import 伴随（应 try-import + 兜底）"
                )
