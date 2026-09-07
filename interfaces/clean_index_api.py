# -*- coding: utf-8 -*-
"""
interfaces/clean_index_api — clean_index 唯一访问接口（v1 SSOT 表）

P0：占位壳，声明签名；P3（clean_index 迁入共享层）后实现体委托 std_lib clean_index。
任何下游（classifier/scanner/timeliness）获取五源 cleaned 路径一律经本接口，
禁止硬编码 *_cleaned_YYYYMMDD 或 sys.path.insert 跨仓访问。
"""
from __future__ import annotations


class CleanIndexAPI:
    """五源 cleaned 最新快照访问层（签名契约先行）。"""

    def latest_csv_path(self, source_id: str) -> str | None:  # pragma: no cover
        raise NotImplementedError("P3 接入 std_lib clean_index 后实现")

    def latest_jsonl_path(self, source_id: str) -> str | None:  # pragma: no cover
        raise NotImplementedError("P3 接入 std_lib clean_index 后实现")

    def validate_files(self) -> dict:  # pragma: no cover
        raise NotImplementedError("P3 接入后实现")

    def is_fresh(self) -> bool:  # pragma: no cover
        raise NotImplementedError("P3 接入后实现")


_api: CleanIndexAPI | None = None


def get_clean_index_api() -> CleanIndexAPI:
    global _api
    if _api is None:
        _api = CleanIndexAPI()
    return _api
