# -*- coding: utf-8 -*-
"""clean 阶段 CLI 入口（极简）。

数据流（与 collectors 的 CollectorPipeline 同构，仅做"洗脏数据"）：
  Load → Adapt → DateNormalize → BodyClean → OcrCorrect → Dedup → Emit(PG/file)

与旧版 run_pipeline 的差异（已收敛为极简）：
  - 移除一切交付闸门/阈值/quarantine/history/sidecar/sanitize/验收 —— 那不是清洗；
  - 输出统一经 collectors.storage.RecordStore 写出（namespace="cleaned"，
    on_conflict="update"），直连 PG（REFACTOR_STORAGE=postgres）或回落 file。

用法：
  cd refactor
  python -m clean.run_clean --project pbc --raw ../raw/pbc_laws.json
  REFACTOR_STORAGE=postgres REFACTOR_DB_URL=postgresql+psycopg://...:5432/db \
      python -m clean.run_clean --project pbc --raw ../raw/pbc_laws.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

# 仓库根（orchestrator/）入路径
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .config import CleanSettings, get_clean_settings
from .pipeline import CleanContext, build_pipeline

LOG = logging.getLogger(__name__)


def run(
    project: str,
    *,
    raw_path: str = "",
    settings: CleanSettings | None = None,
    captured_at: str = "",
    asset_dir: str = "",
    log_dir: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_clean_settings()
    ctx = CleanContext(
        source=project,
        raw_path=raw_path,
        settings=settings,
        captured_at=captured_at,
        asset_dir=asset_dir,
        log_dir=log_dir or os.path.join(_ROOT, "logs"),
    )
    t0 = time.time()
    pipeline = build_pipeline(settings)
    cleaned = pipeline.execute(ctx)
    summary = {
        "project": project,
        "raw_total": ctx.metrics.get("raw_total", 0),
        "cleaned_total": len(cleaned),
        "dedup_removed": ctx.metrics.get("dedup_removed", 0),
        "ocr_corrected_total": ctx.metrics.get("ocr_corrected_total", 0),
        "ocr_uncertain_total": ctx.metrics.get("ocr_uncertain_total", 0),
        "emit_ref": ctx.metrics.get("emit_ref"),
        "emit_count": ctx.metrics.get("emit_count", 0),
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    LOG.info(
        "[%s] 管道完成：raw=%d → cleaned=%d（去重 %d，耗时 %.1fs）",
        project, summary["raw_total"], summary["cleaned_total"],
        summary["dedup_removed"], summary["elapsed_seconds"],
    )
    return summary


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="clean 阶段入口（极简管线）")
    ap.add_argument("--project", required=True, choices=["gov", "mof", "nfra", "pbc"])
    ap.add_argument("--raw", default="", help="原始记录文件路径（缺省从 storage 载入）")
    ap.add_argument("--clean-version", default=None, help="清洗版本标记（默认读 config）")
    ap.add_argument("--captured-at", default="")
    ap.add_argument("--asset-dir", default="", help="OCR 资产目录")
    ap.add_argument("--no-ocr", action="store_true", dest="no_ocr")
    ap.add_argument("--no-sentence", action="store_true", dest="no_sentence")
    ap.add_argument("--no-dedup", action="store_true", dest="no_dedup")
    args = ap.parse_args(argv)

    settings = get_clean_settings()
    if args.clean_version:
        settings.clean_version = args.clean_version
    if args.no_ocr:
        settings.ocr_correction_on = False
    if args.no_sentence:
        settings.sentence_split_on = False
    if args.no_dedup:
        settings.dedup_on = False

    summary = run(
        args.project,
        raw_path=args.raw,
        settings=settings,
        captured_at=args.captured_at,
        asset_dir=args.asset_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
