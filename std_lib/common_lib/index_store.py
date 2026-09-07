# -*- coding: utf-8 -*-
"""
std_lib.common_lib.index_store — 单例 JSON 索引骨架（get_index() 模式复用件）

背景：旧仓 clean_index（CleanIndex/get_clean_index 单例）与 classifier rfn
（RFNIndex/get_index 单例）各自实现了「持久化 JSON/CSV 索引 + 模块级单例 + 原子写」同一骨架；
本模块提供共享基类与单例管理，供 P3/P4 迁移后收敛（非强制重写既有索引，仅统一新增索引）。

使用：
    class MyIndex(JsonIndexStore):
        def _load_impl(self, data): ...   # data(dict) → 自建内存索引

    idx = get_store("my_index", MyIndex, path=...)   # 模块级单例
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from std_lib.common_lib.fs_lock import atomic_write_json


class JsonIndexStore:
    """基于 JSON 文件 + 模块级单例的只读/重建索引骨架。"""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.raw: dict[str, Any] = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.raw = json.load(fh)

    def reload(self) -> None:
        with open(self.path, encoding="utf-8") as fh:
            self.raw = json.load(fh)

    def save(self) -> None:
        atomic_write_json(self.path, self.raw)

    # 子类实现：把 self.raw 转成业务查询结构（惰性）
    def _build(self):
        return None


_store_registry: dict[str, Any] = {}


def get_store(name: str, cls: type, path: str, rebuild: Callable | None = None) -> Any:
    """模块级单例：name 唯一；rebuild 为重建工厂（返回 data dict 并持久化）。"""
    global _store_registry
    if name in _store_registry:
        return _store_registry[name]
    if rebuild is not None:
        data = rebuild()
        atomic_write_json(path, data)
    _store_registry[name] = cls(path)
    return _store_registry[name]


def drop_store(name: str) -> None:
    _store_registry.pop(name, None)
