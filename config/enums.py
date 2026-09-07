# -*- coding: utf-8 -*-
"""
config/enums.py — 全项目受控枚举唯一事实源（v1 SSOT 表 / N7 落地）

背景：延续《三项目状态码值统一规范》v3（2026-08-28），将原先散布于
std_lib/scraper_std/unified_schema.py 的受控枚举上收为本文件单一来源；
scraper_std/unified_schema.py 迁移后从本文件 re-export（R5），保持旧符号可用。

纪律：
  - 任何模块禁止本地定义枚举字面量；值一律 import 自本文件。
  - 新增枚举值：先登记本文件 + 同步 config/schema 契约，再跑 gate_enum_values。
  - 值风格统一小写英文；历史中文台账仅允许在兼容读侧映射，不写入新数据。
"""
from __future__ import annotations

# ==================== external（监管外部文件） ====================

# F1 timeliness_status（时效状态，7 值）——与 scraper_std/unified_schema 对齐
TIMELINESS_STATUS: frozenset[str] = frozenset({
    "valid",            # 现行有效
    "amended",          # 已修订（原文件仍有效但被修订影响）
    "repealed",         # 已废止
    "partially_repealed",  # 部分废止
    "expired",          # 已失效
    "pending",          # 尚未施行
    "uncertain",        # 不确定（占位 N/A 归并，表示未完成核验）
})
# 中文 → 英文兼容映射（仅读旧台账/旧脚本时使用；新数据禁止写入中文值）
TIMELINESS_CN2EN: dict[str, str] = {
    "已废止": "repealed", "已失效": "expired", "现行有效": "valid",
    "有效": "valid", "修订": "amended", "部分废止": "partially_repealed",
    "待定": "pending", "尚未施行": "pending", "不确定": "uncertain",
    "未核验": "uncertain", "未知": "uncertain",
}

# F3 body_source（正文来源，3 值）
BODY_SOURCE: frozenset[str] = frozenset({"webpage", "downloaded_doc", "both"})

# F4 source（数据源标识，5 值）——注意：新增源须同步 config/sources.yaml 并重跑断言
SOURCE_SET: frozenset[str] = frozenset({"nfra", "pbc", "mof", "gov", "supp"})
# 子源/域名 → 五源标识归并表（v3 3.5）
SOURCE_ALIASES: dict[str, str] = {
    "xzfgk": "gov", "flk": "gov", "gov.cn": "gov", "gov.cn补充": "gov",
    "fgk.mof.gov.cn": "mof", "mof.gov.cn": "mof", "mof": "mof",
    "nfra": "nfra", "pbc": "pbc", "supp": "supp",
}
# supp 补充途径 → 内部通道（v3：source 仅五源，途径迁 _raw_fields）
SUPP_SOURCE_CHANNEL: dict[str, str] = {
    "gov.cn补充": "gov_website", "本地补充": "local_doc",
    "官方发布": "official_publish", "本地非公开": "local_restricted",
}

# ==================== internal（内部制度，D-03：IPN 独立体系） ====================

# 内部制度状态（对齐参考蓝图 ACTIVE/EXPIRING/DEPRECATED 语义，小写统一）
INTERNAL_STATUS: frozenset[str] = frozenset({
    "draft",      # 起草中
    "active",     # 现行有效
    "expiring",   # 即将过期(≤30天)
    "deprecated", # 已废止/被替代
    "archived",   # 已归档
})
# 内部制度文件类型
INTERNAL_FILE_TYPE: frozenset[str] = frozenset({
    "policy",     # 制度/管理办法
    "process",    # 流程/作业指引
    "guideline",  # 操作指引/细则
    "manual",     # 手册
    "other",
})
# 内部制度编号前缀（D-03：与 RFN 空间分离）
IPN_PREFIX = "IPN-"

# ==================== 自检 ====================
def assert_enum_bindings() -> None:
    """枚举常量自检（供 gate_enum_values 调用）。"""
    assert len(TIMELINESS_STATUS) == 7, TIMELINESS_STATUS
    assert len(BODY_SOURCE) == 3, BODY_SOURCE
    assert len(SOURCE_SET) == 5, SOURCE_SET
    assert len(INTERNAL_STATUS) == 5, INTERNAL_STATUS
    assert len(INTERNAL_FILE_TYPE) >= 1


if __name__ == "__main__":  # 离线自检
    assert_enum_bindings()
    print("[config.enums] 自检通过：", sorted(SOURCE_SET))
