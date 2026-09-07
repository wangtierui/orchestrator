# -*- coding: utf-8 -*-
"""
doc_type_cleaner.py —— 文件类型标识（doc_type）提取与归一（纯函数）

适配参考脚本《1.1 完整类型标识清单》《1.2 提取函数》（本地参考目录，未随仓携带），
并按《doc_type/category 清洗方案》最终版（用户确认 2026-08-28 18:47）落地：
  - FILE_TYPES 原生三档，**不扩充**（用户更正 1）；
  - 别名归一（用户确认 1=A）：`令→命令`、`法→法律`；
  - 提取失败 → 返回 None（由调用方兜底 pending）；
  - file_type_group 分组（公文/法规类型/其他_分组名）供 _metadata 登记。

本模块为纯函数、无 IO，探查层与清洗层共用同一事实源。
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# 完整类型标识清单（参考脚本 1.1，按匹配优先级排列；原生不扩充）
# --------------------------------------------------------------------------- #
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

LEGAL_DOC_TYPES: set[str] = {
    "决议", "决定", "命令", "令", "公报", "公告", "通告", "意见",
    "通知", "通报", "报告", "请示", "批复", "议案", "函", "纪要",
}
REGULATORY_TYPES: set[str] = {"法", "条例", "规定", "办法", "细则", "规则"}

# 别名归一（v3 确认 1=A：令→命令、法→法律）
DOC_TYPE_ALIAS: dict[str, str] = {
    "令": "命令",
    "法": "法律",
}

# 其他类型分组（参考脚本 1.2 get_other_group）
OTHER_GROUP: dict[str, str] = {
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

_FILE_TYPES_SORTED: list[str] = sorted(FILE_TYPES, key=len, reverse=True)


def _group_of(ft: str) -> str:
    if ft in LEGAL_DOC_TYPES:
        return "公文"
    if ft in REGULATORY_TYPES:
        return "法规类型"
    return f"其他_{OTHER_GROUP.get(ft, '其他未分类')}"


def extract_doc_type(title: str) -> dict[str, Any]:
    """从标题提取文件类型标识（参考脚本 1.2 适配）。

    返回：{doc_type, group, match_pos, status}
      doc_type: 提取的原始标识（未做别名归一），None 表示失败
      group:    公文 / 法规类型 / 其他_分组名
      match_pos: end / middle / not_found / empty_title
      status:   success / not_found / empty_title
    """
    t = (title or "").strip()
    if not t:
        return {"doc_type": None, "group": None, "match_pos": "empty_title", "status": "empty_title"}
    # 策略 0（v3 补丁）：标题以右括号结尾时，先取末尾括号内文本作为真实文种载体
    #   例：中国人民银行令〔2025〕第4号(中国人民银行业务领域网络安全事件报告管理办法)
    #       → 括号内「…报告管理办法」→ 正确提取「办法」（否则末尾是「)」导致误命中「报告」）
    m = re.match(r"^.*?[（(]([^（）()]+)[）)]\s*$", t)
    if m:
        inner = m.group(1).strip()
        for ft in _FILE_TYPES_SORTED:
            if inner.endswith(ft):
                return {"doc_type": ft, "group": _group_of(ft),
                        "match_pos": "end_bracket", "status": "success"}
    for ft in _FILE_TYPES_SORTED:
        if t.endswith(ft):
            return {"doc_type": ft, "group": _group_of(ft), "match_pos": "end", "status": "success"}
    for ft in _FILE_TYPES_SORTED:
        if ft in t:
            return {"doc_type": ft, "group": _group_of(ft), "match_pos": "middle", "status": "success"}
    return {"doc_type": None, "group": "其他未分类", "match_pos": "not_found", "status": "not_found"}


def normalize_doc_type(value: str, title: str = "") -> str:
    """清洗入口：已有值归一 + 别名；无值/非法值按标题提取。

    返回标准值（受控枚举）或 ""（提取失败，由调用方兜底 pending）。
    """
    raw = (value or "").strip()
    if raw and raw not in ("N/A", "-"):
        # 已有值：去别名（令→命令、法→法律），其余受控值原样
        aliased = DOC_TYPE_ALIAS.get(raw, raw)
        if aliased in FILE_TYPES or aliased in ("命令", "法律"):
            return aliased
        # 非受控存量值（如 supp 人为值 便函/国务院文件/行业示范文本）：按标题提取兜底
    r = extract_doc_type(title)
    dt = r["doc_type"]
    if dt is None:
        return ""
    return DOC_TYPE_ALIAS.get(dt, dt)


if __name__ == "__main__":
    # 自检（方案最终版预演断言抽样）
    cases = [
        ("中国银保监会关于印发《商业银行代理销售业务管理办法》的通知", "通知"),
        ("中华人民共和国反洗钱法", "法律"),
        ("国务院关于修改《中华人民共和国外资保险公司管理条例》的决定", "决定"),
        ("企业财务通则", ""),          # 通则不在 FILE_TYPES（不扩充）→ 提取失败
    ]
    for title, expect in cases:
        got = normalize_doc_type("", title)
        mark = "✓" if got == expect else "✗"
        print(f"  {mark} {title[:26]:<28} → {got!r} (期望 {expect!r})")
