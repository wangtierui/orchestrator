# -*- coding: utf-8 -*-
"""gov/gov_session — **缓存 + 会话层**（`RobustSession` 与它绑定的缓存状态）。

为什么这三件必须同处（2026-10-20 第一百零五批，AST 实测）
--------------------------------------------------------
`RobustSession` 运行时读模块级 `_RESP_TEXT`（`TextResponseCache` 实例），而
`_init_cache` 用 `global _RESP_TEXT` **写入**它、`set_cache_dir`/`set_offline`
读写它。四者是一个**不可分割的状态单元**：

  · 若把 `RobustSession` 单独搬走而 `_RESP_TEXT` 留在 `collector` ⇒ 会话读的是
    `gov_session._RESP_TEXT`、初始化改的是 `collector._RESP_TEXT`
    ⇒ **两处状态、缓存开关静默失效**（不报错，只是永远不命中）。
  · 本仓反复踩过这一类"可变全局跨模块"坑，故以此文件为界，**不再二次拆分**。

`collector.py` 侧仍 `re-export` 这些名字（外部导入点不变），但请注意：
`from gov_session import _RESP_TEXT` 取到的只是**导入那一刻的引用快照**——
真正的状态在 `gov_session` 模块里。`collector.main()` 只调用 `_init_cache(...)`
（不直接读 `_RESP_TEXT`，AST 已确认），故无陈旧读取风险。

**双形态**：包形态 / **脚本形态**（`python collectors/gov/collector.py`），故保留引导。
"""
from __future__ import annotations

# ---- 仓库引导（与 collector.py / gov_constants.py 同款；`if not in sys.path` 幂等）----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys

# ---- 标准库 / 第三方（原样复制自 collector.py L44-59，避免漏 import）----
# W4（§7.10 问题 5）：logs 落点改引 paths.LOGS_DIR（原为 CWD 相对 "logs" 自拼，
# 链上 cwd=仓根时写仓根 logs/、standalone 时写 CWD/logs——两种语义隐式分叉）。
# import paths 置于**仓根引导之后**（引导前 import 必 ModuleNotFoundError——
# modules standalone 脚本的同款教训）；isort 将其归入 first-party 段（stdlib 之后）。
import json
import os
import random
import time
import urllib.parse
from typing import Any

# ---- 常量与依赖别名：来自 `gov_constants`（单一出处）----
try:
    from .gov_constants import (
        DEFAULT_HEADERS,
        LOG,
        USER_AGENTS,
        HTTPAdapter,
        OfflineMiss,
        Retry,
        ScrapeConfig,
        _cache_ep,
        bind_source_cache,
        decode_html,
        requests,
    )
except ImportError:  # pragma: no cover  脚本形态
    from gov_constants import (  # type: ignore[no-redef]
        DEFAULT_HEADERS,
        LOG,
        USER_AGENTS,
        HTTPAdapter,
        OfflineMiss,
        Retry,
        ScrapeConfig,
        _cache_ep,
        bind_source_cache,
        decode_html,
        requests,
    )


# ---- S1 可变缓存状态（原 collector.py L148-149）----
_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


# ---- S2 缓存控制（原 collector.py L154-169）----
def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("gov")，单物理根）；path 显式可覆盖。"""
    global _RESP_TEXT
    _RESP_TEXT = bind_source_cache("gov", "text", root=path)
    if offline and _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(flag)


# ---- S3 会话（原 collector.py L177-275）----
class RobustSession:
    """带重试、退避、UA 轮换与随机延时的请求会话。"""

    def __init__(self, cfg: ScrapeConfig):
        self.cfg = cfg
        self.session = requests.Session()
        retry = Retry(
            total=cfg.retries,
            connect=cfg.retries,
            read=cfg.retries,
            status=cfg.retries,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            backoff_factor=1.5,          # 退避：{backoff_factor} * (2 **(n-1))
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        h = dict(DEFAULT_HEADERS)
        h["User-Agent"] = random.choice(USER_AGENTS)
        if referer:
            h["Referer"] = referer
        return h

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.cfg.delay_min, self.cfg.delay_max))

    def get(self, url: str, referer: str | None = None,
            params: dict[str, Any] | None = None,
            is_json: bool = False) -> Any | None:
        params = params or {}
        # 1) 缓存命中：续跑 / 离线
        if _RESP_TEXT is not None:
            cp = _RESP_TEXT.path(_cache_ep(url), params)
            if os.path.exists(cp):
                try:
                    with open(cp, encoding="utf-8") as fh:
                        cached = fh.read()
                    return json.loads(cached) if is_json else cached
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass  # 缓存损坏则重新请求
            if _RESP_TEXT.offline:
                raise _OfflineMiss("%s ? %s" % (url, urllib.parse.urlencode(params)))
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.get(
                    url, params=params, headers=self._headers(referer),
                    timeout=self.cfg.timeout,
                )
                if resp.status_code == 200:
                    # 文本响应使用鲁棒解码（UTF-8→GBK/GB18030），避免中文乱码；
                    # JSON 响应交由 requests 自行解码。
                    result = resp.json() if is_json else decode_html(resp.content)
                    # 写入缓存（仅成功响应且非空，不缓存错误/拦截页）
                    if _RESP_TEXT is not None and result:
                        try:
                            _RESP_TEXT.put(
                                _cache_ep(url), params,
                                json.dumps(result, ensure_ascii=False) if is_json else result,
                            )
                        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                            pass
                    return result
                LOG.warning("GET %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:  # 网络异常 / 超时  # noqa: BLE001
                LOG.warning("GET %s 异常：%s (尝试 %d)", url, e, attempt)
            # 指数退避
            time.sleep(min(2 ** attempt, 30))
        LOG.error("GET %s 多次重试失败，放弃", url)
        return None

    def get_text(self, url: str, referer: str | None = None,
                 params: dict[str, Any] | None = None) -> str | None:
        return self.get(url, referer=referer, params=params, is_json=False)

    def post_json(self, url: str, json_body: dict[str, Any],
                  referer: str | None = None) -> Any | None:
        """POST JSON 并解析响应（带重试/退避/UA 轮换）。失败返回 None。"""
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.post(
                    url, json=json_body,
                    headers=self._headers(referer), timeout=self.cfg.timeout)
                if resp.status_code == 200:
                    return resp.json()
                LOG.warning("POST %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:  # noqa: BLE001
                LOG.warning("POST %s 异常：%s (尝试 %d)", url, e, attempt)
            time.sleep(min(2 ** attempt, 30))
        LOG.error("POST %s 多次重试失败，放弃", url)
        return None

__all__ = [
    "RobustSession", "_OfflineMiss", "_RESP_TEXT", "_init_cache",
    "set_cache_dir", "set_offline",
]
