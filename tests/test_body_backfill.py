# -*- coding: utf-8 -*-
"""tests/test_body_backfill.py — 「正文空但附件有文本」受控回填的守卫。

背景（实测，2026-10-19）
------------------------
`nfra_cleaned_*.quarantine.jsonl` **每日固定 5 条、连续 9 天同一批**
（2026-10-09 → 10-19），`_metadata.validation_errors` 全部只有一条
`body_text: required but null/empty`。

其中 1 条（《中国银保监会关于印发商业银行预期信用损失法实施管理办法的通知》）
**正文 0 字但 attachment_content 有 8727 字**，且附件开头即
「中国银保监会文 件 银保监规〔2022〕号 中国银保监会关于印发…」
⇒ 附件文本就是这份红头文件的正文，本可清洗却被隔离 = **逻辑缺陷**。

另 4 条 `attachment_content` 也是 0 字 ⇒ **源就缺正文**，隔离正确、不可修
（本测试不试图"修复"它们——那会把隔离区变成假数据产地）。

守卫三层
--------
1. **真实数据行为**：那 1 条判为可回填、4 条判为不可（用隔离区实文件驱动）；
2. **反向防误判**：绝不覆盖已有正文、附件太短不回填、附件是**别的文件**不回填
   （最后这条最关键：把附件清单当正文会污染全文检索与语义切分）；
3. **受控与可追溯**：`body_source` 必须是 `config.enums.BODY_SOURCE` 内的值、
   回填须在 `_metadata` 留痕、且环境变量可整体关闭（回滚开关）。
"""
from __future__ import annotations

import glob
import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from config.enums import BODY_SOURCE
from std_lib.scraper_std.pipeline import (
    _BODY_BACKFILL_MIN_CHARS,
    _backfill_normalize,
    _backfill_title_key,
)


def _would_backfill(rec: dict) -> bool:
    """复现 pipeline 内的三重判据（**与实现同构**，但独立书写以便守卫独立生效）。"""
    if str(rec.get("body_text") or "").strip():
        return False
    att = str(rec.get("attachment_content") or "").strip()
    if len(att) < _BODY_BACKFILL_MIN_CHARS:
        return False
    key = _backfill_title_key(rec.get("title"))
    return bool(key) and key in _backfill_normalize(att)


