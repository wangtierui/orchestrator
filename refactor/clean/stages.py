# -*- coding: utf-8 -*-
"""clean 阶段实现（薄封装 core/ 算法）。

每个 stage 只做一件事，真实逻辑在 core/ 里，便于单独单测。
所有 stage 共用 `process(records, ctx) -> records` 签名：Load 忽略入参、
Emit 写出后原样返回，其余为纯变换。

关键约束：cleaned 产物写库时，storage 的 (namespace, key_md5) 去重键只认
detail_url/url/detail_link/api_link；adapter 输出的是 source_url，故 Emit 在 sink
边界把 source_url 注入为 url，保证 cleaned 表能按文档 URL 去重。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

# 仓库根入路径（orchestrator/），使 collectors 可被导入
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from collectors.storage import get_store  # noqa: E402
from collectors.storage.canonical import canonicalize  # noqa: E402

from .adapter import to_clean  # noqa: E402
from .config import CleanSettings  # noqa: E402
from .core.body import clean_and_repair  # noqa: E402
from .core.cleaner import dedup_records  # noqa: E402
from common.text import normalize_date  # noqa: E402
from .core.load import load_raw_records  # noqa: E402
from .core.ocr_correction import run_ocr_correction  # noqa: E402

LOG = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 入口：载入
# --------------------------------------------------------------------------- #
class LoadStage:
    name = "load"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        if ctx.raw_path:
            # --raw 显式文件：采集侧未走 storage（未套 canonicalize），
            # 此处补齐规范规范记录，使其与 storage 路径（已 canonicalize）同构，消除 P4 漂移。
            data = [canonicalize(ctx.source, r) for r in load_raw_records(ctx.raw_path)]
        else:
            store = get_store(ctx.source)
            data = store.load().items
        ctx.metrics["raw_total"] = len(data)
        LOG.info("[%s] 载入原始记录 %d 条", ctx.source, len(data))
        return data


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
# 变换：日期标准化
# --------------------------------------------------------------------------- #
class DateNormalizeStage:
    name = "date"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        for rec in records:
            for fld in ("publish_date", "effective_date"):
                if rec.get(fld):
                    rec[fld] = normalize_date(rec[fld])
        return records


# --------------------------------------------------------------------------- #
# 变换：正文清洗（去噪/空白/乱码）+ 断句修复（sentence_split_on）
# --------------------------------------------------------------------------- #
class BodyCleanStage:
    name = "body"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        for rec in records:
            clean_and_repair(rec, ctx.settings)
        return records


# --------------------------------------------------------------------------- #
# 变换：OCR 误读校正（四级流程）
# --------------------------------------------------------------------------- #
class OcrCorrectStage:
    name = "ocr"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        out, m = run_ocr_correction(records, ctx.settings, ctx.asset_dir, ctx.log_dir)
        ctx.metrics["ocr_corrected_total"] = m["corrected_total"]
        ctx.metrics["ocr_uncertain_total"] = m["uncertain_total"]
        LOG.info(
            "[%s] OCR 校正：修正 %d 处，存疑 %d 条",
            ctx.source, m["corrected_total"], m["uncertain_total"],
        )
        return out


# --------------------------------------------------------------------------- #
# 变换：同批重复抓取剔除
# --------------------------------------------------------------------------- #
class DedupStage:
    name = "dedup"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        if not ctx.settings.dedup_on:
            return records
        before = len(records)
        out = dedup_records(records, key_fields=["dedup_key"], on_content=True)
        ctx.metrics["dedup_removed"] = before - len(out)
        return out


# --------------------------------------------------------------------------- #
# 出口：写出到存储（PG / file 统一走 RecordStore；update 语义）
# --------------------------------------------------------------------------- #
def _clean_store_normalizer(namespace: str, item: dict) -> dict:
    """sink 边界：保证 cleaned 记录带 storage 去重键字段（source_url → url）。"""
    item = dict(item)
    if not any(item.get(k) for k in ("detail_url", "url", "detail_link", "api_link")):
        src = item.get("source_url")
        if src:
            item["url"] = src
    return item


class EmitStage:
    name = "emit"

    def process(self, records: list[dict], ctx: "Any") -> list[dict]:
        store = get_store(
            "cleaned",
            normalizer=_clean_store_normalizer,
            on_conflict="update",
        )
        ref = store.save(records, captured_at=ctx.captured_at or None)
        ctx.metrics["emit_ref"] = ref
        ctx.metrics["emit_count"] = len(records)
        LOG.info("[%s] 写出 cleaned 记录 %d 条 → %s", ctx.source, len(records), ref)
        return records
