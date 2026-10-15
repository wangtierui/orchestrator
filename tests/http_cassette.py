# -*- coding: utf-8 -*-
"""tests/http_cassette.py — 采集器**录制-重放夹具**（TD-1 处置件，方案 §7.9 / §7.8-D 前置）。

为什么需要它
------------
TD-1（`docs/tech_debt.md`）：**采集器无录制-重放夹具 ⇒ 抓取/解析逻辑改动难回归**，
而它又是「>500 行采集文件按层拆分」（D-遗留）的**前置阻塞**——没有可回放的稳定输入，
就不敢动那些大文件的内部结构。

设计（对齐全仓既有纪律）
------------------------
· **回放优先、严格模式**：默认只回放夹具，缺失即抛 `CassetteMiss`（**绝不静默联网**——
  联网会让回归网��不稳定）；录制备选需显式 `mode="record"` 或跑 `tools/record_cassettes.py`。
· **URL 归一**（`_normalize_url`）：小写 scheme/host、去默认端口、去 fragment、query 参数
  **排序**并剔除易变参数（时间戳/追踪 id 之类，命名集中在一处）⇒ 同一逻辑请求无论线上
  带什么抖动参数都命中同一夹具。
· **密钥脱敏**（`_scrub_headers`）：Authorization / Cookie / token 一律落盘为 `***`
  （对齐 gate_secret_scan：夹具入库也不得携带凭据）。
· **夹具即事实源**：`tests/cassettes/*.json` + `manifest.json`（sha256 + 计数 + 来源），
  判据 **J13**（`gates/gate_config_integrity/_console.py` 同族 · `_cassette.py`）守护其
  完整性（防夹具被手改漂移 / 漏登记 / 夹带密钥）。
· **来源可辨**：每条夹具 `origin` ∈ {`recorded`（真实抓取）, `synthetic`（人工最小样本）}；
  测试与门禁都披露 origin，不把合成样本冒充真实抓取。

与既有设施的关系（不重复造）
--------------------------
`std_lib/scraper_std/cache_store.py` 的 offline 缓存是**生产**提速/续跑设施（键为响应文件
路径）；本模块是**测试**夹具（键为归一请求、可脱敏、可断言缺失、可 manifest 审计），
两者并存不互相替代——生产缓存按 URL 末段或 sha1 命名、面向续跑；测试夹具按归一请求配对、
面向回归。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import urllib.parse
from typing import Any

CASSETTES_DIRNAME = "cassettes"
SCHEMA_VERSION = 1

#: 夹具根目录（`None` ⇒ 默认本文件同级的 `cassettes/`）。**参数化点**：门禁 J13 与
#: 负例测试据此注入临时目录；运行期保持默认，避免"测试有、门禁没有"的口径分叉。
CASSETTES_ROOT: str | None = None

#: 易变 query 参数（归一时剔除；线上带抖动、逻辑等价）。命名集中于此，便于按源补充。
VOLATILE_QUERY_KEYS = frozenset({
    "_", "t", "ts", "_t", "r", "rnd", "random", "timestamp", "_ts",
    "traceid", "trace_id", "requestid", "request_id", "rid", "v",
})

_SECRET_HEADERS = ("authorization", "cookie", "set-cookie", "x-api-key",
                   "x-auth-token", "proxy-authorization")


class CassetteMiss(LookupError):
    """回放未命中（**只在 record 模式外抛出**；绝不静默放行成联网）。"""


class CassetteError(RuntimeError):
    """夹具本身损坏（schema/编码/摘要不符）。"""


# --------------------------------------------------------------------------- #
# 归一与脱敏（纯函数，好测）
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    """逻辑请求的归一 URL：小写 host、去默认端口、去 fragment、剔易变参数、query 排序。"""
    u = urllib.parse.urlsplit(url.strip())
    scheme = (u.scheme or "http").lower()
    host = (u.hostname or "").lower()
    port = u.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if not port else f"{host}:{port}"
    pairs = [
        (k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        if k.lower() not in VOLATILE_QUERY_KEYS
    ]
    query = urllib.parse.urlencode(sorted(pairs))
    path = u.path or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def request_key(method: str, url: str, body: bytes | str | None = None) -> str:
    """请求 → 稳定键（sha1(method|归一URL[|请求体摘要]) 前 16 位）。

    ⚠️ **请求体只在非空时参与**：GET/HEAD 恒无请求体，若把「响应体」或空串计入，
    同一逻辑请求会因调用点传参差异算出不同键（实测 gov_list_page 归一变体不命中）。
    POST/PUT 带体请求仍以体摘要区分（不同 body ⇒ 不同键，正确）。
    """
    body_s = ""
    if body:
        body_s = (body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body))
        body_s = hashlib.sha1(body_s.encode("utf-8")).hexdigest()[:16]
    raw = f"{method.upper()}|{normalize_url(url)}"
    if body_s:
        raw += f"|{body_s}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _scrub_headers(headers: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        out[k] = "***" if str(k).lower() in _SECRET_HEADERS else v
    return out


def _normalize_header_keys(headers: Any) -> dict[str, str]:
    """HTTP 头 → 小写键 str 值（`email.message.Message` / dict / 迭代皆可）。"""
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        items = headers.items()
    else:
        items = headers  # type: ignore[assignment]
    out: dict[str, str] = {}
    for k, v in items:
        try:
            out[str(k).lower()] = str(v)
        except Exception:  # noqa: BLE001 头异常不致命（旁路）
            continue
    return out


# --------------------------------------------------------------------------- #
# 夹具文件
# --------------------------------------------------------------------------- #
def cassette_path(name: str) -> str:
    """夹具文件绝对路径（`name` 不含扩展名；落在 `tests/cassettes/`）。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise CassetteError(f"非法夹具名 {name!r}（只允许字母/数字/下划线/连字符）")
    root = CASSETTES_ROOT or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), CASSETTES_DIRNAME)
    return os.path.join(root, f"{name}.json")


