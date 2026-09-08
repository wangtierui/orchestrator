# -*- coding: utf-8 -*-
"""
std_lib 通用缓存模块（五源共用抽象层）
====================================

本模块将 nfra 既有「HTTP 响应缓存」核心逻辑抽象、封装为与数据源无关的通用组件，
供 regulatory_scrapers 下 **五源（gov / mof / nfra / pbc / supp）** 统一复用。
所有调用方的缓存产物统一落盘于 ``regulatory_scrapers/cache/<source>/``，
实现「同一抽象层、行为一致、配置统一、低耦合、易扩展」。

------------------------------------------------------------------
一、职责边界（明确不做什么）
------------------------------------------------------------------
本模块 **只负责缓存的存储与寻址**，不涉以下职责（避免与抓取/解析/清洗层耦合）：

  * 不发起任何网络请求（网络由调用方 fetcher 负责）；
  * 不做 HTML/JSON 解析、字段抽取、正文清洗（属 crawler_common / cleaner）；
  * 不决定抓取策略（列表窗口、WAF 冷却、并发——属各源 scraper / weekly_refresh）；
  * 不负责最终数据落盘（raw json/csv 属各源 scraper 的 --out-dir）；
  * 二进制附件的「下载 + OCR/文本抽取」由 fetch_attachments / std_lib attachments 负责，
    本模块 BlobCache 仅提供「按 owner 存二进制 + manifest 追踪 + content-hash 去重」的
    缓存抽象，供其复用。

------------------------------------------------------------------
二、对外接口契约
------------------------------------------------------------------
顶层工厂：
  get_source_cache(source: str, root: str | None = None) -> SourceCache
      source：数据源标识，取值 "gov" | "mof" | "nfra" | "pbc" | "supp"（任意小写串亦可）。
      root  ：缓存根目录；缺省解析为 ``regulatory_scrapers/cache``
              （依据本文件在仓库中的固定层级 ``<root>/std_lib/scraper_std/`` 自动推导）。
      返回 SourceCache，内含：
        .responses -> ResponseCache     响应缓存，落盘于 ``<root>/<source>/``
        .blobs     -> BlobCache         二进制附件缓存，落盘于 ``<root>/<source>/attachments/``

ResponseCache（JSON 响应缓存）：
  __init__(self, root: str, *, offline: bool = False, ttl: int | None = None)
      root  ：缓存目录（绝对或相对均可，内部 makedirs）。
      offline：离线模式开关；True 时 get() 命中返回、未命中抛 OfflineMiss。
      ttl   ：可选存活秒数；非 None 时，超过 ttl 的文件视为未命中（返回 None）。
  path(endpoint, params) -> str
      确定性文件名：``<ep>__<sorted_params_urlencoded_sanitized[:160]>.json``。
      endpoint 可为完整 URL（取最后一段路径作 ep）或裸端点名。
      与 nfra 原 ``_cache_path`` 算法逐字一致，保证历史缓存可直接复用。
  exists(endpoint, params) -> bool
  get(endpoint, params) -> dict | None
      命中且可解析返回 dict；缺失/损坏/超 ttl 返回 None（offline 下缺失抛 OfflineMiss）。
  put(endpoint, params, data, *, overwrite: bool = True) -> str
      原子写盘（tempfile + os.replace），返回路径；overwrite=False 且已存在则跳过。
  set_offline(flag: bool) -> None
  fetch(endpoint, params, fetcher, *, offline: bool | None = None) -> dict
      高层便捷：命中即返回；否则调用 fetcher(params) -> dict，put 后返回；
      offline 优先用形参，否则用实例 offline。

BlobCache（二进制附件缓存）：
  __init__(self, root: str)
  manifest(owner_id) -> dict           读 owner 的 manifest.json（缺省返回空壳）。
  save(owner_id, data: bytes, name_hint: str, **meta) -> dict
      按 content-sha256 命名文件（保留原扩展名）落盘，并更新 manifest；
      返回该附件的 manifest 条目（含 attachment_name / sha256 / source_saved / **meta）。
  load(owner_id, filename) -> bytes    读取某附件原始字节。
  path(owner_id, filename) -> str      某附件绝对路径。

异常：
  OfflineMiss(Exception)：离线模式缓存未命中时由 ResponseCache.get 抛出，
      调用方据以决定「跳过」而非「联网」。

------------------------------------------------------------------
三、五源标准化调用路径
------------------------------------------------------------------
各源统一通过工厂接入，目录布局一致：

  from std_lib.scraper_std.cache_store import get_source_cache
  cache = get_source_cache("nfra")              # 默认 root=regulatory_scrapers/cache
  # cache.responses 目录 = regulatory_scrapers/cache/nfra/
  # cache.blobs     目录 = regulatory_scrapers/cache/nfra/attachments/

  # 写（抓取成功时）
  cache.responses.put("https://x/SelectByDocId", {"docId": 1199538}, payload)
  # 读（断点续跑 / 离线复现）
  hit = cache.responses.get("https://x/SelectByDocId", {"docId": 1199538})
  # 离线判定
  if hit is None and offline: ...   # 或启用实例 offline 由 get 抛 OfflineMiss

五源各自命名空间（互不干扰，统一根 cache/ 下）：

  gov  -> regulatory_scrapers/cache/gov/
  mof  -> regulatory_scrapers/cache/mof/
  nfra -> regulatory_scrapers/cache/nfra/        （由 nfra_regulations_scraper/cache 迁移而来）
  pbc  -> regulatory_scrapers/cache/pbc/
  supp -> regulatory_scrapers/cache/supp/

参数规范：
  * source 一律小写，取值固定为五源标识之一；
  * endpoint 与 params 必须可复现（params 经 sorted 后编码），保证相同请求命中同一文件；
  * 写入仅在「获取到成功响应」后进行（与 nfra 既有契约一致，不缓存错误/拦截页）；
  * 迁移/合并：因命名算法确定性，不同根目录间整体 rename 即保命中，无需重抓。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

__all__ = [
    "OfflineMiss",
    "ResponseCache",
    "TextResponseCache",
    "BlobCache",
    "SourceCache",
    "get_source_cache",
    "default_cache_root",
]

_VALID = re.compile(r"[^A-Za-z0-9_.-]")


class OfflineMiss(Exception):
    """离线模式下缓存未命中时抛出，由调用方决定跳过而非联网。"""


def _atomic_write_json(path: str, obj: Any) -> None:
    """跨平台原子写 JSON：先写临时文件再 os.replace，避免半截文件。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def default_cache_root() -> str:
    """解析缓存根目录：本文件位于 ``<repo>/std_lib/scraper_std/cache_store.py``，
    上溯两级即 ``<repo>``，拼接 ``cache``。返回绝对路径。"""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(repo_root, "cache")


