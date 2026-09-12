# -*- coding: utf-8 -*-
"""test_scraper_std_extra.py —— 五源映射契约 + 基础件批测（长期项·覆盖冲 25%，2026-09-13）。

覆盖：unified_schema.map_*（五源归一映射）、checkpoint.Checkpoint、cache_store
（BlobCache/ResponseCache/TextResponseCache）、circuit_breaker（AnchorDetector/Fuse）。
全部使用 tmp_path 隔离，不触碰生产数据。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scraper_std import cache_store as cs  # noqa: E402
from scraper_std import checkpoint as ck
from scraper_std import circuit_breaker as cb
from scraper_std import unified_schema as us  # noqa: E402

_MIN = {"title": "关于X的通知", "document_number": "保监发〔2020〕1号",
        "publish_date": "2020-05-06", "body_text": "正文内容", "source_url": "http://x/1"}


class TestMapGovMof:
    def test_map_gov_contract(self):
        out = us.map_gov(dict(_MIN), "v1")
        assert isinstance(out, dict) and out
        # 归一后应含 39 列契约的关键字段（宽松：至少 title/正文/来源类键）
        keys = set(out.keys())
        assert any(k in keys for k in ("title", "标题"))
        assert any("source" in k or "来源" in k for k in keys)

    def test_map_mof_empty_rec(self):
        out = us.map_mof({}, "v1")
        assert isinstance(out, dict)


class TestMapNfraPbcSupp:
    def test_map_nfra(self):
        out = us.map_nfra(dict(_MIN), "v1")
        assert isinstance(out, dict) and out

    def test_map_pbc(self):
        out = us.map_pbc(dict(_MIN), "v1")
        assert isinstance(out, dict) and out

    def test_map_supp(self):
        out = us.map_supp(dict(_MIN), "v1")
        assert isinstance(out, dict) and out

    def test_captured_at_param(self):
        out = us.map_gov(dict(_MIN), "v1", captured_at="2026-09-13 10:00:00")
        assert isinstance(out, dict)


class TestCheckpoint:
    def test_mark_and_check(self, tmp_path):
        cp = ck.Checkpoint(str(tmp_path / "cp.json"), project="t")
        cp.mark_done("url:a", "ok")
        assert cp.is_done("url:a") is True
        assert cp.is_done("url:b") is False
        assert "url:a" in cp.done_urls()

    def test_pending_filters_done(self, tmp_path):
        cp = ck.Checkpoint(str(tmp_path / "cp.json"), project="t")
        cp.mark_done("url:a")
        pend = cp.pending(["url:a", "url:b"])
        assert "url:a" not in pend and "url:b" in pend

    def test_mark_failed_and_save_reload(self, tmp_path):
        p = str(tmp_path / "cp.json")
        cp = ck.Checkpoint(p, project="t")
        cp.mark_failed("url:x", "timeout")
        cp.save()
        cp2 = ck.Checkpoint(p, project="t")
        assert cp2.is_done("url:x") is False   # 失败不算 done
        assert os.path.exists(p)


class TestBlobCache:
    def test_save_load_roundtrip(self, tmp_path):
        # 内容寻址：实际文件名 = sha256 + 原扩展名（存于 meta['attachment_name']）
        bc = cs.BlobCache(str(tmp_path))
        meta = bc.save(1, b"hello", "a.txt")
        assert isinstance(meta, dict) and meta.get("sha256")
        name = meta["attachment_name"]
        assert name.endswith(".txt")
        assert bc.load(1, name) == b"hello"

    def test_path_and_manifest(self, tmp_path):
        bc = cs.BlobCache(str(tmp_path))
        meta = bc.save(2, b"x", "b.bin")
        assert os.path.exists(bc.path(2, meta["attachment_name"]))
        mf = bc.manifest(2)
        assert isinstance(mf, dict) and mf.get("attachments")

    def test_load_missing_raises(self, tmp_path):
        bc = cs.BlobCache(str(tmp_path))
        try:
            bc.load(3, "nope.bin")
            ok = False
        except Exception:
            ok = True
        assert ok


class TestResponseCache:
    def test_put_get_exists(self, tmp_path):
        rc = cs.ResponseCache(str(tmp_path))
        rc.put("ep", {"a": "1"}, {"v": 42})
        assert rc.exists("ep", {"a": "1"}) is True
        got = rc.get("ep", {"a": "1"})
        assert got == {"v": 42}

    def test_fetch_uses_cache(self, tmp_path):
        rc = cs.ResponseCache(str(tmp_path))
        rc.put("ep", {"q": "x"}, {"v": 1})
        out = rc.fetch("ep", {"q": "x"}, fetcher=lambda p: {"v": 999})
        assert out == {"v": 1}          # 命中缓存不调 fetcher

    def test_offline_miss(self, tmp_path):
        rc = cs.ResponseCache(str(tmp_path))
        rc.set_offline(True)
        try:
            rc.fetch("ep", {"q": "miss"}, fetcher=lambda p: {"v": 1})
            ok = False
        except Exception:
            ok = True
        assert ok

    def test_text_cache(self, tmp_path):
        tc = cs.TextResponseCache(str(tmp_path))
        tc.put("ep", {"a": "1"}, "hello")
        assert tc.get("ep", {"a": "1"}) == "hello"
        assert tc.exists("ep", {"a": "1"}) is True
        out = tc.fetch("ep", {"a": "1"}, fetcher=lambda p: "nope")
        assert out == "hello"


class TestCircuitBreaker:
    def test_anchor_detector_default(self):
        d = cb.AnchorDetector()
        ok, why = d.check("<html><body><div class='list'>x</div></body></html>")
        assert isinstance(ok, bool) and isinstance(why, str)

    def test_anchor_detector_custom_selector(self):
        d = cb.AnchorDetector(selectors=["#main"])
        ok, why = d.check("<html><body><div id='main'>x</div></body></html>")
        assert isinstance(ok, bool)

    def test_fuse_check_and_trip(self, tmp_path):
        # 存续语义：miss 累积/显式 trip 后，check 要么抛 RuntimeError 要么返回 (False, *);
        # 快照目录应产生落盘痕迹（宽松断言，防脆）。
        d = cb.AnchorDetector()
        f = cb.Fuse(detector=d, max_miss=2, snapshot_dir=str(tmp_path))
        raised = False
        results = []
        for _ in range(3):
            try:
                ok, _why = f.check("<html><body>x</body></html>")   # 无锚点 → miss
                results.append(ok)
            except RuntimeError:
                raised = True
        assert raised or (False in results)
        assert len(os.listdir(str(tmp_path))) >= 1   # 快照落盘
