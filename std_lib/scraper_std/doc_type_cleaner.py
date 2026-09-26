# -*- coding: utf-8 -*-
"""
doc_type_cleaner.py —— 文件类型标识（doc_type）提取与归一（纯函数）

适配参考脚本《1.1 完整类型标识清单》《1.2 提取函数》（本地参考目录，未随仓携带），
并按《doc_type/category 清洗方案》最终版（用户确认 2026-08-28 18:47）落地：
  - FILE_TYPES 原生三档，**不扩充**（用户更正 1）；
  - 别名归一（用户确认 1=A）：`令→命令`、`法→法律`；
  - 提取失败 → 返回 None（由调用方兜底 pending）；
  - file_type_group 分组（公文/法规类型/其他_分组名）供 _metadata 登记。

本模块为纯函数、无 IO，探查层与清洗层共用同一事实源。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

# G1 上收（2026-09-08）：doc_type 受控清单/别名/分组迁 config.enums 单一事实源，
# 本模块 re-export 保持旧符号可用（R5 同款）。原本地定义已删，禁止回迁副本。
_ORCH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
from config.enums import (  # noqa: E402,F401
    DOC_TYPE_ALIAS,
    DOC_TYPE_GROUP,
    FILE_TYPES,
    LEGAL_DOC_TYPES,
    REGULATORY_TYPES,
)

# 兼容旧符号：OTHER_GROUP = DOC_TYPE_GROUP（本模块 group_of 使用）
OTHER_GROUP: dict[str, str] = DOC_TYPE_GROUP  # noqa: PLC0105

_FILE_TYPES_SORTED: list[str] = sorted(FILE_TYPES, key=len, reverse=True)


def _group_of(ft: str) -> str:
    if ft in LEGAL_DOC_TYPES:
        return "公文"
    if ft in REGULATORY_TYPES:
        return "法规类型"
    return f"其他_{OTHER_GROUP.get(ft, '其他未分类')}"


def extract_doc_type(title: str) -> dict[str, Any]:
    """从标题提取文件类型标识（参考脚本 1.2 适配）。

    返回：{doc_type, group, match_pos, status}
      doc_type: 提取的原始标识（未做别名归一），None 表示失败
      group:    公文 / 法规类型 / 其他_分组名
      match_pos: end / middle / not_found / empty_title
      status:   success / not_found / empty_title
    """
    t = (title or "").strip()
    if not t:
        return {
            "doc_type": None,
            "group": None,
            "match_pos": "empty_title",
            "status": "empty_title",
        }
    # 策略 0（v3 补丁）：标题以右括号结尾时，先取末尾括号内文本作为真实文种载体
    #   例：中国人民银行令〔2025〕第4号(中国人民银行业务领域网络安全事件报告管理办法)
    #       → 括号内「…报告管理办法」→ 正确提取「办法」（否则末尾是「)」导致误命中「报告」）
    m = re.match(r"^.*?[（(]([^（）()]+)[）)]\s*$", t)
    if m:
        inner = m.group(1).strip()
        for ft in _FILE_TYPES_SORTED:
            if inner.endswith(ft):
                return {
                    "doc_type": ft,
                    "group": _group_of(ft),
                    "match_pos": "end_bracket",
                    "status": "success",
                }
    for ft in _FILE_TYPES_SORTED:
        if t.endswith(ft):
            return {"doc_type": ft, "group": _group_of(ft), "match_pos": "end", "status": "success"}
    for ft in _FILE_TYPES_SORTED:
        if ft in t:
            return {
                "doc_type": ft,
                "group": _group_of(ft),
                "match_pos": "middle",
                "status": "success",
            }
    return {
        "doc_type": None,
        "group": "其他未分类",
        "match_pos": "not_found",
        "status": "not_found",
    }


def normalize_doc_type(value: str, title: str = "") -> str:
    """清洗入口：已有值归一 + 别名；无值/非法值按标题提取。

    返回标准值（受控枚举）或 ""（提取失败，由调用方兜底 pending）。
    """
    raw = (value or "").strip()
    if raw and raw not in ("N/A", "-"):
        # 已有值：去别名（令→命令、法→法律），其余受控值原样
        aliased = DOC_TYPE_ALIAS.get(raw, raw)
        if aliased in FILE_TYPES or aliased in ("命令", "法律"):
            return aliased
        # 非受控存量值（如 supp 人为值 便函/国务院文件/行业示范文本）：按标题提取兜底
    r = extract_doc_type(title)
    dt = r["doc_type"]
    if dt is None:
        return ""
    return DOC_TYPE_ALIAS.get(dt, dt)


if __name__ == "__main__":
    # 自检（方案最终版预演断言抽样）
    cases = [
        ("中国银保监会关于印发《商业银行代理销售业务管理办法》的通知", "通知"),
        ("中华人民共和国反洗钱法", "法律"),
        ("国务院关于修改《中华人民共和国外资保险公司管理条例》的决定", "决定"),
        ("企业财务通则", ""),  # 通则不在 FILE_TYPES（不扩充）→ 提取失败
    ]
    for title, expect in cases:
        got = normalize_doc_type("", title)
        mark = "✓" if got == expect else "✗"
        print(f"  {mark} {title[:26]:<28} → {got!r} (期望 {expect!r})")
