# -*- coding: utf-8 -*-
"""refactor.storage —— 跨模块共享的通用持久化层（顶层再导出）。

其它模块在"以 refactor 为顶层包"的运行方式下（如 `python -m refactor.clean.x`），
可直接 `from refactor.storage import open_store, RecordStore, StoredRecord`。
采集器自身以 `from ..storage import ...`（相对）使用同一份实现，二者指向同一代码。
"""
from refactor.collectors.storage import (  # noqa: F401
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
