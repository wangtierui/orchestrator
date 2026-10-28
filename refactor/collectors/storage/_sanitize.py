# -*- coding: utf-8 -*-
"""存储写入边界的脏 Unicode 清洗。

抓取到的文本可能含代理码元（surrogate），来源有二：
1. 孤立代理（lone surrogate）：坏解码 / lxml 返回，psycopg 写 PG、json 写正文文件都会
   报 'surrogates not allowed'，须删除。
2. 合法代理对（surrogate pair）：某个 astral 字符（如 emoji / 生僻 CJK）被坏解码后，在
   Python str 里以"高代理+低代理"两个码元表示；psycopg 同样无法把代理码元编码成 UTF-8，
   须把它们合成回真实码点（单个 astral 字符）。

这里在落盘前统一清洗：先合成合法代理对，再删除残留的孤立代理。
"""
from __future__ import annotations

import re

# 用 chr 构造代理区间，避免源码里出现字面量代理字符（会导致源文件 UTF-8 解码失败）。
_HIGH = chr(0xD800)
_HIGH_END = chr(0xDBFF)
_LOW = chr(0xDC00)
_LOW_END = chr(0xDFFF)

# 合法代理对：高代理后紧跟低代理 → 合成真实码点
_PAIR = re.compile("[" + _HIGH + "-" + _HIGH_END + "][" + _LOW + "-" + _LOW_END + "]")
# 残留孤立代理（不成对的高/低代理）
_LONE = re.compile("[" + _HIGH + "-" + _LOW_END + "]")


def sanitize_surrogates(text: str) -> str:
    """去除/修复字符串中的非法 Unicode：

    - 合法代理对（高+低）合成真实 astral 码点；
    - 残留孤立代理删除；
    - NUL(0x00) 字节删除（PostgreSQL 的 text/json/jsonb 字段均拒绝 NUL）。
    """
    text = _PAIR.sub(
        lambda m: chr(
            (ord(m.group(0)[0]) - 0xD800) * 0x400
            + (ord(m.group(0)[1]) - 0xDC00)
            + 0x10000
        ),
        text,
    )
    text = _LONE.sub("", text)
    return text.replace("\x00", "")


def sanitize_value(v):
    """递归清洗任意结构（str / list / dict / 标量）中的代理码元。"""
    if isinstance(v, str):
        return sanitize_surrogates(v)
    if isinstance(v, list):
        return [sanitize_value(x) for x in v]
    if isinstance(v, dict):
        return {sanitize_value(k): sanitize_value(val) for k, val in v.items()}
    return v
