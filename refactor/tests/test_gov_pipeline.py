# -*- coding: utf-8 -*-
"""gov CollectorPipeline 迁移的 mock 集成测试（不触网）。

monkeypatch 网络层（xzfgk 的 RobustSession.get_text / zhengceku.fetch /
fetch_gov_attachments / ProcessLock），预置主库，跑通 collect()，
断言 gov 的"双子源编排 + 主库并集(merge)"被钩子正确表达：
  - xzfgk 列表含 1 已知(K1) + 1 新(X1) → K1 跳过并靠 merge 保活、X1 抓详情得正文；
  - zhengceku 列表含 1 已知(Z1) + 1 新(Z2) → 同上；
  - 最终产物 = 旧主库(K1,Z1) ∪ 新(X1,Z2) = 4 条，新条目正文来自详情。
"""
import argparse
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

from collectors.gov import collector as gov_mod
from collectors.gov import zhengceku as zc_mod
from collectors.gov import attachments as att_mod
from collectors.gov.collector import GovCollector, XzfgkScraper


# —— 主库（已知条目，应被 merge 保活）——
K1_URL = "https://xzfg.moj.gov.cn/front/law/detail?id=K1"
X1_URL = "https://xzfg.moj.gov.cn/front/law/detail?id=X1"
Z1_URL = "https://www.gov.cn/zhengce/zhengceku/bmwj/content_111.htm"
Z2_URL = "https://www.gov.cn/zhengce/zhengceku/bmwj/content_222.htm"


def _seed_master(out_dir):
    path = os.path.join(out_dir, "gov_laws.json")
    items = [
        {"title": "法规K1", "detail_url": K1_URL, "full_text": "旧正文K1",
         "category": "行政法规", "source": "xzfgk", "publish_date": "2020-01-01"},
        {"title": "法规Z1", "detail_url": Z1_URL, "full_text": "旧正文Z1",
         "category": "部门文件", "source": "zhengceku", "publish_date": "2020-03-01"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "gov", "captured_at": "2020-01-01T00:00:00+00:00",
                   "count": len(items), "items": items}, f, ensure_ascii=False)


XZ_LIST_HTML = (
    '<html><body>'
    '<li class="list-item"><span class="title"><a href="/front/law/detail?id=K1">法规K1</a></span>'
    '<span class="publish-date">2020-01-01</span><span class="implemented-date">2020-02-01</span></li>'
    '<li class="list-item"><span class="title"><a href="/front/law/detail?id=X1">法规X1</a></span>'
    '<span class="publish-date">2024-01-01</span><span class="implemented-date">2024-02-01</span></li>'
    '</body></html>'
)
XZ_DETAIL_HTML = (
    '<html><body><h1 class="text-title">法规X1</h1>'
    '<div class="law-chapter">正文X1</div></body></html>'
)
ZC_LIST_HTML = (
    '<html><body><script>var nPageCount=1;</script>'
    '<li><a href="https://www.gov.cn/zhengce/zhengceku/bmwj/content_111.htm">法规Z1</a> 成文日期：2020-03-01</li>'
    '<li><a href="https://www.gov.cn/zhengce/zhengceku/bmwj/content_222.htm">法规Z2</a> 成文日期：2024-03-01</li>'
    '</body></html>'
)
ZC_DETAIL_HTML = (
    '<html><head><title>法规Z2</title></head>'
    '<body><div class="pages_content">正文Z2</div></body></html>'
)


class FakeSession:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_text(self, url, referer=None, params=None):
        if "search2.html" in url:
            page = (params or {}).get("PageIndex", 1)
            if page and page > 1:
                return None
            return XZ_LIST_HTML
        return XZ_DETAIL_HTML


def fake_fetch(url, binary=False, referer=None, timeout=35):
    if "home" in url:
        return ZC_LIST_HTML
    return ZC_DETAIL_HTML


def test_gov_pipeline_migration():
    tmp = tempfile.mkdtemp(prefix="gov_test_")
    _seed_master(tmp)

    with mock.patch.object(gov_mod, "RobustSession", FakeSession), \
         mock.patch.object(zc_mod, "fetch", fake_fetch), \
         mock.patch.object(gov_mod, "fetch_gov_attachments", lambda *a, **k: ([], "")), \
         mock.patch.object(att_mod, "fetch_gov_attachments", lambda *a, **k: ([], "")):
        GovCollector().collect(
            tmp, source="all", max_pages=0, max_items=0, no_details=False,
            details_limit=0, full=False, resume=None, delay_min=0, delay_max=0,
            timeout=30, retries=1, summary_len=200, csv=False,
            stop_after_empty_pages=1, backfill=False, max_seconds=0.0)

    path = os.path.join(tmp, "gov_laws.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    by_url = {r["detail_url"]: r for r in data["items"]}
    print("produced urls:", sorted(by_url.keys()))

    # 并集：旧主库(K1,Z1) ∪ 新(X1,Z2) = 4 条
    assert set(by_url) == {K1_URL, X1_URL, Z1_URL, Z2_URL}, set(by_url)

    # 新条目正文来自详情
    assert by_url[X1_URL]["full_text"] == "正文X1", by_url[X1_URL]["full_text"]
    assert by_url[Z2_URL]["full_text"] == "正文Z2", by_url[Z2_URL]["full_text"]
    # 已知条目未被重抓，保留主库旧正文（merge 保活）
    assert by_url[K1_URL]["full_text"] == "旧正文K1", by_url[K1_URL]["full_text"]
    assert by_url[Z1_URL]["full_text"] == "旧正文Z1", by_url[Z1_URL]["full_text"]
    # 子源标记保留
    assert by_url[X1_URL]["source"] == "xzfgk"
    assert by_url[Z2_URL]["source"] == "zhengceku"
    print("test_gov_pipeline_migration OK")


if __name__ == "__main__":
    test_gov_pipeline_migration()
    print("ALL GOV PIPELINE TESTS PASSED")
