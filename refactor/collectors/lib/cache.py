# -*- coding: utf-8 -*-
"""可选的 HTTP 响应磁盘缓存（仅 nfra 的 --cache-dir/--offline 预取流水线使用）。

注意：pbc / gov / mof 的透明 HTTP 响应重放已于重构中移除；此处仅保留 nfra 在显式
WAF 规避两阶段工作流下所需的缓存能力。如需为全源统一缓存方案，后续可在此扩展。
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