# --------------------------------------------------------------------------- #
# 五源统一缓存工厂（2026-09-08）：单一物理缓存根 + 源级单例绑定
#
# 缓存根唯一约定 = ``<repo>/modules/regulatory_scrapers/cache/<source>/``
# （各 collector 不再自行 dirname 推导 cache 路径，消除 nfra 双根等分叉）。
# 若 modules/regulatory_scrapers 不存在（共享库独立使用场景）回退 ``<repo>/cache``。
# 支持 env ``SCRAPER_CACHE_ROOT`` / configure_cache_root() 整体覆盖。
# --------------------------------------------------------------------------- #
_CACHE_OVERRIDE = ""
_BOUND: dict[tuple[str, str, str], object] = {}
_KIND_CLASSES = {}


def configure_cache_root(root: str) -> None:
    """设置统一缓存基根（空串恢复默认解析）；影响其后 source_cache_root/绑定。"""
    global _CACHE_OVERRIDE
    _CACHE_OVERRIDE = (root or "").strip()


def scraper_cache_base() -> str:
    """统一缓存基根：优先 ``<repo>/modules/regulatory_scrapers/cache``（本仓布局）。"""
    here = os.path.dirname(os.path.abspath(__file__))       # std_lib/scraper_std/
    repo_root = os.path.dirname(os.path.dirname(here))
    modules_cand = os.path.join(repo_root, "modules", "regulatory_scrapers", "cache")
    if os.path.isdir(os.path.dirname(modules_cand)):        # modules/regulatory_scrapers 存在
        return modules_cand
    return os.path.join(repo_root, "cache")


def source_cache_root(source: str, *, root: str | None = None) -> str:
    """某源的缓存根 = 基根/<source>（source 小写化）。root 显式时以 root/<source> 为准。"""
    base = (root or _CACHE_OVERRIDE or os.environ.get("SCRAPER_CACHE_ROOT") or "").strip()
    if not base:
        base = scraper_cache_base()
    return os.path.join(base, str(source or "").lower())


def bind_source_cache(source: str, kind: str = "json", *, root: str | None = None,
                      offline: bool = False):
    """源级请求/附件缓存**单例绑定**：返回（首次创建并记忆的）缓存实例。

    kind：
      "json" → ResponseCache（JSON 响应）
      "text" → TextResponseCache（HTML/文本响应）
      "blob" → BlobCache（二进制附件）
    root：缺省自动 = source_cache_root(source)（统一根 <base>/<source>；kind=blob 追加
          /attachments 子目录，与 SourceCache.blobs 结构一致）；显式给定时**直接作为
          该源缓存目录**（兼容 CLI --cache-dir 语义）。
    同 (source, kind, root) 复用同一实例。collector 一次调用即可消除自写薄包装。
    """
    global _KIND_CLASSES
    if not _KIND_CLASSES:
        _KIND_CLASSES.update({"json": ResponseCache, "text": TextResponseCache, "blob": BlobCache})
    if kind not in _KIND_CLASSES:
        raise ValueError(f"未知缓存类型: {kind!r}（可用 json/text/blob）")
    if root:
        r = root
    elif kind == "blob":
        r = os.path.join(source_cache_root(source), "attachments")
    else:
        r = source_cache_root(source)
    key = (source, kind, os.path.abspath(r))
    if key not in _BOUND:
        _BOUND[key] = _KIND_CLASSES[kind](r)
    if offline:
        _BOUND[key].set_offline(True)
    return _BOUND[key]


