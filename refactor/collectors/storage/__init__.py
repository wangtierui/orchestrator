# -*- coding: utf-8 -*-
"""采集器专用的 storage 薄封装。

在通用 RecordStore 之上注入：
1) 入库规范化（canonicalize）（canonicalize）—— 各源异构 payload 在入库入口统一；
2) 按源选择冲突策略 —— pbc/gov/nfra 官方文档不可变 → 写一次(nothing)；
   mof 含有效性/废止变更 → 覆盖(update)，以保留其增量/作废追踪。

采集器仍用 ``from ..storage import get_storage, StorageSettings, open_storage``，
方法仍叫 save_master/load_master（实例别名，向下兼容历史调用）。
"""
from .base import RecordNormalizer, RecordStore, StoredRecord
from .config import StorageSettings
from .canonical import canonicalize
from .factory import get_store, open_store

# 各源默认冲突策略
_DEFAULT_ON_CONFLICT = {
    "pbc": "nothing",
    "gov": "nothing",
    "nfra": "nothing",
    "mof": "update",  # mof 法规会"废止/失效/移除"，确需覆盖
}


def open_storage(source: str, out_dir: str | None = None, *, filename=None):
    oc = _DEFAULT_ON_CONFLICT.get(source, "nothing")
    return open_store(
        source, out_dir, filename=filename, normalizer=canonicalize, on_conflict=oc
    )


def get_storage(source: str, settings=None, *, filename=None):
    oc = _DEFAULT_ON_CONFLICT.get(source, "nothing")
    return get_store(
        source, settings, filename=filename, normalizer=canonicalize, on_conflict=oc
    )


__all__ = [
    "RecordStore",
    "StoredRecord",
    "StorageSettings",
    "get_storage",
    "open_storage",
]
