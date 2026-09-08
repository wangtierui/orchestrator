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

# 内部制度源文件扩展名（scan 支持集；2026-09-08 收口，避免散落扩展名字面量）
INTERNAL_EXT: frozenset[str] = frozenset({
    "pdf", "doc", "docx", "xlsx", "xls", "xlsm",
})
# 主题对齐方式（internal align_one.method；R18 只增不删）
ALIGN_METHOD: frozenset[str] = frozenset({"title", "body", "unaligned"})
# 桥/merged 引用匹配方式（matched_by）
REF_MATCH_METHOD: frozenset[str] = frozenset({"docno_sig", "title"})
# RFN↔clean 桥 relation 值（R7；self=锚定同实体, refresh=C1已核对, supersede=C2待人工）
BRIDGE_RELATION: frozenset[str] = frozenset({"self", "refresh", "supersede"})

# ==================== doc_type 文种（G1，上收自 scraper_std/doc_type_cleaner，2026-09-08） ====================
# 注意：FILE_TYPES 为 list，顺序承载「按匹配优先级」语义，禁止改序/去重时改变相对优先级。
FILE_TYPES: list[str] = [
    # ===== 第一优先级：法定公文文种（15 种 + 令/函） =====
    "命令", "决定", "决议", "公报", "公告", "通告", "意见",
    "通知", "通报", "报告", "请示", "批复", "议案", "纪要", "令", "函",
    # ===== 第二优先级：规范性文件类型 =====
    "条例", "办法", "细则", "规则", "规定", "法",
    # ===== 第三优先级：其他类型（常见） =====
    "规划", "纲要", "计划", "方案", "要点", "安排", "预案",
    "章程", "制度", "准则", "规范", "守则", "公约", "规程",
    "说明", "解读", "指南", "指引", "问答", "释义",
    "答复", "复函", "意见书", "告知书", "决定书",
    "白皮书", "蓝皮书", "年报", "专报", "信息", "动态", "统计",
    "公示", "证明", "凭证",
    "合同", "协议", "备忘录",
]
# 法定公文文种（doc_type group=公文 用）
LEGAL_DOC_TYPES: frozenset[str] = frozenset({
    "决议", "决定", "命令", "令", "公报", "公告", "通告", "意见",
    "通知", "通报", "报告", "请示", "批复", "议案", "函", "纪要",
})
# 规范性文件类型（doc_type group=法规类型 用）
REGULATORY_TYPES: frozenset[str] = frozenset({"法", "条例", "规定", "办法", "细则", "规则"})
# 别名归一（v3 确认 1=A：令→命令、法→法律）
DOC_TYPE_ALIAS: dict[str, str] = {"令": "命令", "法": "法律"}
# 其他类型分组（doc_type group=其他_分组名）
DOC_TYPE_GROUP: dict[str, str] = {
    "规划": "规划部署", "纲要": "规划部署", "计划": "规划部署",
    "方案": "规划部署", "要点": "规划部署", "安排": "规划部署", "预案": "规划部署",
    "章程": "制度治理", "制度": "制度治理", "准则": "制度治理",
    "规范": "制度治理", "守则": "制度治理", "公约": "制度治理", "规程": "制度治理",
    "说明": "说明解释", "解读": "说明解释", "指南": "说明解释",
    "指引": "说明解释", "问答": "说明解释", "释义": "说明解释",
    "答复": "答复处理", "复函": "答复处理", "意见书": "答复处理",
    "告知书": "答复处理", "决定书": "答复处理",
    "白皮书": "信息数据", "蓝皮书": "信息数据", "年报": "信息数据",
    "专报": "信息数据", "信息": "信息数据", "动态": "信息数据", "统计": "信息数据",
    "公示": "文书凭证", "证明": "文书凭证", "凭证": "文书凭证",
    "合同": "合同协议", "协议": "合同协议", "备忘录": "合同协议",
}

