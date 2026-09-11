# -*- coding: utf-8 -*-
"""tests/test_base_publish.py — Base Contract v1 验收（发布件 + 索引 + 统一查询面）

覆盖：
  1) 外部/内部发布件构建（计数/manifest sha256）；
  2) SQLite+FTS5 索引构建；
  3) interfaces/base_api 查询面（精确过滤 / 全文检索 / manifest 一致）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "modules"), os.path.join(_ROOT, "std_lib"),
           os.path.join(_ROOT, "interfaces")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_base_contract_v1():
    from base_publish import build_external as be_mod
    from base_publish import build_fts as bf_mod
    from base_publish import build_internal as bi_mod

    m = be_mod.build()
    assert m["counts"]["records"] > 1000, "外部记录数异常"
    assert m["counts"]["clauses"] > 0
    for name, meta in m["files"].items():
        assert len(meta["sha256"]) == 64, f"{name} sha256 缺失"
        assert meta["count"] == m["counts"][
            {"external_records.jsonl": "records", "external_clauses.jsonl": "clauses",
             "external_attachments.jsonl": "attachments"}[name]]

    mi = bi_mod.build()
    assert mi["counts"]["policies"] > 50, "内部制度数异常"
    assert mi["counts"]["clauses"] > 0

    fe = bf_mod.build_external()
    assert fe["records"] == m["counts"]["records"]
    fi = bf_mod.build_internal()
    assert fi["policies"] == mi["counts"]["policies"]

    from base_api import manifest, query_external, query_internal, search_external, search_internal
    assert manifest("external")["counts"]["records"] == m["counts"]["records"]
    rows = query_external(limit=3)
    assert rows and all(r.get("record_id") for r in rows)
    assert search_external("银行代理保险", limit=3), "外部 FTS 检索无命中"
    assert search_internal("销售行为", limit=3), "内部 FTS 检索无命中"
    pols = query_internal(limit=3)
    assert pols and pols[0].get("ipn", "").startswith("IPN-")
