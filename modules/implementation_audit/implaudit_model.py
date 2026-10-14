# -*- coding: utf-8 -*-
"""modules.implementation_audit.model — 领域模型（3 实体 + Gap，方案 §5.3.1）。

纪律与 `reg_frameworks.model` 同款：frozen dataclass + **构造即校验**（受控值取
`config.enums`：EXEC_STATUS / GAP_CLASS / GAP_SEVERITY / EVIDENCE_KIND）——
非法值导入即失败，不落库后才被门禁挑出。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


def _mkid(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ImplementationRecord:
    """执行记录（台账 ledgers 解析行）：某部门某期间对某条款的执行状态。"""

    rid: str
    dept: str
    period: str
    exec_status: str
    ipn: str | None = None
    article_no: str | None = None
    source_file: str = ""

    def __post_init__(self) -> None:
        from config.enums import EXEC_STATUS

        if self.exec_status not in EXEC_STATUS:
            raise ValueError(f"exec_status={self.exec_status!r} 不在受控域 EXEC_STATUS")
        if not self.dept.strip():
            raise ValueError("ImplementationRecord.dept 不得为空")
        if not self.period.strip():
            raise ValueError("ImplementationRecord.period 不得为空")

    @staticmethod
    def mk_id(dept: str, period: str, ipn: str | None, article_no: str | None) -> str:
        return _mkid("impl", dept, period, ipn or "", article_no or "")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceMaterial:
    """作证材料（报告/日志/截图/系统导出）：存在性 + sha256 + 采集时间。"""

    eid: str
    ipn: str
    article_no: str
    kind: str
    path_or_ref: str
    sha256: str = ""
    collected_at: str = ""

    def __post_init__(self) -> None:
        from config.enums import EVIDENCE_KIND

        if self.kind not in EVIDENCE_KIND:
            raise ValueError(f"kind={self.kind!r} 不在受控域 EVIDENCE_KIND")
        if not self.path_or_ref.strip():
            raise ValueError("EvidenceMaterial.path_or_ref 不得为空")

    @staticmethod
    def mk_id(ipn: str, article_no: str, path_or_ref: str) -> str:
        return _mkid("ev", ipn, article_no, path_or_ref)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Gap:
    """差距（核心产物）：条款要求 ↔ 执行/作证之间的缺口。"""

    gid: str
    ipn: str
    gap_class: str
    severity: str
    article_no: str | None = None
    rfn: str | None = None
    dept: str = ""
    period: str = ""
    detail: str = ""
    worklist_id: str | None = None

    def __post_init__(self) -> None:
        from config.enums import GAP_CLASS, GAP_SEVERITY

        if self.gap_class not in GAP_CLASS:
            raise ValueError(f"gap_class={self.gap_class!r} 不在受控域 GAP_CLASS")
        if self.severity not in GAP_SEVERITY:
            raise ValueError(f"severity={self.severity!r} 不在受控域 GAP_SEVERITY")
        if not self.ipn.strip():
            raise ValueError("Gap.ipn 不得为空")

    @staticmethod
    def mk_id(ipn: str, article_no: str | None, gap_class: str) -> str:
        return _mkid("gap", ipn, article_no or "", gap_class)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ClauseRequirement:
    """条款要求（来自 `draft_clause` 条款对照素材的只读投影）。"""

    ipn: str
    article_no: str
    text: str = ""
    rfn: str | None = None
    requirement_level: str = "required"   # required | recommended

    def __post_init__(self) -> None:
        if self.requirement_level not in ("required", "recommended"):
            raise ValueError(f"requirement_level={self.requirement_level!r} 非法")

    def to_dict(self) -> dict:
        return asdict(self)




#: **契约锚点**（模块级元组；同 reg_frameworks.model 的口径说明）
IMPL_RECORD_FIELDS: tuple[str, ...] = tuple(ImplementationRecord.__dataclass_fields__)
EVIDENCE_FIELDS: tuple[str, ...] = tuple(EvidenceMaterial.__dataclass_fields__)
GAP_FIELDS: tuple[str, ...] = tuple(Gap.__dataclass_fields__)