# ==================== category 效力位阶（G2，上收自 scraper_std/category_classifier，2026-09-08） ====================
# 13 级位阶（位阶序：数值型，司法解释用 2.5；category 受控值英文小写）
CONSTITUTION = "constitution"                        # 宪法 1
LAW = "law"                                          # 法律 2
JUDICIAL_INTERPRETATION = "judicial_interpretation"  # 司法解释 2.5
ADMIN_REGULATION = "admin_regulation"                # 行政法规 3
LOCAL_REGULATION = "local_regulation"                # 地方性法规 4
AUTONOMOUS_REGULATION = "autonomous_regulation"      # 自治条例 5
DEPT_RULE = "dept_rule"                              # 部门规章 6
LOCAL_GOVERNMENT_RULE = "local_government_rule"      # 地方政府规章 7
STATE_COUNCIL_NORMATIVE = "state_council_normative"  # 国务院规范性文件 8
DEPT_NORMATIVE = "dept_normative"                    # 部门规范性文件 9
LOCAL_GOVERNMENT_NORMATIVE = "local_government_normative"  # 地方政府规范性文件 10
INDUSTRY_RULE = "industry_rule"                      # 行业规定 11
OTHER = "other"                                      # 其他 12
AUTHORITY_RANK: dict[str, float] = {
    CONSTITUTION: 1, LAW: 2, JUDICIAL_INTERPRETATION: 2.5, ADMIN_REGULATION: 3,
    LOCAL_REGULATION: 4, AUTONOMOUS_REGULATION: 5, DEPT_RULE: 6,
    LOCAL_GOVERNMENT_RULE: 7, STATE_COUNCIL_NORMATIVE: 8, DEPT_NORMATIVE: 9,
    LOCAL_GOVERNMENT_NORMATIVE: 10, INDUSTRY_RULE: 11, OTHER: 12,
}
CATEGORY_SET: frozenset[str] = frozenset(AUTHORITY_RANK)
# raw category 存量映射（用户确认 3=A + 更正 3：法律解释=司法解释）
CATEGORY_MAP: dict[str, str] = {
    "法律": LAW, "国家法律": LAW, "法律法规": LAW, "宪法": CONSTITUTION,
    "修正案": LAW, "法律解释": JUDICIAL_INTERPRETATION,  # 更正 3
    "司法解释": JUDICIAL_INTERPRETATION,
    "行政法规": ADMIN_REGULATION,
    "部门规章": DEPT_RULE, "财政法律法规（财政部规章）": DEPT_RULE,
    "政策规章规范性文件": DEPT_NORMATIVE, "规范性文件": DEPT_NORMATIVE,
    "财政部规范性文件": DEPT_NORMATIVE, "部门规范性文件": DEPT_NORMATIVE,
    "地方法规": LOCAL_REGULATION, "监察法规": LOCAL_REGULATION,
    "行业自律文本": INDUSTRY_RULE,
}
# 括号修饰变体（内部便函/内部文件/内部备案）→ dept_normative + 修饰迁 _raw_fields.公开属性
CATEGORY_MODIFIER_HINTS: tuple[str, ...] = ("内部便函", "内部文件", "内部备案")

# ==================== 自检 ====================
def assert_enum_bindings() -> None:
    """枚举常量自检（供 gate_enum_values 调用）。"""
    assert len(TIMELINESS_STATUS) == 7, TIMELINESS_STATUS
    assert len(BODY_SOURCE) == 3, BODY_SOURCE
    assert len(SOURCE_SET) == 5, SOURCE_SET
    assert len(INTERNAL_STATUS) == 5, INTERNAL_STATUS
    assert len(INTERNAL_FILE_TYPE) >= 1
    assert len(INTERNAL_EXT) == 6, INTERNAL_EXT
    assert len(ALIGN_METHOD) == 3, ALIGN_METHOD
    assert len(REF_MATCH_METHOD) == 2, REF_MATCH_METHOD
    assert len(BRIDGE_RELATION) == 3, BRIDGE_RELATION
    # G1 doc_type：FILE_TYPES 须含全部法定文种/法规类型值且与别名归一闭包一致
    assert len(FILE_TYPES) == 60, len(FILE_TYPES)
    assert LEGAL_DOC_TYPES.issubset(FILE_TYPES), LEGAL_DOC_TYPES - set(FILE_TYPES)
    assert REGULATORY_TYPES.issubset(FILE_TYPES)
    assert all(v in FILE_TYPES or v in ("命令", "法律") for v in DOC_TYPE_ALIAS.values())
    assert set(DOC_TYPE_GROUP).issubset(FILE_TYPES), set(DOC_TYPE_GROUP) - set(FILE_TYPES)
    # G2 category：13 级位阶闭包 + 位阶数值唯一 + 存量映射值域 ∈ CATEGORY_SET
    assert len(AUTHORITY_RANK) == 13, AUTHORITY_RANK
    assert len(set(AUTHORITY_RANK.values())) == 13, "位阶数值重复"
    assert set(CATEGORY_MAP.values()).issubset(CATEGORY_SET)
    assert sorted(CATEGORY_SET) == ["admin_regulation", "autonomous_regulation", "constitution",
                                    "dept_normative", "dept_rule", "industry_rule",
                                    "judicial_interpretation", "law", "local_government_normative",
                                    "local_government_rule", "local_regulation", "other",
                                    "state_council_normative"]


if __name__ == "__main__":  # 离线自检
    assert_enum_bindings()
    print("[config.enums] 自检通过：", sorted(SOURCE_SET))
