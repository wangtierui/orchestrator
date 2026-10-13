# -*- coding: utf-8 -*-
"""common.text —— 跨包共享的文本/日期基础工具（单一事实源）。

此前 `normalize_date` 在 `collectors/lib/text_utils.py` 与 `clean/core/cleaner.py`
各有一份，且契约不一致：
  - collectors 版：解析失败返回 ""（含时间只出日期）；
  - clean 版：解析失败保留原串（保守）、且支持 `YYYY-MM-DD HH:MM:SS`。

本模块统一为一份**超集**契约：
  - None / "" / "None" / "null" / "[]" → ""（空语义）；
  - list/tuple(年,月,日) → ISO，无法解析 → ""；
  - 字符串：优先按显式格式（含时间）解析，其次正则抽取日期；
    均失败 → **保留原串**（保守：绝不静默丢弃非空原始信息）。

对 collectors 的唯一行为变化：此前非空但不可解析会返回 ""，现返回原串
（数据保全，且下游 clean 用同一函数再次归一，两边口径从此一致）。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日",
    "%Y%m%d",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
]
_EMPTY = {"", "None", "null", "[]", "NoneType", "nan"}
_DATE_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})")


def normalize_date(value: Any) -> str:
    """日期归一（collectors 与 clean 共用）。详见模块 docstring 的契约说明。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return "%04d-%02d-%02d" % (int(value[0]), int(value[1]), int(value[2]))
        except (ValueError, TypeError):
            return ""
    if not isinstance(value, str):
        value = str(value)
    s = value.strip()
    if s in _EMPTY:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            dt = _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.hour or dt.minute or dt.second:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    m = _DATE_RE.search(s)
    if m:
        try:
            dt = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s  # 保守：保留原始信息，不静默丢弃


def content_sha256(text: str) -> str:
    """字符串 sha256 十六进制摘要（clean 的 content_hash 与 collectors 的
    content_sha256 同义，统一于此）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


if __name__ == "__main__":  # 离线自检
    assert normalize_date(None) == ""
    assert normalize_date("") == ""
    assert normalize_date("None") == ""
    assert normalize_date("2024-01-01") == "2024-01-01"
    assert normalize_date("2024/01/01 12:30") == "2024-01-01 12:30:00"
    assert normalize_date("近日") == "近日"          # 保守保留
    assert normalize_date([2024, 1, 2]) == "2024-01-02"
    assert content_sha256("") == hashlib.sha256(b"").hexdigest()
    print("[common.text] 离线自检通过")
