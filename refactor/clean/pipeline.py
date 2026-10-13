# -*- coding: utf-8 -*-
"""clean 阶段编排（极简）。

极简 clean = 一条**无状态、源无关**的数据流：

    Load → Adapt → DateNormalize → BodyClean → OcrCorrect → Dedup → Emit

- 编排（CleanPipeline）只按顺序跑 stage 列表，不含任何变换逻辑；
- 每个 stage 是薄封装，真实算法在 core/ 里（cleaner / transform / ocr_correction /
  sentence_split / adapter），可单独单测；
- 没有任何交付闸门 / 隔离 / sidecar / history / 验收 —— 那些不属于"洗脏数据"。

CleanContext 携带一次运行的全部环境（源、配置、路径、指标累加器）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config import CleanSettings, get_clean_settings
from .stages import (
    AdaptStage,
    BodyCleanStage,
    DateNormalizeStage,
    DedupStage,
    EmitStage,
    LoadStage,
    OcrCorrectStage,
)

LOG = logging.getLogger(__name__)


@dataclass
class CleanContext:
    """一次 clean 运行的上下文（被各 stage 读写）。"""

    source: str
    raw_path: str = ""           # 非空则走文件载入；空则从 storage(source) 载入
    settings: CleanSettings = field(default_factory=get_clean_settings)
    captured_at: str = ""
    asset_dir: str = ""          # OCR 资产目录
    log_dir: str = ""            # 存疑样本导出目录
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CleanStage(Protocol):
    """stage 协议：process 接收上一步记录、返回下一步记录（纯变换或 sink 均同签名）。"""

    name: str

    def process(self, records: list[dict], ctx: "CleanContext") -> list[dict]: ...


class CleanPipeline:
    """极简编排器：依次执行 stage，并打印每阶段耗时/条数。"""

    def __init__(self, stages: list[CleanStage]):
        self.stages = stages

    def execute(self, ctx: CleanContext) -> list[dict]:
        records: list[dict] = []
        for stage in self.stages:
            t0 = time.time()
            records = stage.process(records, ctx)
            LOG.info(
                "[%s] 阶段 %-12s 完成  %.2fs  当前 %d 条",
                ctx.source, stage.name, time.time() - t0, len(records),
            )
        return records


def build_pipeline(settings: CleanSettings | None = None) -> CleanPipeline:
    """组装极简 clean 数据流（顺序即语义）。"""
    settings = settings or get_clean_settings()
    stages: list[CleanStage] = [
        LoadStage(),
        AdaptStage(),
        DateNormalizeStage(),
        BodyCleanStage(),
        OcrCorrectStage(),
        DedupStage(),
        EmitStage(),
    ]
    return CleanPipeline(stages)