def load_cassette(name: str) -> dict:
    """读夹具并校验 schema 与逐条摘要（损坏即 `CassetteError`，绝不静默重录）。"""
    p = cassette_path(name)
    if not os.path.exists(p):
        raise CassetteMiss(f"夹具不存在：{p}")
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CassetteError(f"夹具不可解析 {p}：{type(e).__name__}: {e}") from e
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise CassetteError(
            f"夹具 schema 不支持 {p}：{doc.get('schema_version')} != {SCHEMA_VERSION}")
    if doc.get("origin") not in ("recorded", "synthetic"):
        raise CassetteError(f"夹具 origin 必填且 ∈ recorded|synthetic：{p}")
    for it in doc.get("interactions") or []:
        body = it.get("body") or ""
        raw = (base64.b64decode(body) if it.get("body_encoding") == "base64"
               else body.encode("utf-8"))
        want = it.get("body_sha256") or hashlib.sha256(raw).hexdigest()
        if hashlib.sha256(raw).hexdigest() != want:
            raise CassetteError(f"夹具正文摘要不符：{p}::{it.get('key')}")
    return doc


def make_cassette(*, source: str, interactions: list[dict],
                  origin: str = "synthetic", note: str = "",
                  recorded_at: str = "") -> dict:
    """构造（并补齐）夹具文档——录制与手工合成共用同一构造口（字段口径唯一）。

    `interactions` 每条：`{method,url,status,headers,body,body_encoding?}`；
    本函数补 `key`（归一键）、`normalized_url` 与 `body_sha256`。
    """
    norm: list[dict] = []
    for it in interactions:
        body = it.get("body") or ""
        raw = (base64.b64decode(body) if it.get("body_encoding") == "base64"
               else body.encode("utf-8"))
        # 键只认**请求体**（`request_body`，默认 None ⇒ GET 形态）；`body` 是响应体。
        norm.append({
            "method": it.get("method", "GET").upper(),
            "url": it["url"],
            "normalized_url": normalize_url(it["url"]),
            "key": request_key(it.get("method", "GET"), it["url"], it.get("request_body")),
            "status": int(it.get("status", 200)),
            "reason": it.get("reason", "OK"),
            "headers": _scrub_headers(it.get("headers")),
            "body_encoding": it.get("body_encoding", "utf-8"),
            "body": body,
            "body_sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {"schema_version": SCHEMA_VERSION, "source": source, "origin": origin,
            "recorded_at": recorded_at, "note": note, "interactions": norm}


def save_cassette(name: str, doc: dict) -> str:
    """落盘夹具（UTF-8 / LF / 缩进 2，稳定 diff）。"""
    if not doc.get("interactions"):
        raise CassetteError(f"拒绝落盘空夹具：{name}")
    p = cassette_path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return p


def cassette_file_sha256(name: str) -> str:
    """夹具文件级 sha256（manifest 审计用）。"""
    p = cassette_path(name)
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------- #
# 重放器
# --------------------------------------------------------------------------- #
class _ReplayResponse(io.BytesIO):
    """最小 HTTP 响应替身：可 `with`、可 `.read()`、带 `status` / `headers` / `url`。

    采集器侧典型用法：`with opener.open(req) as resp: resp.read()` /
    `resp.status` / `resp.headers.get_content_charset()` / `resp.geturl()`。
    """

    #: requests 兼容属性（由 `_adapt_requests` 填充；此处显式声明以满足类型检查）
    status_code: int
    headers: Any
    content: bytes
    encoding: str

    #: requests 兼容属性（由 `_adapt_requests` 填充；此处显式声明以满足类型检查）
    status_code: int
    headers: Any
    content: bytes
    encoding: str

    def __init__(self, body: bytes, status: int, headers: dict[str, str], url: str) -> None:
        super().__init__(body)
        self.status = status
        self.code = status
        self.reason = "OK"
        self._headers = headers
        self._url = url

    def __enter__(self) -> _ReplayResponse:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def geturl(self) -> str:
        return self._url

    def info(self) -> _ReplayHeaderView:
        return _ReplayHeaderView(self._headers)

    def getheader(self, name: str, default: Any = None) -> Any:
        return self._headers.get(str(name).lower(), default)


class _ReplayHeaderView:
    """`email.message.Message` 鸭子类型替身（`resp.headers.get_content_charset()` 等）。"""

    def __init__(self, headers: dict[str, str]) -> None:
        self._h = dict(headers)

    def get(self, name: str, default: Any = None) -> Any:
        return self._h.get(str(name).lower(), default)

    def get_content_charset(self) -> str | None:
        ctype = self._h.get("content-type", "")
        m = re.search(r"charset=([\w\-]+)", ctype, re.I)
        return m.group(1) if m else None

    def items(self):
        return list(self._h.items())

    def __getitem__(self, k: str) -> str:
        return self._h[k]

    def __contains__(self, k: str) -> bool:
        return k in self._h


class CassettePlayer:
    """按夹具重放：命中返回 `_ReplayResponse`；未命中抛 `CassetteMiss`（严格，不联网）。"""

    def __init__(self, name: str, *, strict: bool = True) -> None:
        self.name = name
        self.doc = load_cassette(name)
        self.strict = strict
        self._by_key = {it["key"]: it for it in self.doc.get("interactions") or []}
        self.hits: list[str] = []
        self.misses: list[str] = []

    @property
    def origin(self) -> str:
        return str(self.doc.get("origin") or "")

    def available_keys(self) -> list[str]:
        return sorted(self._by_key)

    def get(self, method: str, url: str, body: bytes | str | None = None,
            headers: dict[str, str] | None = None) -> _ReplayResponse:
        k = request_key(method, url, body)
        it = self._by_key.get(k)
        if it is None:
            self.misses.append(k)
            hint = f"可用键={self.available_keys()}"
            raise CassetteMiss(f"[{self.name}] 未命中：{method} {normalize_url(url)}（key={k}）；{hint}")
        self.hits.append(k)
        raw = (base64.b64decode(it["body"]) if it.get("body_encoding") == "base64"
               else (it.get("body") or "").encode("utf-8"))
        return _ReplayResponse(raw, int(it.get("status", 200)),
                               _normalize_header_keys(it.get("headers")), it.get("url", url))

    def assert_all_used(self) -> None:
        """夹具内每条交互都被用到（防夹具腐化：留死条目）——由测试显式调用。"""
        unused = sorted(set(self._by_key) - set(self.hits))
        if unused:
            raise CassetteError(f"[{self.name}] 夹具存在未被使用的交互（腐化）：{unused}")


@contextlib.contextmanager
def replay(monkeypatch: Any, name: str, *, strict: bool = True):
    """把 urllib / requests / 单栈 `robust_get` 全部指向夹具（`monkeypatch` 自动复原）。

    覆盖四类网络入口（与 TD-1 调研一致——采集器并非全部经单栈）：
      ① `urllib.request.urlopen`（pbc / mof / nfra_attachments / supp 等）；
      ② `urllib.request.OpenerDirector.open`（nfra 用 `build_opener()` 形态）；
      ③ `requests.Session.get/request/send`（gov 的会话级 RobustSession，ADR-0009 豁免）；
      ④ `std_lib.scraper_std.http.robust_get` / `AdaptiveHttpClient.get`（单栈门面）。
    """
    player = CassettePlayer(name, strict=strict)
    import urllib.request as _ur

    def _fake_urlopen(req: Any, *a: Any, **kw: Any) -> _ReplayResponse:
        url, method, body = _split_req(req)
        return player.get(method, url, body)

    def _fake_opener_open(self: Any, req: Any, *a: Any, **kw: Any) -> _ReplayResponse:
        url, method, body = _split_req(req)
        return player.get(method, url, body)

    monkeypatch.setattr(_ur, "urlopen", _fake_urlopen)
    # ⚠️ `OpenerDirector.open` 是**实例属性**（`__init__` 内 `self.open = self._open`）⇒
    #    补类属性无效。改补 `_open`（构造时解析），并要求调用方在**补丁之后**构造
    #    opener（`build_opener()` 之前的实例仍走真实网络——故此点写进 docstring 契约）。
    with contextlib.suppress(AttributeError):
        monkeypatch.setattr(_ur.OpenerDirector, "_open", _fake_opener_open)
        monkeypatch.setattr(_ur.OpenerDirector, "open", _fake_opener_open,
                            raising=False)

    # requests（若已安装；未装则跳过——本仓 requests 是 gov 采集器的软依赖）
    try:
        import requests as _rq
    except ImportError:  # pragma: no cover
        _rq = None  # type: ignore[assignment]
    if _rq is not None:
        def _fake_send(self: Any, req: Any, **kw: Any) -> Any:
            url, method, _body = _split_req(req)
            resp = player.get(method, url, None)
            _adapt_requests(resp, req)
            return resp
        monkeypatch.setattr(_rq.Session, "send", _fake_send, raising=False)

    # 单栈门面（若已导入）
    with contextlib.suppress(ImportError):
        from std_lib.scraper_std import http as _http

        def _fake_robust_get(url: str, *a: Any, **kw: Any) -> Any:
            binary = bool(kw.get("binary"))
            params = kw.get("params")
            full = url + (("?" + urllib.parse.urlencode(params)) if params else "")
            resp = player.get("GET", full, None)
            return resp.read() if binary else resp.read().decode(
                _resp_charset(resp), "replace")
        monkeypatch.setattr(_http, "robust_get", _fake_robust_get, raising=False)

    yield player


def _resp_charset(resp: _ReplayResponse) -> str:
    try:
        return resp.headers.get_content_charset() or "utf-8"
    except Exception:  # noqa: BLE001
        return "utf-8"


def _adapt_requests(resp: _ReplayResponse, req: Any) -> None:
    """让 `_ReplayResponse` 兼带 requests 的最小属性（`status_code`/`headers`/`content`）。"""
    resp.status_code = resp.status  # type: ignore[attr-defined]
    resp.headers = resp.info()  # type: ignore[attr-defined]
    resp.content = resp.getvalue()  # type: ignore[attr-defined]
    resp.encoding = _resp_charset(resp)  # type: ignore[attr-defined]


def _split_req(req: Any) -> tuple[str, str, bytes | None]:
    """把 urllib Request / requests PreparedRequest / str 统一成 `(url, method, body)`。"""
    if isinstance(req, str):
        return req, "GET", None
    url = getattr(req, "full_url", None) or getattr(req, "url", None) or str(req)
    method = str(getattr(req, "method", None) or getattr(req, "method", None) or "GET").upper()
    body = getattr(req, "data", None)
    if body is None:
        body = getattr(req, "body", None)
    if isinstance(body, str):
        body = body.encode("utf-8")
    return str(url), method, body  # type: ignore[return-value]