def _endpoint_key(endpoint: str) -> str:
    """从完整 URL 或裸端点名提取用作文件名前缀的端点标识。"""
    if "://" in endpoint or "/" in endpoint:
        return endpoint.rsplit("/", 1)[-1] or endpoint
    return endpoint


class ResponseCache:
    """JSON 响应缓存：确定性文件名 = ``<ep>__<sorted_params 编码>``。

    与 nfra 原 ``_cache_path`` 算法逐字一致，保证历史缓存可直接迁移复用。
    """

    def __init__(
        self,
        root: str,
        *,
        offline: bool = False,
        ttl: int | None = None,
    ) -> None:
        self.root = root
        self.offline = offline
        self.ttl = ttl
        os.makedirs(root, exist_ok=True)

    # —— 寻址 ——
    def path(self, endpoint: str, params: dict) -> str:
        ep = _endpoint_key(endpoint)
        qs = urllib.parse.urlencode(sorted(params.items()))
        safe = _VALID.sub("_", qs)
        return os.path.join(self.root, "%s__%s.json" % (ep, safe[:160]))

    def exists(self, endpoint: str, params: dict) -> bool:
        return os.path.exists(self.path(endpoint, params))

    # —— 读写 ——
    def get(self, endpoint: str, params: dict) -> dict | None:
        p = self.path(endpoint, params)
        if not os.path.exists(p):
            if self.offline:
                raise OfflineMiss("%s ? %s" % (endpoint, urllib.parse.urlencode(params)))
            return None
        if self.ttl is not None:
            try:
                if (time.time() - os.path.getmtime(p)) > self.ttl:
                    return None
            except OSError:
                return None
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            # 缓存损坏 → 视为未命中（由调用方重新请求）
            return None

    def put(self, endpoint: str, params: dict, data: Any, *, overwrite: bool = True) -> str:
        p = self.path(endpoint, params)
        if not overwrite and os.path.exists(p):
            return p
        _atomic_write_json(p, data)
        return p

    def set_offline(self, flag: bool) -> None:
        self.offline = bool(flag)

    def fetch(
        self,
        endpoint: str,
        params: dict,
        fetcher: Callable[[dict], Any],
        *,
        offline: bool | None = None,
    ) -> Any:
        """高层便捷：命中即返回；否则调用 fetcher(params) 并 put 后返回。

        offline 优先用形参，否则用实例 offline。离线且未命中时抛 OfflineMiss。
        """
        use_offline = self.offline if offline is None else offline
        p = self.path(endpoint, params)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass  # 损坏则重新获取
        if use_offline:
            raise OfflineMiss("%s ? %s" % (endpoint, urllib.parse.urlencode(params)))
        data = fetcher(params)
        self.put(endpoint, params, data)
        return data


