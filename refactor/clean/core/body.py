# -*- coding: utf-8 -*-
"""body.py —— 正文清洗 + 断句修复的组合配方（core 业务核心）。

把原 ``BodyCleanStage`` 的编排（clean_text → 按需 repair_text → 写 metadata 标记）
下沉到这里，stage 只负责遍历调用。
"""
from __future__ import annotations

from typing import Any

from .cleaner import clean_text
from .sentence_split import repair_text


def clean_and_repair(rec: dict[str, Any], settings: "Any") -> dict[str, Any]:
    """单条正文清洗配方：清理 + 断句修复 + 落 metadata 标记。

    - ``clean_text``（去噪/空白/乱码），保留段落换行；
    - ``sentence_split_on`` 时 ``repair_text``（表格 / 无标点长文兜底切分）；
    - 写 ``_metadata``: ``clean_version`` / ``table_recovery_method``（经
      :func:`schema.annotate` 走受控键）；
    - 切分结果写顶层 ``split_sentences`` / ``raw_uncut_text``。
    返回同一 ``rec``（就地修改）。
    """
    from ..schema import annotate  # 延迟导入，避免 schema→core→schema 循环

    body = rec.get("content") or rec.get("body_text") or ""
    if not body:
        return rec
    cleaned = clean_text(body, keep_newlines=True)
    rec["content"] = cleaned
    annotate(rec, clean_version=settings.clean_version)
    if getattr(settings, "sentence_split_on", False):
        repaired = repair_text(cleaned, source="webpage")
        if repaired["is_table"]:
            cur = rec.get("_metadata", {}).get("table_recovery_method")
            annotate(rec, table_recovery_method=cur or "pending_table_block")
        if repaired["split_sentences"]:
            rec["split_sentences"] = repaired["split_sentences"]
            rec["raw_uncut_text"] = repaired["raw_uncut_text"]
    return rec
