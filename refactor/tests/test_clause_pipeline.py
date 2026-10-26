# -*- coding: utf-8 -*-
"""refactor.clause 单元测试：条文抽取编排（不触网，file 后端 + 临时目录）。

覆盖：单条解析、空正文跳过、端到端读 cleaned → 写 clauses、产物结构正确性。
PG 后端只改变落盘介质（key 派生/冲突策略由 collectors.storage 负责，另有其测试），
本测试用 file 后端验证编排逻辑本身。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 包根 refactor/

from collectors.storage import get_store
from collectors.storage.config import StorageSettings
from clause.pipeline import build_clause_for_record, build_clause_index


LAW_TEXT = (
    "第一章 总则\n"
    "第一条 为了规范经营行为，制定本办法。\n"
    "第二条 本办法适用于中华人民共和国境内。\n"
    "第二章 分则\n"
    "第三条 机构应当审慎经营，防范风险。"
)


def _settings():
    return StorageSettings(backend="file", raw_dir=tempfile.mkdtemp(prefix="clause_test_"))


def test_build_clause_for_record_law():
    rec = {
        "source": "gov",
        "dedup_key": "gov-1",
        "detail_url": "http://example.gov/1",
        "title": "测试办法",
        "document_number": "2020-1",
        "publish_date": "2020-01-01",
        "effective_date": "2020-02-01",
        "content": LAW_TEXT,
    }
    row = build_clause_for_record(rec)
    assert row is not None
    assert row["mode"] == "law"
    assert row["chapter_count"] == 2
    assert row["article_count"] == 3
    assert row["detail_url"] == "http://example.gov/1"
    # 最大化精简：仅保留 rfn 锚点 + rag 所需的身份字段，其余透传字段已删除
    _trimmed = (
        "source",
        "publish_date",
        "effective_date",
        "category",
        "summary",
        "issuing_authority",
        "scope",
        "source_url",
        "issue_organ",
    )
    assert all(k not in row for k in _trimmed)
    assert isinstance(row["articles"], list) and len(row["articles"]) == 3
    assert "validation" in row and "status" in row["validation"]


def test_build_clause_for_record_empty_content():
    rec = {"source": "gov", "dedup_key": "gov-2", "detail_url": "", "body_text": "   "}
    assert build_clause_for_record(rec) is None


def test_build_clause_index_end_to_end():
    s = _settings()
    cleaned = [
        {
            "source": "gov",
            "dedup_key": "gov-1",
            "detail_url": "http://example.gov/1",
            "title": "测试办法",
            "document_number": "2020-1",
            "publish_date": "2020-01-01",
            "effective_date": "2020-02-01",
            "content": LAW_TEXT,
        },
        {  # 无正文 → 应被跳过
            "source": "mof",
            "dedup_key": "mof-1",
            "detail_url": "http://example.mof/1",
            "title": "空正文",
            "content": "",
        },
    ]
    get_store("cleaned", s).save(cleaned, on_conflict="update")

    summary = build_clause_index(s)
    assert summary["total_records"] == 2
    assert summary["parsed"] == 1
    assert summary["skipped"] == 1
    assert summary["clause_count"] == 1
    assert summary["sources"] == {"gov": 1, "mof": 1}

    out = get_store("clauses", s).load()
    assert out.count == 1
    clause = out.items[0]
    assert clause["article_count"] == 3
    assert clause["chapter_count"] == 2
    # validation 必须存在且为 dict
    assert isinstance(clause["validation"], dict)
    assert clause["dedup_key"] == "gov-1"


def test_build_clause_index_idempotent_update():
    s = _settings()
    a = {
        "source": "nfra",
        "dedup_key": "n-1",
        "detail_url": "http://example.nfra/1",
        "title": "A",
        "content": "第一条 内容甲。",
    }
    get_store("cleaned", s).save([a], on_conflict="update")
    build_clause_index(s)
    # 改正文后重跑（同 detail_url）→ 覆盖而非重复
    a["content"] = "第一条 内容乙，已更新。"
    get_store("cleaned", s).save([a], on_conflict="update")
    build_clause_index(s)
    out = get_store("clauses", s).load()
    assert out.count == 1
    assert "已更新" in out.items[0]["articles"][0]["body"]
