# -*- coding: utf-8 -*-
"""interfaces/governance_api — 治理库（governance.db）统一读取契约（阶段 1，2026-09-18）

定位：
    本模块是「三轨制」治理轨的**只读消费面**（见
    `reports/数据流转与存储交互优化方案_20260917.md` §4.1/§4.3）。
    唯一读写在 `std_lib/common_lib/governance_store.py`；本文件只做**归一与再导出**，
    供命令层（`cli.py governance`）、门禁层（`gates/gate_watermark.py`）与后续消费方
    （drafter/报告/审计）统一取数，避免各自拼 SQL。

可读面（全部只读，且治理库未启用时返回空/说明而不抛）：
    status()             治理库概览（表计数 + 水位一致性 + 最近运行）
    watermarks()         产物水位全量
    watermark(key)       单产物水位
    edges()              水位展开的有向边（机器可读的依赖拓扑）
    check()              一致性判据（= gate_watermark 判据）
    audit(...)           审计日志查询
    artifacts(...)       原件注册查询
    gate_results(...)    门禁历史结果

纪律：
    - **写路径不在此暴露**：写入一律经 `governance_store`（表级唯一写方，§4.3 矩阵）。
    - 治理库是旁路观测设施：未启用（库不存在）时应**静默降级为空结果**，不阻断调用链。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # interfaces/
_ROOT = os.path.dirname(_HERE)                                # orchestrator 根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from std_lib.common_lib import governance_store as _gs  # noqa: E402

__all__ = ["enabled", "db_path", "status", "watermarks", "watermark", "edges",
           "check", "audit", "artifacts", "gate_results"]


def enabled() -> bool:
    """治理库是否已启用（库文件存在）。"""
    return _gs.enabled()


def db_path() -> str:
    """治理库路径（供报告/诊断展示）。"""
    return _gs.db_path()


def status() -> dict:
    """治理库概览（= `governance_store.snapshot()`）。"""
    return _gs.snapshot()


def watermarks() -> list[dict]:
    """全部产物水位（含 inputs 解析后的 dict）。"""
    return _gs.list_watermarks()


def watermark(artifact_key: str) -> dict | None:
    """单产物水位；不存在返回 None。"""
    return _gs.get_watermark(artifact_key)


def edges() -> list[dict]:
    """水位展开的依赖边（status ∈ ok/stale/unregistered）。"""
    return _gs.dependency_edges()


def check() -> tuple[bool, dict]:
    """水位一致性判据（与 gate_watermark 同源，单一实现）。"""
    return _gs.check_dependencies()


def audit(limit: int = 200, *, target: str = "", target_key: str = "") -> list[dict]:
    """审计日志（append-only）查询。"""
    return _gs.list_audit(limit=limit, target=target, target_key=target_key)


def artifacts(limit: int = 0, *, kind: str = "") -> list[dict]:
    """原件注册查询（path_keys 已解析为 paths）。"""
    return _gs.list_artifacts(limit=limit, kind=kind)


def gate_results(run_id: str = "", limit: int = 100) -> list[dict]:
    """门禁历史结果（按 run_id 归档）。"""
    return _gs.list_gate_results(run_id=run_id, limit=limit)
