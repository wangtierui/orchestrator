# -*- coding: utf-8 -*-
"""storage —— 跨模块共享的通用持久化层（顶层再导出）。

以 refactor/ 为项目根时可直接 `from storage import open_store, RecordStore, ...`；
采集器自身以 `from ..storage import ...`（相对）使用同一份实现，二者指向同一代码。
"""
from collectors.storage import (  # noqa: F401
    RecordNormalizer,
    RecordStore,
    StoredRecord,
    StorageSettings,
    canonicalize,
    get_store,
    open_store,
)

__all__ = [
    "RecordStore",
    "StoredRecord",
    "RecordNormalizer",
    "StorageSettings",
    "get_store",
    "open_store",
    "canonicalize",
]
