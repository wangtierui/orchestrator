# -*- coding: utf-8 -*-
"""interfaces.implementation_audit_api — 落地执行检视读取契约（W3，方案 §5.3）。

只读消费面（与既有 `interfaces/*_api.py` 同构）；写侧仅透传 `run_gaps`（差距引擎入口，
其内部按既有写口落事实源与 worklist）。
"""
from __future__ import annotations

# 引导纪律：唯一引导点是 bootstrap.py（gate_import_bootstrap 断言基线只减不增）——
# 本模块由链/CLI 经 bootstrap 后调用，故**不自写 sys.path.insert**。
from modules.implementation_audit import implaudit_store as _store

__all__ = [
    "enabled", "records", "evidence", "gaps", "gaps_by_severity", "stats",
    "rules", "ingest_dir", "run_gaps", "render_markdown",
]


def enabled() -> bool:
    """是否已有执行记录（未摄取 → False）。"""
    return bool(_store.list_records())


def records(*, dept: str = "", period: str = "") -> list[dict]:
    """执行记录（可按部门/期间过滤）。"""
    return _store.list_records(dept=dept, period=period)


def evidence(*, ipn: str = "") -> list[dict]:
    """作证材料登记。"""
    return _store.list_evidence(ipn=ipn)


def gaps(*, ipn: str = "", severity: str = "", gap_class: str = "") -> list[dict]:
    """差距清单（三向过滤）。"""
    return _store.list_gaps(ipn=ipn, severity=severity, gap_class=gap_class)


def gaps_by_severity(severity: str) -> list[dict]:
    """按严重度取差距（high/medium/low）。"""
    return _store.list_gaps(severity=severity)


def stats() -> dict:
    """概览（记录/作证/差距/high 数）。"""
    return _store.stats()


def rules() -> list[dict]:
    """差距规则表（**领域资产**，可审阅；求值器与其分离）。"""
    from modules.implementation_audit.engine import load_rules

    return [dict(r) for r in load_rules()]


def ingest_dir(src_dir: str, **kw) -> dict:
    """台账目录 → 执行记录（透传 ingest）。"""
    from modules.implementation_audit import ingest as _ing

    return _ing.ingest_dir(src_dir, **kw)


def run_gaps(**kw) -> dict:
    """跑差距引擎（透传 report；落事实源 + worklist）。"""
    from modules.implementation_audit import implaudit_report as _rep

    return _rep.run_gaps(**kw)


def render_markdown(**kw) -> str:
    """差距报告 markdown。"""
    from modules.implementation_audit import implaudit_report as _rep

    return _rep.render_markdown(**kw)