class TextResponseCache:
    """文本/HTML 响应缓存：与 ``ResponseCache`` 同款确定性命名（``<ep>__<sorted_params 编码>``），

    但落盘为 UTF-8 **文本**（扩展名 ``.txt``），用于缓存 gov / pbc 等返回 HTML 的源。
    命名算法与 ``ResponseCache.path`` 完全一致（仅扩展名不同），保证同请求在不同缓存类型间
    可对应、可迁移。

    行为契约与 ``ResponseCache`` 对齐：
      * 命中读盘跳过网络；``offline`` 下缺失抛 ``OfflineMiss``；
      * 仅在「成功响应」后 ``put``（调用方负责仅在有效内容时写盘，不缓存错误/拦截页）；
      * 原子写（tempfile + os.replace）。
    """

    def __init__(
        self,
        root: str,
        *,
        offline: bool = False,
        ttl: int | None = None,
    ) -> None:
        self.root = root
        self.offline = offline
        self.ttl = ttl
        os.makedirs(root, exist_ok=True)

    # —— 寻址（与 ResponseCache 同算法，扩展名 .txt）——
    def path(self, endpoint: str, params: dict) -> str:
        ep = _endpoint_key(endpoint)
        qs = urllib.parse.urlencode(sorted(params.items()))
        safe = _VALID.sub("_", qs)
        return os.path.join(self.root, "%s__%s.txt" % (ep, safe[:160]))

    def exists(self, endpoint: str, params: dict) -> bool:
        return os.path.exists(self.path(endpoint, params))

    # —— 读写 ——
    def get(self, endpoint: str, params: dict) -> str | None:
        p = self.path(endpoint, params)
        if not os.path.exists(p):
            if self.offline:
                raise OfflineMiss("%s ? %s" % (endpoint, urllib.parse.urlencode(params)))
            return None
        if self.ttl is not None:
            try:
                if (time.time() - os.path.getmtime(p)) > self.ttl:
                    return None
            except OSError:
                return None
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            # 缓存损坏 → 视为未命中（由调用方重新请求）
            return None

    def put(self, endpoint: str, params: dict, text: str, *, overwrite: bool = True) -> str:
        p = self.path(endpoint, params)
        if not overwrite and os.path.exists(p):
            return p
        d = os.path.dirname(p) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, p)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return p

    def set_offline(self, flag: bool) -> None:
        self.offline = bool(flag)

    def fetch(
        self,
        endpoint: str,
        params: dict,
        fetcher: Callable[[dict], str],
        *,
        offline: bool | None = None,
    ) -> str:
        """高层便捷：命中即返回文本；否则调用 fetcher(params) 并 put 后返回。

        offline 优先用形参，否则用实例 offline。离线且未命中时抛 OfflineMiss。
        """
        use_offline = self.offline if offline is None else offline
        p = self.path(endpoint, params)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return fh.read()
            except Exception:
                pass  # 损坏则重新获取
        if use_offline:
            raise OfflineMiss("%s ? %s" % (endpoint, urllib.parse.urlencode(params)))
        data = fetcher(params)
        self.put(endpoint, params, data)
        return data


class BlobCache:
    """二进制附件缓存：按 owner（如 doc_id）在子目录存原始字节 + manifest 追踪。

    文件名即 content-sha256（保留原扩展名），天然去重；manifest.json 记录
    每个附件的元信息，供下游抽取/校验复用。
    """

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _owner_dir(self, owner_id: Any) -> str:
        return os.path.join(self.root, str(owner_id))

    def manifest_path(self, owner_id: Any) -> str:
        return os.path.join(self._owner_dir(owner_id), "manifest.json")

    def manifest(self, owner_id: Any) -> dict:
        p = self.manifest_path(owner_id)
        if os.path.exists(p):
            try:
                m = json.load(open(p, encoding="utf-8"))
                m.setdefault("doc_id", owner_id)
                m.setdefault("attachments", [])
                return m
            except Exception:
                pass
        return {"doc_id": owner_id, "attachments": []}

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def save(
        self,
        owner_id: Any,
        data: bytes,
        name_hint: str,
        **meta: Any,
    ) -> dict:
        """存一份附件原始字节，按 sha256 命名（保留扩展名），更新 manifest。

        返回该附件 manifest 条目（含 attachment_name / sha256 / source_saved / **meta）。
        同名文件已存在则更新条目而非重复落盘。
        """
        ext = os.path.splitext(name_hint)[1] or ""
        h = self._sha256(data)
        fname = h + ext
        d = self._owner_dir(owner_id)
        os.makedirs(d, exist_ok=True)
        fpath = os.path.join(d, fname)
        with open(fpath, "wb") as fh:
            fh.write(data)

        m = self.manifest(owner_id)
        entry = {"attachment_name": fname, "sha256": h, "source_saved": True, **meta}
        for e in m["attachments"]:
            if e.get("attachment_name") == fname:
                e.update(entry)
                break
        else:
            m["attachments"].append(entry)
        m["attachment_count"] = len(m["attachments"])
        _atomic_write_json(self.manifest_path(owner_id), m)
        return entry

    def load(self, owner_id: Any, filename: str) -> bytes:
        with open(os.path.join(self._owner_dir(owner_id), filename), "rb") as fh:
            return fh.read()

    def path(self, owner_id: Any, filename: str) -> str:
        return os.path.join(self._owner_dir(owner_id), filename)


class SourceCache:
    """某数据源的缓存聚合：响应缓存 + 二进制附件缓存，统一命名空间。"""

    def __init__(self, root: str, source: str) -> None:
        self.root = root
        self.source = source
        self.responses = ResponseCache(os.path.join(root, source))
        self.blobs = BlobCache(os.path.join(root, source, "attachments"))


def get_source_cache(source: str, root: str | None = None) -> SourceCache:
    """工厂：按 source 标识获取统一缓存（默认根 ``regulatory_scrapers/cache``）。

    source 取值：``gov`` / ``mof`` / ``nfra`` / ``pbc`` / ``supp``（小写）。
    """
    root = root or default_cache_root()
    return SourceCache(root, source)
