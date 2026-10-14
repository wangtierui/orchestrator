# -*- coding: utf-8 -*-
"""modules.reg_frameworks.model — 领域模型（5 实体，方案 §5.2.1）。

设计纪律
--------
· **frozen dataclass**：实体不可变（避免「同一 iid 两套 theme」的静默分叉）；
· **构造即校验**：受控值（`FRAMEWORK_ID`/`REPORTING_CADENCE`/`ITEM_TYPE`/`MAP_METHOD`/
  `MAP_STATUS`）在 `__post_init__` 校验——非法值**导入即失败**，不落库后再被门禁挑出；
· **置信度有界**：`0.0 ≤ confidence ≤ 1.0`，且 `MAP_METHOD` 越「软」默认上限越低
  （semantic ≤ 0.9 —— 语义相似**不得**自动 confirmed，见 `mapping.py` 人工闸门）。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from config.enums import (
    FRAMEWORK_ID,
    ITEM_TYPE,
    MAP_METHOD,
    MAP_STATUS,
    REPORTING_CADENCE,
)


def _mkid(*parts: str) -> str:
    """稳定标识（内容派生，可重复抽取得到同 id ⇒ 幂等 upsert 的前提）。"""
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Framework:
    """监管数据体系（EAST 2.0 / 县域分支 / 保险统计 / 偿付能力 …）。"""

    fid: str
    name: str
    regulator: str
    cadence: str
    deadline_day: int | None = None
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if self.fid not in FRAMEWORK_ID:
            raise ValueError(f"fid={self.fid!r} 不在受控域 FRAMEWORK_ID")
        if self.cadence not in REPORTING_CADENCE:
            raise ValueError(f"cadence={self.cadence!r} 不在受控域 REPORTING_CADENCE")
        if self.deadline_day is not None and not (1 <= int(self.deadline_day) <= 31):
            raise ValueError(f"deadline_day={self.deadline_day} 须 ∈ 1..31")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DataTable:
    """数据表（EAST 112 张量级）——「谁管业务谁管数据」的责任映射在此声明。"""

    tid: str
    fid: str
    name: str
    theme: str
    owner_dept: str = ""
    owner_person: str = ""
    source_systems: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.fid not in FRAMEWORK_ID:
            raise ValueError(f"fid={self.fid!r} 不在受控域")
        if not self.name.strip():
            raise ValueError("DataTable.name 不得为空")
        if not self.theme.strip():
            raise ValueError("DataTable.theme 不得为空（必须挂既有主题体系）")

    @staticmethod
    def mk_id(fid: str, name: str) -> str:
        return _mkid("table", fid, name)

    def to_dict(self) -> dict:
        return {**asdict(self), "source_systems": list(self.source_systems)}


@dataclass(frozen=True)
class DataItem:
    """数据项（EAST 3053 个量级）——条款映射的最小粒度。"""

    iid: str
    tid: str
    name: str
    item_type: str = "statistic"
    definition: str = ""
    validation_rule: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.item_type not in ITEM_TYPE:
            raise ValueError(f"item_type={self.item_type!r} 不在受控域 ITEM_TYPE")
        if not self.name.strip():
            raise ValueError("DataItem.name 不得为空")

    @staticmethod
    def mk_id(tid: str, name: str) -> str:
        return _mkid("item", tid, name)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["keywords"] = list(self.keywords)
        return d


#: 各映射方法的**置信上限**（质量优先：方法越软越不可信；semantic 永不自动 confirmed）。
#: ⚠️ 刻意做**模块级常量**而非 dataclass 字段——字段会被 `asdict()` 写进事实源行
#: （每条映射都带一份_METHOD_CEILING 副本＝数据污染），且字段会污染契约字段计数。
METHOD_CEILING: dict[str, float] = {"explicit": 1.0, "keyword": 0.9, "semantic": 0.75}


@dataclass(frozen=True)
class ClauseItemMap:
    """条款 ↔ 数据项映射（**本模块核心产物**；支持双向追溯）。"""

    map_id: str
    rfn: str
    article_no: str
    iid: str
    method: str
    confidence: float
    evidence: str = ""
    status: str = "review"

    def __post_init__(self) -> None:
        if self.method not in MAP_METHOD:
            raise ValueError(f"method={self.method!r} 不在受控域 MAP_METHOD")
        if self.status not in MAP_STATUS:
            raise ValueError(f"status={self.status!r} 不在受控域 MAP_STATUS")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence={self.confidence} 须 ∈ [0,1]")
        ceiling = METHOD_CEILING.get(self.method, 0.0)
        if float(self.confidence) > ceiling + 1e-9:
            raise ValueError(
                f"method={self.method} 的 confidence={self.confidence} 超过上限 {ceiling}"
                "（软方法不得高置信）"
            )

    @staticmethod
    def mk_id(rfn: str, article_no: str, iid: str) -> str:
        return _mkid("map", rfn, article_no, iid)

    @property
    def needs_review(self) -> bool:
        """是否须人工闸门：非 confirmed，或方法为 semantic（阈值纪律：语义法不自动确认）。"""
        return self.status != "confirmed" or self.method == "semantic"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceMaterial:
    """作证材料登记（与 implementation_audit 共享语义；本模块只登记不裁决）。"""

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

    @staticmethod
    def mk_id(ipn: str, article_no: str, path_or_ref: str) -> str:
        return _mkid("ev", ipn, article_no, path_or_ref)

    def to_dict(self) -> dict:
        return asdict(self)




#: **契约锚点**（模块级元组，供 `contract_manifest` 的 file/symbol/count 三元断言核验；
#: 领域实体字段集合的机器可读形态——类属性 `__dataclass_fields__` 点号断言不认，故显式导出）。
FRAMEWORK_FIELDS: tuple[str, ...] = tuple(Framework.__dataclass_fields__)
DATA_TABLE_FIELDS: tuple[str, ...] = tuple(DataTable.__dataclass_fields__)
DATA_ITEM_FIELDS: tuple[str, ...] = tuple(DataItem.__dataclass_fields__)
CLAUSE_ITEM_MAP_FIELDS: tuple[str, ...] = tuple(ClauseItemMap.__dataclass_fields__)
