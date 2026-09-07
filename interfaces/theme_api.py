# -*- coding: utf-8 -*-
"""
interfaces/theme_api — 监管主题体系唯一访问/写接口（R2：收敛 3 个写入者为单接口）

- 读取：主题映射 THEME_MAP（唯一源 modules/regulatory_classifier/rfn/__init__.py）、按主题查记录。
- 写入：唯一写接口 set_theme(rfn, theme, basis)——merge_final_theme(全量种子)/
  merge_review107(裁定)/rfn.registry 自动登记 全部改走本接口（P4 收敛）。
- 对齐：align_record() 供 internal_policy_base.aligner 打标（D-03：仅对齐，不登记 RFN）。
"""
from __future__ import annotations

# 主题码 → 全名（R16：扩展主题唯一入口；P4 迁入后由 modules/regulatory_classifier/rfn 提供）
THEME_MAP_P0: dict[str, str] = {
    "T0": "T0上位法锚点",
    "T1": "T1销售行为与消费者保护",
    "T2": "T2产品与精算制度",
    "T3": "T3资本与偿付能力监管",
    "T4": "T4公司治理与股权关联交易",
    "T5": "T5资金运用与资产负债管理",
    "T6": "T6养老与健康保险专项",
    "T7": "T7反洗钱与反恐怖融资",
    "T8": "T8机构准入与组织监管",
    "T9": "T9风险处置与案件合规",
    "T10": "T10数据治理与信息披露",
}


class ThemeAPI:
    def get_theme_map(self) -> dict[str, str]:
        return dict(THEME_MAP_P0)

    def set_theme(self, rfn, theme, basis):  # pragma: no cover
        raise NotImplementedError("P4 接入主题归属唯一写接口后实现（R2）")

    def by_theme(self, theme):  # pragma: no cover
        raise NotImplementedError("P4 接入后实现")

    def align_record(self, ipn, theme_hint=None):  # pragma: no cover
        raise NotImplementedError("P6 internal_policy_base.aligner 接入后实现（仅对齐不打标 RFN）")


_api: ThemeAPI | None = None


def get_theme_api() -> ThemeAPI:
    global _api
    if _api is None:
        _api = ThemeAPI()
    return _api
