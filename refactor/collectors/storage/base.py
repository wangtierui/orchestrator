# -*- coding: utf-8 -*-
"""跨模块共享的通用存储契约（与具体落盘介质、与"采集"语义解耦）。

任何模块（采集器、清洗、质检…）都通过 ``open_storage(namespace, ...)`` 获得一个
RecordStore，按 ``(namespace, key)`` 写入 / 读回记录。``namespace`` 即数据分区键：
采集器用 source 名（如 "pbc"），其它模块用各自命名。

设计要点：
- 写一次（默认 on_conflict="nothing"）：已存在 (namespace, key) 的行不插入；
- 覆盖（on_conflict="update"）：冲突时覆盖，用于确有"更新/作废"语义的源（如 mof）；
- 规范化（normalizer）：落盘前把异构 payload 映射为统一规范记录（见 canonical.py）。
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Protocol, runtime_checkable


@dataclass
class StoredRecord:
    """一次写入产出的统一记录形态（跨介质、跨模块通用）。"""

    namespace: str
    captured_at: str  # ISO-8601
    count: int
    items: List[dict]
    extra: dict | None = None


# 规范化函数签名：把某 namespace 的异构 payload 映射为统一规范记录。
RecordNormalizer = Callable[[str, dict], dict]


@runtime_checkable
class RecordStore(Protocol):
    """存储后端协议：绑定到单一 namespace。"""

    namespace: str

    def save(
        self,
        items: List[dict],
        captured_at=None,
        extra: dict | None = None,
        *,
        normalizer: RecordNormalizer | None = None,
        on_conflict: str = "nothing",
    ) -> str:
        """写入一批条目，返回可定位标识（文件路径 / run_id）。

        normalizer : 覆盖实例默认规范化器（落盘前将 payload 映射为统一规范记录）。
        on_conflict: "nothing"（默认，写一次）| "update"（冲突覆盖）。
        """
        ...

    def load(self, version: str = "latest") -> StoredRecord:
        """读回记录；无数据时返回空 StoredRecord。

        "latest" 语义：返回该 namespace 当前全量（跨 run 并集），captured_at 取最近写入时间。
        """
        ...
