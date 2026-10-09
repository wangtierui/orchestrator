# -*- coding: utf-8 -*-
"""本地磁盘请求缓存 + 文档根路径。自包含，无外部依赖。

替代原 std_lib.scraper_std.cache_store 在爬虫侧的用法：
- `bind_source_cache(source, kind, root=None)` → 返回 LocalSourceCache（接口兼容旧 cache_store）
- `docs_root(*parts)` → 文档/附件落盘基址，统一收口到 refactor/data/docs 下
- `OfflineMiss` → 离线缺失异常
"""
import hashlib
import json
import os

from ..base import REPO_ROOT


class OfflineMiss(Exception):
    pass


def url_endpoint_key(endpoint, params=None):
    flat = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1((endpoint + "|" + flat).encode("utf-8")).hexdigest()


class LocalSourceCache:
    """按 (endpoint, params) 哈希落盘的极简请求缓存；支持离线只读模式。"""

    def __init__(self, source, kind, root=None):
        self.source = source
        self.kind = kind
        self.root = root or os.path.join(REPO_ROOT, "data", "cache", source, kind)
        self.offline = False

    def path(self, endpoint, params):
        return os.path.join(self.root, url_endpoint_key(endpoint, params) + ".json")

    def set_offline(self, flag):
        self.offline = bool(flag)

    def put(self, endpoint, params, data):
        p = self.path(endpoint, params)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, p)

    def get(self, endpoint, params):
        p = self.path(endpoint, params)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        if self.offline:
            raise OfflineMiss(endpoint)
        return None


def bind_source_cache(source, kind, root=None):
    return LocalSourceCache(source, kind, root=root)


def source_cache_root(source, kind="text", root=None):
    """返回某 (source, kind) 的缓存根目录（与 LocalSourceCache 的默认布局一致）。"""
    return root or os.path.join(REPO_ROOT, "data", "cache", source, kind)


def docs_root(*parts):
    """文档/附件落盘基址：refactor/data/docs/<parts...>。"""
    return os.path.join(REPO_ROOT, "data", "docs", *parts)
