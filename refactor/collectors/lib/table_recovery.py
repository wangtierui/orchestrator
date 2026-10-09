# -*- coding: utf-8 -*-
"""本地 table_recovery 占位（移植自 std_lib.scraper_std.table_recovery）。

旧格式表格结构化恢复；独立环境下返回空（与 mof 兜底一致），不阻断主流程。
"""
from typing import Any


def structured_table_fields(data: bytes, name: str = "", *, kind=None) -> dict[str, Any]:
    """返回表格结构化字段（独立环境不恢复，返回空）。"""
    return {}
