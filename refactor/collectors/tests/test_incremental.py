# -*- coding: utf-8 -*-
"""增量翻页闸门：通用策略默认不早停，显式阈值才启用追新优化（不触网、不触库）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collectors.lib.incremental import KnownTracker, should_stop_paging
from collectors.lib.pipeline import load_master_context


def test_pipeline_injects_gate_into_tracker():
    """基类把阈值/full 标志注入通用闸门，四源不再自带配置。"""
    class _Master:
        items = [{"detail_url": "u1"}]

    class _Storage:
        def load_master(self):
            return _Master()

    _, tracker, by_key = load_master_context(
        _Storage(), lambda r: r.get("detail_url"),
        stop_after_empty_pages=2, scan_all=True)
    assert by_key == {"u1": _Master.items[0]}
    assert tracker.stop_after_empty_pages == 2
    assert tracker.scan_all is True
    for _ in range(3):
        tracker.note_page(0)
    assert tracker.should_stop() is False  # full 模式恒不早停


def test_default_no_early_stop():
    # 历史回填中断后重启：头部整页全 known，也不能停，必须继续走到未抓的尾部。
    for streak in (1, 5, 100):
        assert should_stop_paging(streak, 0, False) is False
    assert should_stop_paging(1, None, False) is False
    assert should_stop_paging(1, -1, False) is False


def test_explicit_threshold_still_works():
    assert should_stop_paging(1, 1, False) is True
    assert should_stop_paging(1, 2, False) is False
    assert should_stop_paging(2, 2, False) is True
    assert should_stop_paging(99, 2, True) is False  # full 模式恒不早停


def test_tracker_gate_is_shared_by_all_sources():
    t = KnownTracker(key_of=lambda r: r.get("k"), stop_after_empty_pages=2)
    t.note_page(0)
    assert t.should_stop() is False
    t.note_page(0)
    assert t.should_stop() is True
    t.note_page(3)  # 出现新条目，连击清零
    assert t.should_stop() is False
    t.note_empty_page()
    assert t.should_stop() is False


def test_tracker_default_and_full_mode():
    default = KnownTracker(key_of=lambda r: r.get("k"))
    for _ in range(5):
        default.note_page(0)
    assert default.should_stop() is False

    full = KnownTracker(key_of=lambda r: r.get("k"),
                        stop_after_empty_pages=1, scan_all=True)
    for _ in range(5):
        full.note_page(0)
    assert full.should_stop() is False
