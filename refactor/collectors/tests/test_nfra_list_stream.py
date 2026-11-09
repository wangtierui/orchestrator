# -*- coding: utf-8 -*-
"""nfra 列表逐页产出（不触网）：与其他三源同节奏，中断不丢已抓页。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collectors.nfra import collector as nfra


def _rows(ids):
    return [{"docId": i} for i in ids]


def test_get_list_for_item_yields_per_page(monkeypatch):
    pages = {1: _rows([1, 2]), 2: _rows([3, 4]), 3: _rows([5])}
    calls = []
    reported = []

    def fake_http(url, params, **kw):
        calls.append(params["pageIndex"])
        return {"data": {"total": 5, "rows": pages[params["pageIndex"]]}}

    monkeypatch.setattr(nfra, "http_get_json", fake_http)
    monkeypatch.setattr(nfra, "sleep_between", lambda *a, **k: None)
    seen = []
    for rows in nfra.get_list_for_item(
            "927", 0, 0, on_page=lambda p, t: reported.append((p, t))):
        seen.append([r["docId"] for r in rows])
    assert seen == [[1, 2], [3, 4], [5]]  # 每页立刻产出，不攒到最后
    assert calls == [1, 2, 3]
    assert [p for p, _ in reported] == [1, 2, 3]


def test_partial_pages_survive_failure(monkeypatch):
    def fake_http(url, params, **kw):
        if params["pageIndex"] == 2:
            raise RuntimeError("HTTP 403")
        return {"data": {"total": 100, "rows": _rows([params["pageIndex"]])}}

    monkeypatch.setattr(nfra, "http_get_json", fake_http)
    monkeypatch.setattr(nfra, "sleep_between", lambda *a, **k: None)
    got = []
    try:
        for rows in nfra.get_list_for_item("927", 0, 0):
            got.append([r["docId"] for r in rows])
    except RuntimeError:
        pass
    assert got == [[1]]  # 第 1 页已产出，不再因第 2 页失败整栏目丢弃
