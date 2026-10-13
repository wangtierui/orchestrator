# -*- coding: utf-8 -*-
"""文档 / 产物落盘路径（自包含，无外部依赖，不做任何 HTTP 响应缓存）。

- docs_root(*parts) → 文档/附件落盘基址 refactor/data/docs/<parts...>
- source_cache_root(source, kind) → nfra 预取流水线（curl 规避 WAF）的暂存根目录
  refactor/data/cache/<source>/<kind>（仅 nfra 两阶段抓取使用，非透明响应重放）。
"""
import os

from ..base import REPO_ROOT


def source_cache_root(source, kind="text", root=None):
    return root or os.path.join(REPO_ROOT, "data", "cache", source, kind)


def docs_root(*parts):
    return os.path.join(REPO_ROOT, "data", "docs", *parts)
