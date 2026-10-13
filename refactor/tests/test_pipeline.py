# -*- coding: utf-8 -*-
"""lib/pipeline 单元测试：load_master_context 与 CollectorPipeline 路由（不触网）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 包根 refactor/

from collectors.lib.incremental import KnownTracker
from collectors.lib.pipeline import (
    CollectorContext,
    CollectorPipeline,
    load_master_context,
)


class FakeMaster:
    def __init__(self, items):
        self.items = items


class FakeStorage:
    def __init__(self, items):
        self._items = items
        self.saved = None

    def load_master(self):
        return FakeMaster(self._items)

    def save_master(self, records, extra=None):
        self.saved = (records, extra)
        return "locator"


def test_load_master_context():
    master_items = [
        {"detail_url": "a", "fetch_status": "ok", "x": 1},
        {"detail_url": "b", "fetch_status": "fetch_failed"},
        {"detail_url": "c", "fetch_status": "ok", "x": 3},
    ]
    st = FakeStorage(master_items)
    master, tracker, by_key = load_master_context(
        st, lambda r: r.get("detail_url"),
        known_pred=lambda r: r.get("fetch_status") == "ok")
    assert sorted(tracker.known) == ["a", "c"], tracker.known        # 仅 ok
    assert by_key["a"]["x"] == 1                                      # 取回内容
    assert by_key["b"]["fetch_status"] == "fetch_failed"             # 全量入 by_key
    assert tracker.classify({"detail_url": "a"}) == "known"
    assert tracker.classify({"detail_url": "b"}) == "new"
    assert tracker.classify({"detail_url": "a"}) == "dup"            # 本轮重复
    print("test_load_master_context OK")


class FakePipe(CollectorPipeline):
    source_id = "fake"

    def key_of(self, item):
        return item.get("id")

    def iter_list_entries(self, ctx):
        for e in ({"id": 1}, {"id": 1}, {"id": 2}):   # 末位 dup
            yield e

    def handle_new(self, entry, ctx):
        return {"id": entry["id"], "status": "new"}

    def preserve_missing_master(self, ctx):
        return True


def test_pipeline_routing():
    master_items = [{"id": 3, "status": "old"}]
    st = FakeStorage(master_items)
    pipe = FakePipe()
    tracker = KnownTracker(key_of=lambda r: r.get("id"))
    tracker.load_known(master_items)                       # id=3 已知
    ctx = CollectorContext(storage=st, master=FakeMaster(master_items),
                          tracker=tracker, master_by_key={3: master_items[0]},
                          out_dir="/tmp", opts={})
    records = pipe._collect(ctx)
    # id=1(new), id=2(new), id=3(已知→复用, 且列表缺失但 preserve)
    assert records == [
        {"id": 1, "status": "new"},
        {"id": 2, "status": "new"},
        {"id": 3, "status": "old"},
    ], records
    print("test_pipeline_routing OK")


if __name__ == "__main__":
    test_load_master_context()
    test_pipeline_routing()
    print("ALL PIPELINE TESTS PASSED")
