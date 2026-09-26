# -*- coding: utf-8 -*-
"""tests/test_dedup_key_uniqueness.py — dedup_key 唯一性校验（SSOT / 数据校验，第六批）

背景（v2 §2.3.2）：`dedup_key` 被声明为"唯一业务主键"，但此前只有非空断言
（`assert rec["dedup_key"]`），**跨记录去重键冲突无人发现**。`check_unique_dedup_keys`
补上唯一性，供 clean 管道/门禁据以告警或隔离（避免下游按 dedup_key 建索引时静默覆盖）。
"""
from __future__ import annotations

from std_lib.scraper_std.schema_validation import check_unique_dedup_keys


def _recs(*keys):
    return [{"dedup_key": k, "title": f"t{i}"} for i, k in enumerate(keys)]


def test_no_duplicates():
    ok, dups = check_unique_dedup_keys(_recs("a", "b", "c"))
    assert ok is True and dups == []


def test_duplicates_detected_with_indices():
    ok, dups = check_unique_dedup_keys(_recs("a", "b", "a", "c", "a"))
    assert ok is False
    assert dups == [{"dedup_key": "a", "count": 3, "indices": [0, 2, 4]}]


def test_missing_keys_are_skipped_not_falsely_flagged():
    """缺失 dedup_key 的记录不计入重复（由 `validate_record` 的必填校验另行拦截）。"""
    ok, dups = check_unique_dedup_keys([{"dedup_key": ""}, {"title": "无键"}])
    assert ok is True and dups == []


def test_empty_and_non_dict_tolerant():
    assert check_unique_dedup_keys([]) == (True, [])
    ok, dups = check_unique_dedup_keys([None, {"dedup_key": "x"}])  # type: ignore[list-item]
    assert ok is True
