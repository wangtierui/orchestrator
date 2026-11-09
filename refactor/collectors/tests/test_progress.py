# -*- coding: utf-8 -*-
"""统一进度日志单测（不触网、不触库）。

覆盖：计数/快照、节流与定时心跳、空实现兜底、基类 _collect 打点接线。
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collectors.lib.incremental import KnownTracker
from collectors.lib.pipeline import CollectorContext, CollectorPipeline
from collectors.lib.progress import NULL_PROGRESS, ProgressLogger, progress_of


class _FakeMaster:
    def __init__(self, items):
        self.items = items


class _FakeStorage:
    def __init__(self):
        self.save_calls = 0

    def load_master(self):
        return _FakeMaster([])

    def save_master(self, records, extra=None):
        self.save_calls += 1
        return "locator"


class _Pipe(CollectorPipeline):
    source_id = "fake"

    def key_of(self, item):
        return item.get("id")

    def iter_list_entries(self, ctx):
        for e in ({"id": 1}, {"id": 1}, {"id": 2}):
            yield e

    def handle_new(self, entry, ctx):
        return {"id": entry["id"], "fetch_status": "ok"}


def _ctx(progress):
    tracker = KnownTracker(key_of=lambda r: r.get("id"))
    return CollectorContext(
        storage=_FakeStorage(), master=_FakeMaster([]), tracker=tracker,
        master_by_key={}, out_dir="/tmp", opts={}, progress=progress)


def test_counters_and_snapshot():
    p = ProgressLogger("fake", heartbeat=False, every_items=100, interval=999)
    p.batch("栏目A")
    p.page(3, 12)
    p.scan(label="https://example.com/x")
    p.branch("new")
    p.result({"fetch_status": "ok"})
    p.result({"fetch_status": "no_content"})
    p.error()
    s = p.snapshot("heartbeat")
    assert "page=3/12" in s
    assert "batch=栏目A" in s
    assert "scan=1" in s and "new=1" in s
    assert "rec=2 ok=1 non_ok=1 err=1" in s
    assert "cur=https://example.com/x" in s
    assert "speed=" in s and "elapsed=" in s


def test_throttle_heartbeat_and_finish(caplog):
    p = ProgressLogger("fake", every_items=2, interval=0.05, heartbeat=True)
    with caplog.at_level(logging.INFO, logger="progress.fake"):
        p.start()
        p.page(1, 3)
        p.scan(label="a")
        p.branch("new")
        p.result({"fetch_status": "ok"})
        p.scan(label="b")
        p.branch("known")
        time.sleep(0.13)
        p.finish()
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("START ") for m in msgs), msgs
    assert any(m.startswith("page ") for m in msgs), msgs
    assert any(m.startswith("progress ") for m in msgs), msgs
    assert any(m.startswith("heartbeat ") for m in msgs), msgs
    assert any(m.startswith("done ") for m in msgs), msgs
    assert not p._thread.is_alive()


def test_null_progress_and_progress_of():
    assert progress_of(object()) is NULL_PROGRESS
    p = progress_of(type("C", (), {})())
    p.start()
    p.batch("x")
    p.page(1, 2)
    p.scan()
    p.branch("new")
    p.result(None)
    p.error()
    p.finish()
    assert p.snapshot() == ""


def test_canonical_shape_records_count_as_ok():
    """mof/gov 的 handle_new 不带 fetch_status（入库时由 canonicalize 推导）。"""
    p = ProgressLogger("fake", heartbeat=False, every_items=100, interval=999)
    p.result({"content": "法规正文", "attachments": []})
    p.result({"content": "", "attachments": []})
    p.result({"content": "法规正文", "fetch_status": "no_content"})
    p.result({"content": "法规正文",
              "attachments": [{"extract_status": "download_failed"}]})
    p.result({"content": "法规正文",
              "attachments": [{"extract_status": "unreachable_private_ip",
                               "unreachable": True}]})
    assert p._c["rec"] == 5
    assert p._c["ok"] == 2 and p._c["non_ok"] == 3
    p.finish()


def test_progress_status_matches_canonicalize():
    """日志的 ok 判定必须与最终落库的 fetch_status 完全一致。"""
    from collectors.lib.fetch_status import derive_from_record, is_ok
    from collectors.storage.canonical import canonicalize

    samples = [
        {"content": "正文", "attachments": []},
        {"content": "  ", "attachments": []},
        {"content": "正文", "fetch_status": "no_content"},
        {"content": "正文", "attachments": [{"extract_status": "download_failed"}]},
        {"content": "正文", "attachments": [{"extract_status": "needs_ocr"}]},
        {"content": "正文", "attachments": [{"extract_status": "unreachable_private_ip",
                                            "unreachable": True}]},
    ]
    for sample in samples:
        stored = canonicalize("mof", sample)["fetch_status"]
        assert is_ok(derive_from_record(sample)) == is_ok(stored), sample


def test_pipeline_wires_progress():
    p = ProgressLogger("fake", heartbeat=False, every_items=100, interval=999)
    ctx = _ctx(p)
    pipe = _Pipe()
    pipe._collect(ctx)
    # 逐条落库（已删攒批）：两个 new（id=1,id=2；id=1 重复为 dup 跳过）各 save 一次
    assert ctx.storage.save_calls == 2
    assert p._c["scan"] == 3
    assert p._c["new"] == 2 and p._c["dup"] == 1
    assert p._c["rec"] == 2 and p._c["ok"] == 2
    p.finish()


def test_single_field_set_for_all_sources():
    """四源共用同一渲染：字段集合一致，共享层不含任何阶段分支。"""
    def fields(src):
        p = ProgressLogger(src, heartbeat=False, every_items=100, interval=999)
        p.batch("栏目A")
        p.page(3, 10)
        p.scan(label="标题")
        p.branch("new")
        p.result({"fetch_status": "ok"})
        s = p.snapshot("page")
        p.finish()
        return {kv.split("=", 1)[0] for kv in s.split() if "=" in kv}

    assert fields("pbc") == fields("nfra") == fields("gov") == fields("mof")
    assert not hasattr(ProgressLogger, "phase")
    assert not hasattr(ProgressLogger, "list_rows")
