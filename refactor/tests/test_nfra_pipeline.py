# -*- coding: utf-8 -*-
"""nfra CollectorPipeline 迁移的 mock 集成测试（不触网）。

monkeypatch 网络层（get_child_items / get_list_for_item / get_detail /
load_attachments / download_original_doc），预置主库，跑通 collect()，
断言：
  - 列表跨页重复(docId=1 二次出现) → 仅一条；
  - 主库已有(docId=1,3) → 直接复用旧记录（不抓详情）；
  - 新条目(docId=2) → 抓详情（get_detail 仅被调用一次）；
  - title_link 分支(docId=4) → 不抓详情，detail_url=titleLink；
  - 列表缺失但主库有(docId=3) → 并集保护重新并入。
"""
import json
import os
import sys
import tempfile
import types
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 包根 refactor/

# 本环境若 REFACTOR_STORAGE=postgres 会强制 sqlalchemy；测试仅验证 file 后端，
# 强制回 file（不影响被测管线逻辑）。
os.environ["REFACTOR_STORAGE"] = "file"

# storage/factory 顶层 import sqlalchemy（postgres 后端）；本环境未装。
# 桩入占位模块，使默认 file 后端可正常加载（postgres 分支不在此测试范围）。
if "collectors.storage.sql_backend" not in sys.modules:
    _stub = types.ModuleType("collectors.storage.sql_backend")
    _stub.SqlStore = object
    sys.modules["collectors.storage.sql_backend"] = _stub

from collectors.nfra import collector as nfra_mod
from collectors.nfra.collector import NfraCollector, build_detail_url, BASE


def _seed_master(out_dir, items):
    path = os.path.join(out_dir, "nfra_regulations.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "nfra", "captured_at": "2020-01-01T00:00:00+00:00",
                   "count": len(items), "items": items}, f, ensure_ascii=False)


def test_nfra_pipeline_migration():
    tmp = tempfile.mkdtemp(prefix="nfra_test_")
    # 主库预置：docId=1（已知）、docId=3（列表缺失，需并集保护）
    _seed_master(tmp, [
        {"doc_id": 1, "title": "M1", "category": "X", "publish_date": "2024-01-01"},
        {"doc_id": 3, "title": "M3", "category": "Y", "publish_date": "2024-03-03"},
    ])

    rows = [
        {"docId": 1, "docSubtitle": "标题1", "publishDate": "2024-01-01",
         "isTitleLink": 0, "docFileUrl": "", "pdfFileUrl": ""},
        {"docId": 2, "docSubtitle": "标题2", "publishDate": "2024-02-02",
         "isTitleLink": 0, "docFileUrl": "", "pdfFileUrl": ""},
        {"docId": 1, "docSubtitle": "标题1", "publishDate": "2024-01-01",  # 跨页重复
         "isTitleLink": 0, "docFileUrl": "", "pdfFileUrl": ""},
        {"docId": 4, "docSubtitle": "标题4", "publishDate": "2024-04-04",
         "isTitleLink": 1, "titleLink": "http://t4", "docFileUrl": "", "pdfFileUrl": ""},
    ]

    get_detail_calls = []

    def fake_get_detail(opener, doc_id, dmin, dmax):
        get_detail_calls.append(doc_id)
        return {
            "doc_id": doc_id, "title": "D%d标题" % doc_id,
            "publish_date": "2024-02-02", "build_date": "2024-02-01",
            "document_number": "令〔2024〕%d号" % doc_id, "effective_date": "2024-03-01",
            "index_no": "索引%d" % doc_id, "issuing_authority": "总局",
            "category": "", "summary": "摘要%d" % doc_id,
            "content": "正文内容%d" % doc_id, "doc_file_url": "", "pdf_file_url": "",
        }

    with mock.patch.object(nfra_mod, "get_child_items",
                           return_value=[{"item_id": 111, "item_name": "栏目A", "type": ""}]), \
         mock.patch.object(nfra_mod, "get_list_for_item",
                           side_effect=lambda op, item_id, dmin, dmax: rows if item_id == 111 else []), \
         mock.patch.object(nfra_mod, "get_detail", side_effect=fake_get_detail), \
         mock.patch.object(nfra_mod, "load_attachments", return_value=[]), \
         mock.patch.object(nfra_mod, "download_original_doc", return_value=""), \
         mock.patch.object(nfra_mod.os, "makedirs", lambda p, exist_ok=False: None):
        locator = NfraCollector().collect(
            tmp, limit=0, no_detail=False, cache_dir="", offline=False)

    with open(locator, encoding="utf-8") as f:
        data = json.load(f)

    items = data["items"]
    ids = [r["doc_id"] for r in items]
    print("produced doc_ids:", ids)
    # 顺序：列表 [doc1(known), doc2(new), doc4(new)] + 并集 [doc3]
    assert ids == [1, 2, 4, 3], ids

    by_id = {r["doc_id"]: r for r in items}
    # known 复用主库旧记录
    assert by_id[1]["title"] == "M1" and by_id[1]["category"] == "X"
    # new 抓详情
    assert by_id[2]["title"] == "D2标题" and by_id[2]["content"] == "正文内容2"
    assert by_id[2]["detail_url"] == build_detail_url(2, 111)
    # title_link 分支：不抓详情，detail_url=titleLink，content 空
    assert by_id[4]["detail_url"] == "http://t4" and by_id[4]["content"] == ""
    # 并集保护：主库旧记录
    assert by_id[3]["title"] == "M3" and by_id[3]["category"] == "Y"

    # get_detail 仅对 docId=2 调用一次（known/并集/title_link 均不触发详情抓取）
    assert get_detail_calls == [2], get_detail_calls
    # 无错误
    assert data.get("errors") == [], data.get("errors")
    print("test_nfra_pipeline_migration OK")


if __name__ == "__main__":
    test_nfra_pipeline_migration()
    print("ALL NFRA PIPELINE TESTS PASSED")
