# -*- coding: utf-8 -*-
"""clean 阶段实现（薄封装 core/ 算法）。

每个 stage 只做一件事，真实逻辑在 core/ 里，便于单独单测。
所有 stage 共用 `process(records, ctx) -> records` 签名：Load 忽略入参、
Emit 写出后原样返回，其余为纯变换。

关键约束：cleaned 产物写库时已对齐采集规范信封（detail_url 等），storage 的
(namespace, key_md5) 去重键直接认 detail_url 计算 key_md5，无需在 sink 再做字段
注入（原则7：去重身份单一契约，与 build_dedup_key 同源）。
"""
from __future__ import annotations

import logging
from typing import Any

# 写侧仍复用 SqlStore 的 upsert；读侧按 DDL 列直读，不经 RawStore 的旧形状还原。
from collectors.storage import get_store  # noqa: E402

from . import db  # noqa: E402
from .adapter import to_clean  # noqa: E402
from .config import CleanSettings  # noqa: E402
from .core.body import clean_and_repair  # noqa: E402
from .core.cleaner import clean_text  # noqa: E402
from common.text import normalize_date  # noqa: E402
from .core.load import load_raw_records  # noqa: E402
from .core.ocr_correction import run_ocr_correction
from .core.attachment_extract import process_attachment  # noqa: E402

LOG = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 入口：载入
# --------------------------------------------------------------------------- #
class LoadStage:
    name = "load"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        if ctx.raw_path:
            # 旁路调试输入：按 PG 契约的统一字段名读取，不做源别名翻译。
            data = load_raw_records(ctx.raw_path)
        else:
            data = db.load_raw(ctx.source)
        ctx.metrics["raw_total"] = len(data)
        LOG.info("[%s] 载入原始记录 %d 条", ctx.source, len(data))
        return data


class AttachmentExtractStage:
    """附件解析（通用层层处理）：标准 PDF→文本层，扫描件→OCR。

    在 load 之后、adapt/清洗之前执行：就地更新 ``rec['attachments']`` 并回写
    raw 副表。文档理解归 clean，不分散到采集源，也不做成独立命令。
    """

    name = "attach"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        enable_ocr = True  # 文档处理层必然尝试 OCR；引擎缺失由 extract_document_text 降级
        for rec in records:
            atts = rec.get("attachments")
            if not isinstance(atts, list) or not atts:
                continue
            did = rec.get("doc_id") or rec.get("id")
            if did is None:
                continue
            changed = False
            for att in atts:
                if not isinstance(att, dict):
                    continue
                if att.get("extract_status") == "ok" and att.get("text"):
                    continue
                updated = process_attachment(att, enable_ocr=enable_ocr)
                if updated is None:
                    continue
                att.clear()
                att.update(updated)
                changed = True
            if changed:
                db.write_attachments(ctx.source, did, atts)
        return records


# --------------------------------------------------------------------------- #
# 适配：规范记录 → clean 统一 schema（源无关单点）
# --------------------------------------------------------------------------- #
class AdaptStage:
    name = "adapt"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        return [
            to_clean(
                r, ctx.source,
                clean_version=ctx.settings.clean_version,
                captured_at=ctx.captured_at,
            )
            for r in records
        ]


# --------------------------------------------------------------------------- #
# 变换：统一文本清洗（阶段二）
# --------------------------------------------------------------------------- #
class UnifiedTextCleanStage:
    """统一文本清洗（阶段二）：对【所有文本字段】跑同一套去噪/去乱码/格式替换/OCR 纠错。

    - 日期：``publish_date`` 归一；
    - 正文 ``content``：``clean_and_repair``（去噪 + 断句修复）；
    - 附件聚合文本 ``attachment_content``：``clean_text``（去噪/去乱码）—— 修掉此前
      只洗 content、漏洗附件文本的覆盖不均问题；
    - OCR 纠错：``content`` + ``attachment_content`` 统一走四级流程（run_ocr_correction
      已扩展同时处理两字段）。
    """

    name = "unified_clean"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        # 1) 日期归一
        for rec in records:
            if rec.get("publish_date"):
                rec["publish_date"] = normalize_date(rec["publish_date"])
        # 2) 正文清洗：content 走 clean_and_repair；attachment_content 走 clean_text
        for rec in records:
            clean_and_repair(rec, ctx.settings)
            att = rec.get("attachment_content") or ""
            if att:
                rec["attachment_content"] = clean_text(att, keep_newlines=True)
        # 3) OCR 误读校正（content + attachment_content）
        out, m = run_ocr_correction(records, ctx.settings, ctx.asset_dir, ctx.log_dir)
        ctx.metrics["ocr_corrected_total"] = m["corrected_total"]
        ctx.metrics["ocr_uncertain_total"] = m["uncertain_total"]
        LOG.info(
            "[%s] 统一文本清洗：OCR 修正 %d 处，存疑 %d 条",
            ctx.source, m["corrected_total"], m["uncertain_total"],
        )
        return out


# --------------------------------------------------------------------------- #
# 出口：写出 cleaned_items（upsert）+ 删除采集侧已不存在的行
# --------------------------------------------------------------------------- #
class EmitStage:
    name = "emit"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        # key_md5 由 detail_url 派生（与 build_dedup_key 同源），无需字段注入。
        store = get_store("cleaned", on_conflict="update")
        store.init_schema()  # 幂等：仅旧 payload 形态时删表重建，之后为 no-op
        ref = store.save(records, captured_at=ctx.captured_at or None)
        # clean ≡ 采集侧：本批没写到的该源旧行（含历史残留）一律删除。
        pruned = db.prune_cleaned(store.engine, ctx.source, db.keys_of(records))
        ctx.metrics["emit_ref"] = ref
        ctx.metrics["emit_count"] = len(records)
        ctx.metrics["pruned"] = pruned
        LOG.info("[%s] 写出 cleaned 记录 %d 条 → %s，删除采集侧已不存在 %d 条",
                 ctx.source, len(records), ref, pruned)
        return records
