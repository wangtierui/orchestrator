# -*- coding: utf-8 -*-
"""mof CollectorPipeline 迁移的 mock 集成测试（不触网）。

monkeypatch 网络层（net._request / net.RateLimiter / collect_attachments /
write_status / 锁），预置主库，跑通 collect()，
断言 mof 的"变更指纹 diff"被钩子正确表达：
  - 列表新增(1002) → added（抓详情，content 来自详情）；
  - 主库已有且指纹未变(1001) → unchanged（复用旧记录）；
  - 主库已有且指纹变更 → updated（重抓）；本用例未触发，但分支存在；
  - 主库有但列表缺失(1003) → removed（active=False）。
"""
import json
import os
import sys
import tempfile
import types
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["REFACTOR_STORAGE"] = "file"
if "collectors.storage.sql_backend" not in sys.modules:
    _stub = types.ModuleType("collectors.storage.sql_backend")
    _stub.SqlStore = object
    sys.modules["collectors.storage.sql_backend"] = _stub

from collectors.mof import collector as mof_mod
from collectors.mof.collector import MofCollector, _canon_law_id


def _seed_master(out_dir, items):
    path = os.path.join(out_dir, "mof_laws.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "mof", "captured_at": "2020-01-01T00:00:00+00:00",
                   "count": len(items), "items": items}, f, ensure_ascii=False)


class FakeRateLimiter:
    def __init__(self, *a, **k):
        pass

    def wait(self):
        pass


REC_A = {
    "id": "1001", "title": "法规A", "lcontent": "<p>A内容</p>",
    "lBuDate": "2024-01-01", "lSsDate": "2024-03-01", "lbbdw": "财政部",
    "lwh": "财令〔2024〕1号", "lcaption": "题注A",
    "ltflbName": "财政法律法规（财政部规章）", "fileCount": 0, "createDeptName": "X",
}
REC_B = {
    "id": "1002", "title": "法规B", "lcontent": "<p>B内容</p>",
    "lBuDate": "2024-02-02", "lSsDate": "2024-04-01", "lbbdw": "财政部",
    "lwh": "财令〔2024〕2号", "lcaption": "题注B",
    "ltflbName": "财政法律法规（财政部规章）", "fileCount": 0, "createDeptName": "X",
}


def fake_request(method, url, payload=None, rate=None):
    if method == "POST" and url.endswith("/lawFile/list"):
        page = (payload or {}).get("current", 1)
        if page == 1:
            return {"code": 200, "data": {"records": [REC_A, REC_B], "total": 2, "pages": 1}}
        return {"code": 200, "data": {"records": [], "total": 2, "pages": 1}}
    if method == "GET" and "/lawFile/get/" in url:
        rid = url.rsplit("/", 1)[-1]
        return {"code": 200, "data": {
            "id": rid, "lcontent": "<p>正文%s</p>" % rid,
            "lBuDate": "2024-01-01", "lSsDate": "2024-03-01", "lbbdw": "财政部",
            "lwh": "财令〔2024〕%s号" % rid, "lcaption": "题注%s" % rid, "lstate": "有效"}}
    return {"code": 200, "data": {}}


def test_mof_pipeline_migration():
    tmp = tempfile.mkdtemp(prefix="mof_test_")
    # 主库：1001（指纹未变 → unchanged）、1003（列表缺失 → removed）
    _seed_master(tmp, [
        {"id": 1001, "title": "法规A", "content": "A内容",
         "publish_date": "2024-01-01", "effective_date": "2024-03-01",
         "issuing_authority": "财政部", "document_number": "财令〔2024〕1号", "summary": "题注A",
         "active": True, "change": "added"},
        {"id": 1003, "title": "法规C", "content": "C内容",
         "publish_date": "2024-05-05", "effective_date": "",          "issuing_authority": "财政部",
         "document_number": "财令〔2024〕3号", "summary": "题注C", "active": True, "change": "added"},
    ])

    with mock.patch.object(mof_mod.net, "RateLimiter", FakeRateLimiter), \
         mock.patch.object(mof_mod.net, "_request", fake_request), \
         mock.patch.object(mof_mod, "collect_attachments", lambda rid, rate: []), \
         mock.patch.object(mof_mod, "write_status", lambda *a, **k: None):
        MofCollector().collect(
            tmp, category="1000000000000300000", limit=None,
            no_detail=False, no_attachments=False, refetch_attachments=False,
            delay=0)

    path = os.path.join(tmp, "mof_laws.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = {r["id"]: r for r in data["items"]}
    print("produced ids:", sorted(items.keys()))

    # 1002 列表新增 → added，正文来自详情
    b = items[1002]
    assert b["change"] == "added", b["change"]
    assert b["content"] == "正文1002", b["content"]
    assert b["detail_url"] and "1002" in b["detail_url"]
    assert b["active"] is True

    # 1001 主库已有且指纹未变 → unchanged（复用旧记录内容）
    a = items[1001]
    assert a["change"] == "unchanged", a["change"]
    assert a["content"] == "A内容", a["content"]
    assert a["active"] is True

    # 1003 主库有但列表缺失 → removed
    c = items[1003]
    assert c["change"] == "removed", c["change"]
    assert c["active"] is False

    # 计数口径
    assert sorted(items.keys()) == [1001, 1002, 1003]
    print("test_mof_pipeline_migration OK")


if __name__ == "__main__":
    test_mof_pipeline_migration()
    print("ALL MOF PIPELINE TESTS PASSED")