def _quarantine_records() -> list[dict]:
    files = sorted(glob.glob(os.path.join(
        ROOT, "modules", "regulatory_scrapers", "data", "cleaned",
        "nfra_cleaned_*.quarantine.jsonl")))
    if not files:
        return []
    out = []
    with open(files[-1], encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


class TestRealData:
    def test_exactly_one_backfillable(self) -> None:
        """**真实数据**：5 条里恰有 1 条可回填（附件就是正文的 8727 字那条）。"""
        recs = _quarantine_records()
        if not recs:
            pytest.skip("无隔离区实文件（数据目录未落地）")
        hits = [r for r in recs if _would_backfill(r)]
        assert len(hits) == 1, (
            f"可回填条数应为 1，实际 {len(hits)}："
            f"{[str(r.get('title'))[:30] for r in hits]}")

    def test_backfillable_one_has_body_null_att_rich(self) -> None:
        """**根因守卫**：可回填那条的特征必须是 body 空 + att 富文本 + 含标题。"""
        recs = _quarantine_records()
        if not recs:
            pytest.skip("无隔离区实文件")
        hits = [r for r in recs if _would_backfill(r)]
        if not hits:
            pytest.skip("当前无可用样本")
        r = hits[0]
        assert not str(r.get("body_text") or "").strip()
        assert len(str(r.get("attachment_content") or "")) >= _BODY_BACKFILL_MIN_CHARS
        assert _backfill_title_key(r.get("title")) in _backfill_normalize(
            r.get("attachment_content"))

    def test_non_backfillable_have_no_attachment_text(self) -> None:
        """另 4 条**确实**无附件文本（回填判据不该硬凑样本）。"""
        recs = _quarantine_records()
        if not recs:
            pytest.skip("无隔离区实文件")
        others = [r for r in recs if not _would_backfill(r)]
        for r in others:
            assert len(str(r.get("attachment_content") or "").strip()) \
                < _BODY_BACKFILL_MIN_CHARS, (
                f"被判不可回填但附件够长：{str(r.get('title'))[:40]}")


class TestNoFalsePositive:
    def test_never_overwrites_existing_body(self) -> None:
        """负例：已有正文**绝不覆盖**（回填只补空缺）。"""
        rec = {"title": "某通知", "body_text": "已有正文内容",
               "attachment_content": "某通知" * 200}
        assert _would_backfill(rec) is False

    def test_short_attachment_rejected(self) -> None:
        """负例：附件太短（页眉/水印级）不回填。"""
        rec = {"title": "某通知", "body_text": "",
               "attachment_content": "某通知" * 3}
        assert _would_backfill(rec) is False

    def test_attachment_of_other_document_rejected(self) -> None:
        """**最关键的负例**：附件是**别的文件** ⇒ 不回填。

        若把附件清单/他文当正文，会污染全文检索与语义切分，且**事后难发现**
        ——这比"不回填"代价高得多。
        """
        rec = {"title": "关于甲事项的通知", "body_text": "",
               "attachment_content": "关于乙事项的通知" * 60}
        assert _would_backfill(rec) is False

    def test_empty_title_never_matches(self) -> None:
        """负例：标题为空 ⇒ 无从证明"附件即本文档" ⇒ 不回填。"""
        rec = {"title": "", "body_text": "",
               "attachment_content": "随便一段足够长的文本" * 60}
        assert _would_backfill(rec) is False

    def test_correct_case_positive(self) -> None:
        """正例：三重判据全满足时回填。"""
        rec = {"title": "关于甲事项的通知", "body_text": "",
               "attachment_content": "关于甲事项的通知" * 60}
        assert _would_backfill(rec) is True


class TestControlledAndAuditable:
    def test_body_source_value_is_in_controlled_enum(self) -> None:
        """**受控枚举**：回填用的 `body_source` 必须在 `BODY_SOURCE` 内。

        若用了枚举外的值，`validate_record` 会判 schema 失败 ⇒ 回填反而导致隔离
        （比不回填更糟：数据被吞）。这条钉住该值。
        """
        assert "downloaded_doc" in BODY_SOURCE, sorted(BODY_SOURCE)

    def test_pipeline_marks_audit_fields(self) -> None:
        """**可追溯**：实现必须写 `_metadata.body_backfilled_from` 等留痕字段。"""
        src = open(os.path.join(ROOT, "std_lib", "scraper_std", "pipeline.py"),
                      encoding="utf-8").read()
        for marker in ("body_backfilled_from", "body_backfill_att_chars",
                       "body_backfill_rule", "body_source"):
            assert marker in src, f"pipeline 缺留痕字段 {marker}"

    def test_rollback_switch_exists(self) -> None:
        """**回滚开关**：`REG_ORCH_DISABLE_BODY_BACKFILL=1` 必须能整体关闭。"""
        src = open(os.path.join(ROOT, "std_lib", "scraper_std", "pipeline.py"),
                      encoding="utf-8").read()
        assert "REG_ORCH_DISABLE_BODY_BACKFILL" in src

    def test_backfill_runs_before_schema_validation(self) -> None:
        """**顺序守卫**：回填必须在 schema 校验**之前**。

        若在校验后，`body_text` 空已经导致隔离，回填就无从生效
        （这条是"改了但没起作用"的典型失态）。
        """
        src = open(os.path.join(ROOT, "std_lib", "scraper_std", "pipeline.py"),
                      encoding="utf-8").read()
        i_bf = src.find("_BODY_BACKFILL_MIN_CHARS")
        i_val = src.find("validate_record(rec, UNIFIED_SCHEMA")
        assert i_bf > 0 and i_val > 0, "未找到回填或校验点"
        assert i_bf < i_val, "回填点在校验之后 ⇒ 不会生效"
