# -*- coding: utf-8 -*-
"""FileStore：JSON 文件落盘（默认后端，零外部依赖）。

写：统一为 StoredRecord 结构（{namespace, captured_at, count, items}），原子替换；
读：兼容历史产物（裸列表 / 旧 source 字段 / records / meta 等）。
file 后端为整文件覆盖，天然无重复行问题；write-once 语义主要体现在 postgres 后端。
"""
import json
import os
from datetime import datetime, timezone

from .base import StoredRecord

UTC = timezone.utc


class FileStore:
    def __init__(
        self,
        namespace: str,
        raw_dir: str = "data/raw",
        filename: str | None = None,
        normalizer=None,
        on_conflict: str = "nothing",
    ):
        self.namespace = namespace
        self.raw_dir = raw_dir
        self.filename = filename or f"{namespace}_data.json"
        self._normalizer = normalizer
        self._on_conflict = on_conflict

    def _path(self) -> str:
        return os.path.join(self.raw_dir, self.filename)

    def _normalize(self, data) -> StoredRecord:
        if isinstance(data, list):
            items = data
            captured_at = (
                datetime.fromtimestamp(os.path.getmtime(self._path()), UTC).isoformat()
                if os.path.exists(self._path())
                else ""
            )
            extra = None
        else:
            items = data.get("items") or data.get("records") or []
            captured_at = data.get("captured_at") or (data.get("meta") or {}).get(
                "captured_at"
            ) or ""
            known = {"namespace", "source", "captured_at", "count", "items", "records"}
            extras = {k: v for k, v in data.items() if k not in known}
            extra = extras or None
        return StoredRecord(
            namespace=self.namespace,
            captured_at=captured_at,
            count=len(items),
            items=items,
            extra=extra,
        )

    def save(
        self,
        items,
        captured_at=None,
        extra=None,
        *,
        normalizer=None,
        on_conflict=None,
    ) -> str:
        norm = normalizer or self._normalizer
        if norm:
            items = [norm(self.namespace, it) for it in items]
        captured_at = (captured_at or datetime.now(UTC)).isoformat()
        rec = StoredRecord(
            namespace=self.namespace,
            captured_at=captured_at,
            count=len(items),
            items=items,
            extra=extra,
        )
        payload = {
            "namespace": rec.namespace,
            "source": rec.namespace,  # 兼容历史下游按 source 读取
            "captured_at": rec.captured_at,
            "count": rec.count,
            "items": rec.items,
        }
        if rec.extra:
            payload.update(rec.extra)
        os.makedirs(self.raw_dir, exist_ok=True)
        path = self._path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子替换，避免写中途崩溃损坏
        return path

    # 兼容历史调用名
    save_master = save

    def load(self, version: str = "latest") -> StoredRecord:
        path = self._path()
        if not os.path.exists(path):
            return StoredRecord(
                namespace=self.namespace, captured_at="", count=0, items=[]
            )
        with open(path, encoding="utf-8") as f:
            return self._normalize(json.load(f))

    load_master = load
