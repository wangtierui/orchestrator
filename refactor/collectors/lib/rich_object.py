# -*- coding: utf-8 -*-
"""本地 rich_object 占位（移植自 std_lib.scraper_std.rich_object）。

附件富字段构造；独立环境下返回空结构（与 mof 兜底一致），不阻断主流程。
"""
from typing import Any


def rich_object_fields(data: bytes, name: str = "", *, image_dir=None, rec_key="") -> dict[str, Any]:
    """返回附件结构化字段（独立环境不抽取，返回空）。"""
    return {}
