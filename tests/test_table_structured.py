# -*- coding: utf-8 -*-
"""表格类结构化存储：共享抽取 helper + 四源 map 透传（2026-09-08 仿 supp 打通）。

验证：
  1) std_lib.table_recovery.structured_table_fields：xlsx 字节 → 结构化表键（raw 落键样板）；
  2) map_gov/mof/nfra/pbc 对 raw 表键透传（raw 有键 → cleaned 39 列表格列带出，schema 零变更）。
纯本地（openpyxl 合成 xlsx），零网络/零外部依赖。
"""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "std_lib") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "std_lib"))

openpyxl = pytest.importorskip("openpyxl")

from std_lib.scraper_std import unified_schema  # noqa: E402
from std_lib.scraper_std.table_recovery import structured_table_fields  # noqa: E402


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据项表"
    ws.append(["字段名称", "报送口径", "备注"])
    ws.append(["保单号", "String", "必填"])
    ws.append(["客户姓名", "String", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_structured_table_fields_xlsx():
    res = structured_table_fields(_xlsx_bytes(), "样本.xlsx")
    assert res.get("table_recovery_method") == "structured"
    assert res.get("table_structured"), "应解析出结构化表"
    assert "报送口径" in res.get("table_raw_text", "")
    # 非表格字节（纯文本）不应产出键
    assert structured_table_fields(b"hello world", "a.txt") == {}


@pytest.mark.parametrize("name", ["gov", "mof", "nfra", "pbc"])
def test_map_transparent_table_fields(name):
    rec = {
        "title": "样例", "source": name, "body_text": "正文",
        "attachment_content": "附件全文文本",
        "table_structured": [{"sheet": "s1", "rows": [["a", "b"]]}],
        "table_raw_text": "a|b",
        "table_recovery_method": "structured",
    }
    out = unified_schema.MAPPERS[name](rec, "v1.0.0", "2026-09-08")
    assert out.get("table_structured"), f"{name} map 应透传 table_structured"
    assert out.get("table_recovery_method") == "structured"
    assert out.get("table_raw_text") == "a|b"
    assert out.get("attachment_content") == "附件全文文本"
    # 无表键 raw → 空占位（不破坏既有无表格记录）
    plain = unified_schema.MAPPERS[name]({"title": "x", "body_text": "b"}, "v1", "")
    assert plain.get("table_structured") == []
    assert plain.get("table_recovery_method") == ""
