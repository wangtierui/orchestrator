# -*- coding: utf-8 -*-
"""pbc CollectorPipeline 迁移的 mock 集成测试（不触网）。

monkeypatch 网络层（net.Fetcher / parse_listing / parse_detail / is_attachment /
_is_cacheable / CATEGORIES / ProcessLock），预置主库，跑通 collect()，
断言：
  - 主库已有(http://known，fetch_status='ok') → 复用旧记录并标 skipped_existing；
  - 新条目(http://n1,n2,n3) → 抓详情（parse_detail 生效，content 填充）；
  - 逐栏目分批落盘：save_master 至少被调用 2 次（栏目边界 checkpoint + 末尾）。
"""
import json
import os
import sys
import tempfile
import types
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 包根 refactor/

# 强制 file 后端（本环境若 REFACTOR_STORAGE=postgres 会强制 sqlalchemy）。
os.environ["REFACTOR_STORAGE"] = "file"

# storage/factory 顶层 import sqlalchemy（postgres 后端）；本环境未装，桩入占位。
if "collectors.storage.sql_backend" not in sys.modules:
    _stub = types.ModuleType("collectors.storage.sql_backend")
    _stub.SqlStore = object
    sys.modules["collectors.storage.sql_backend"] = _stub

from collectors.pbc import collector as pbc_mod
from collectors.pbc.collector import PbcCollector


def _seed_master(out_dir, items):
    path = os.path.join(out_dir, "pbc_laws.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "pbc", "captured_at": "2020-01-01T00:00:00+00:00",
                   "count": len(items), "items": items}, f, ensure_ascii=False)


class FakeFetcher:
    def __init__(self, *a, **k):
        pass

    def get(self, url, referer=None, binary=False, timeout=None):
        if binary:
            return (200, b"PK\x03\x04")
        return (200, "<html>stub</html>")


def test_pbc_pipeline_migration():
    tmp = tempfile.mkdtemp(prefix="pbc_test_")
    # 主库预置 1 条已知（fetch_status='ok' 才计入 known，断点续跑）
    _seed_master(tmp, [{
        "detail_url": "http://known", "title": "Mknown", "category": "国家法律",
        "content": "旧正文", "fetch_status": "ok",
    }])

    # 栏目：c1 含 [known, n1]，c2 含 [n2, n3]
    c1 = "http://pbc/c1"
    c2 = "http://pbc/c2"
    pbc_mod.net.CATEGORIES = [
        {"name": "国家法律", "index_url": c1 + "/index.html"},
        {"name": "部门规章", "index_url": c2 + "/index.html"},
    ]

    def fake_parse_listing(html_text, column_dir):
        if column_dir == c1:
            return ("mod1", 1, [("K标题", "http://known"), ("N1标题", "http://n1")])
        if column_dir == c2:
            return ("mod2", 1, [("N2标题", "http://n2"), ("N3标题", "http://n3")])
        return (None, 1, [])

    def fake_parse_detail(dtxt):
        return {"title": "详情标题", "publish_date": "2024-01-01", "document_number": "X",
                "issuing_authority": "Y", "effective_date": "2024-01-02", "content": "正文"}

    import collectors.storage.file_backend as fb
    real_save = fb.FileStore.save_master
    save_calls = []

    def spy_save(self, records, extra=None):
        save_calls.append(len(records))
        return real_save(self, records, extra=extra)

    with mock.patch.object(pbc_mod.net, "Fetcher", FakeFetcher), \
         mock.patch.object(pbc_mod.net, "_is_cacheable", lambda txt: True), \
         mock.patch.object(pbc_mod, "parse_listing", fake_parse_listing), \
         mock.patch.object(pbc_mod.parse, "parse_detail", fake_parse_detail), \
         mock.patch.object(pbc_mod.parse, "is_attachment", lambda url: False), \
         mock.patch.object(fb.FileStore, "save_master", spy_save):
        locator = PbcCollector().collect(
            tmp, max_items=0, no_attachments=True, csv=False, delay=1.0)

    with open(locator, encoding="utf-8") as f:
        data = json.load(f)

    items = data["items"]
    urls = [r["detail_url"] for r in items]
    print("produced detail_urls:", urls)
    # 顺序：c1 [known, n1] + c2 [n2, n3]
    assert urls == ["http://known", "http://n1", "http://n2", "http://n3"], urls

    by_url = {r["detail_url"]: r for r in items}
    # known 复用主库旧记录，标 skipped_existing（内容不丢）
    known = by_url["http://known"]
    assert known["fetch_status"] == "skipped_existing", known["fetch_status"]
    assert known["content"] == "旧正文", known["content"]
    # new 抓详情：content 填充
    for u in ("http://n1", "http://n2", "http://n3"):
        assert by_url[u]["fetch_status"] == "ok", by_url[u]["fetch_status"]
        assert by_url[u]["content"] == "正文", by_url[u]["content"]
    # 分类正确归属栏目
    assert by_url["http://n1"]["category"] == "国家法律"
    assert by_url["http://n2"]["category"] == "部门规章"

    # 逐栏目分批落盘：至少 2 次 save（栏目边界 checkpoint + 末尾）
    print("save_master call sizes:", save_calls)
    assert len(save_calls) >= 2, save_calls
    print("test_pbc_pipeline_migration OK")


if __name__ == "__main__":
    test_pbc_pipeline_migration()
    print("ALL PBC PIPELINE TESTS PASSED")
