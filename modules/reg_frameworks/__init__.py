# -*- coding: utf-8 -*-
"""modules.reg_frameworks — 多监管数据体系建模（W3，方案 §5.2；用途二）。

为什么有这个模块
--------------
`data/inbox/regulatory_stats/` 域（EAST 2.0 等监管统计报送素材）此前**素材就绪但无消费方**
（`inbox_registry.yaml` 的 `consumer: pending_consumer` 长期悬置）——典型的「游离节点」。
本模块把它接入链路：素材 → `DataTable`/`DataItem` 结构化 → 条款↔数据项映射（低置信进
人工闸门）→ 治理库投影。

领域模型（5 实体，全部 frozen dataclass，受控值一律取 `config.enums`）
--------------------------------------------------------------------
`Framework`（体系）→ `DataTable`（数据表）→ `DataItem`（数据项）→ `ClauseItemMap`（映射）
+ `EvidenceMaterial`（作证材料登记，与 implementation_audit 共享语义）。

分层纪律：本模块**只读**既有产物（clauses/relations/cleaned/主题表），不反向依赖
scrapers/classifier 的内部实现（跨模块只经 `interfaces/`）；写入只经本模块 `store.py`
（事实源 JSONL，原子写）。
"""
from __future__ import annotations

__all__ = ["model", "store", "extract", "mapping"]
__version__ = "0.1.0"
