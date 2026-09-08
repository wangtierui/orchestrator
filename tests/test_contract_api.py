# -*- coding: utf-8 -*-
"""interfaces.contract 字段别名归一 API 回归（2026-09-08 字段治理）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_alias_integrity():
    from interfaces.contract import assert_alias_integrity
    assert_alias_integrity()  # 无冲突即通过


def test_read_field_mixed_keys():
    from interfaces.contract import read_field
    # 中文列行
    assert read_field({"文件名称": "《X办法》", "时效状态": "valid"}, "title") == "《X办法》"
    assert read_field({"时效状态": "repealed"}, "timeliness_status") == "repealed"
    # 英文别名行
    assert read_field({"title": "t", "eff_status": "valid"}, "timeliness_status") == "valid"
    assert read_field({"doc_no": "保监发〔2017〕54号"}, "document_number") == "保监发〔2017〕54号"
    # 缺省
    assert read_field({"title": "t"}, "document_number") == ""


def test_en_aliases():
    from interfaces.contract import en_aliases
    a = en_aliases("title")
    assert a[0] == "title" and "文件名称" in a and "标题" in a
    assert "时效状态" in en_aliases("timeliness_status")
