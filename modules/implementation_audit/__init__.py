# -*- coding: utf-8 -*-
"""modules.implementation_audit — 落地执行检视（W3，方案 §5.3；用途四）。

为什么有这个模块
--------------
「制度条款是否被真正执行、拿什么作证」此前**完全空白**——材料侧其实已部分具备
（内部制度原文 + 条款级对照素材 `draft_clause`），但缺「执行记录」与「作证材料」的
结构化登记与差距判定。本模块把三方对齐：

    制度条款（draft_clause 产物）× 执行记录（ledgers 解析）× 作证材料
        → Gap（not_executed / partial / no_evidence / insufficient_evidence）
        → worklist(implaudit_gap) + 整改追踪

规则形式化：YAML 规则表（`rules/gap_rules.yaml`，**规则可审阅可版本化**）+ 确定性求值器
（~200 行，纯函数、可全量单测）——不引外部规则引擎（规则集小且 severity 矩阵是领域语义，
通用引擎表达不了；自研可证明正确性，见 §5.3.2）。
"""
from __future__ import annotations

__all__ = ["model", "engine", "ingest", "report"]
__version__ = "0.1.0"
