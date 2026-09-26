# -*- coding: utf-8 -*-
"""
category_classifier.py —— 效力位阶（category）判定（纯函数）

适配参考脚本《1.3 发文机关特征判断函数》《1.4 效力位阶清洗主函数》
（本地参考目录，未随仓携带），并按《doc_type/category 清洗方案》最终版
（用户确认+更正 2026-08-28 18:47）落地：
  - category **13 级位阶**（用户更正 2）：宪法1 / 法律2 / 司法解释2.5 /
    行政法规3 / 地方性法规4 / 自治条例5 / 部门规章6 / 地方政府规章7 /
    国务院规范性文件8 / 部门规范性文件9 / 地方政府规范性文件10 /
    行业规定11 / 其他12；
  - raw category 存量映射（用户确认 3=A + 更正 3：`法律解释`=`司法解释`）优先，
    机关判定仅映射未命中时触发；supp 用途值由调用方迁 `_raw_fields.用途`。

本模块为纯函数、无 IO，探查层与清洗层共用同一事实源。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

# G2 上收（2026-09-08）：category 13 级位阶/存量映射/修饰提示迁 config.enums 单一事实源，
# 本模块 re-export 保持旧符号可用（R5 同款）。原本地定义已删，禁止回迁副本。
_ORCH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
_STD_LIB_ROOT = os.path.join(_ORCH_ROOT, "std_lib")
if _STD_LIB_ROOT not in sys.path:
    sys.path.insert(0, _STD_LIB_ROOT)
from config.enums import (  # noqa: E402,F401
    ADMIN_REGULATION,
    AUTHORITY_RANK,
    AUTONOMOUS_REGULATION,
    CATEGORY_MAP,
    CATEGORY_MODIFIER_HINTS,
    CATEGORY_SET,
    CONSTITUTION,
    DEPT_NORMATIVE,
    DEPT_RULE,
    INDUSTRY_RULE,
    JUDICIAL_INTERPRETATION,
    LAW,
    LOCAL_GOVERNMENT_NORMATIVE,
    LOCAL_GOVERNMENT_RULE,
    LOCAL_REGULATION,
    OTHER,
    STATE_COUNCIL_NORMATIVE,
)


def analyze_agency(agency: str) -> dict[str, bool]:
    """分析发文机关层级特征（参考脚本 1.3 适配）。"""
    a = agency or ""
    return {
        "is_state_council": bool(re.search(r"国务院", a)),
        "is_state_council_order": bool(re.search(r"^国务院令", a)),
        "is_local_people_congress": bool(re.search(r"(省|市|自治区|自治州|自治县).*人大", a)),
        "is_autonomous": bool(re.search(r"自治", a)),
        "is_central_dept": bool(re.search(r"(部|委员会|总局|总署|署|局|中国人民银行|审计署)", a)),
        "is_local_government": bool(re.search(r"(省|市|自治区|自治州|自治县).*政府", a)),
        "is_ministry_order": bool(
            re.search(r"(教育部|公安部|.*?部|.*?局|.*?署|中国人民银行|审计署)令", a or "")
        ),
        "is_government_order": bool(re.search(r"(省|市|自治区).*?政府令", a or "")),
        "has_local_place": bool(re.search(r"(省|市|县|区|乡|镇)", a)),
    }


def classify_category(
    raw_category: str, title: str = "", agency: str = "", doc_type: str = ""
) -> dict[str, Any]:
    """效力位阶判定入口（参考脚本 1.4 适配 + 13 级 + 司法解释规则）。

    返回：{category, authority_rank, authority_status, verification_source,
           modifier}  —— modifier 为括号修饰文本（如有，供 _raw_fields.公开属性）。
    """
    result: dict[str, Any] = {
        "category": OTHER,
        "authority_rank": AUTHORITY_RANK[OTHER],
        "authority_status": "unknown",
        "verification_source": "",
        "modifier": "",
    }
    rc = (raw_category or "").strip()
    t = (title or "").strip()
    ag = (agency or "").strip()

    # ---- 0) 括号修饰变体剥离（supp 部门规范性文件（内部便函）等） ----
    modifier = ""
    if rc:
        m = re.search(r"（([^（）]*(?:内部便函|内部文件|内部备案)[^（）]*)）", rc)
        if m:
            modifier = m.group(1)
            rc = rc.replace(m.group(0), "").strip()
    result["modifier"] = modifier

    # ---- 1) 存量映射优先（用户确认 3=A） ----
    if rc in CATEGORY_MAP:
        cat = CATEGORY_MAP[rc]
        result.update(
            {
                "category": cat,
                "authority_rank": AUTHORITY_RANK[cat],
                "authority_status": "success",
                "verification_source": f"raw category 存量映射: {rc}",
            }
        )
        return result

    # ---- 2) 机关判定（映射未命中时，参考 1.4 规则 1-11 + 司法解释） ----
    ai = analyze_agency(ag)
    if "宪法修正案" in t or rc == "宪法":
        return _mk(CONSTITUTION, "success", "标题含‘宪法修正案’或 raw category=宪法")
    if doc_type == "法律" or re.search(r"《中华人民共和国.*法》", t):
        return _mk(LAW, "success", "类型标识为‘法/法律’或标题为《中华人民共和国*法》")
    if re.search(r"^国务院令", t):
        return _mk(ADMIN_REGULATION, "success", "以国务院令形式发布")
    if ai["is_state_council"] and doc_type in ("条例", "规定", "办法", "细则", "规则"):
        return _mk(ADMIN_REGULATION, "success", "国务院发布且类型为法规类型")
    if "最高人民法院" in ag or "最高人民检察院" in ag:
        return _mk(
            JUDICIAL_INTERPRETATION, "success", "最高人民法院/最高人民检察院（司法解释规则）"
        )
    if ai["is_local_people_congress"] and doc_type == "条例":
        return _mk(LOCAL_REGULATION, "success", "地方人大发布，类型为条例")
    if ai["is_autonomous"] and doc_type == "条例":
        return _mk(AUTONOMOUS_REGULATION, "success", "自治机关发布，类型为条例")
    if ai["is_ministry_order"]:
        return _mk(DEPT_RULE, "success", "以部委令形式发布")
    if (
        ai["is_central_dept"]
        and not ai["has_local_place"]
        and doc_type in ("规定", "办法", "细则", "规则")
    ):
        return _mk(DEPT_RULE, "success", "中央部门发布，类型为法规类型")
    if ai["is_government_order"]:
        return _mk(LOCAL_GOVERNMENT_RULE, "success", "以地方政府令形式发布")
    if ai["is_local_government"] and doc_type in ("规定", "办法"):
        return _mk(LOCAL_GOVERNMENT_RULE, "success", "地方政府发布，类型为规定/办法")

    # ---- 3) 法定文种 → 规范性文件按机关细分（参考 1.4 规则 8-10） ----
    from scraper_std.doc_type_cleaner import LEGAL_DOC_TYPES

    if doc_type in LEGAL_DOC_TYPES or doc_type in ("命令", "法律"):
        if ai["is_state_council"]:
            return _mk(STATE_COUNCIL_NORMATIVE, "estimated", "国务院发布，法定文种")
        if ai["is_central_dept"] and not ai["has_local_place"]:
            return _mk(DEPT_NORMATIVE, "estimated", "中央部门发布，法定文种")
        if ai["is_local_government"] or ai["has_local_place"]:
            return _mk(LOCAL_GOVERNMENT_NORMATIVE, "estimated", "地方政府发布，法定文种")
        # 无法确定机关层级（本项目为中央监管语境，归 other 而非地方政府——适配差异④）
        return _mk(OTHER, "estimated", "法定文种但无法确定机关层级")

    # ---- 4) 兜底 ----
    if not t and not ag:
        result["authority_status"] = "insufficient_info"
        result["verification_source"] = "缺少标题与发文机关信息"
        return result
    result["verification_source"] = "无法归入任何效力层级"
    return result


def _mk(cat: str, status: str, source: str) -> dict[str, Any]:
    return {
        "category": cat,
        "authority_rank": AUTHORITY_RANK[cat],
        "authority_status": status,
        "verification_source": source,
        "modifier": "",
    }


if __name__ == "__main__":
    cases = [
        # (raw_category, title, agency, doc_type, 期望)
        ("法律", "中华人民共和国反洗钱法", "全国人民代表大会常务委员会", "法律", LAW),
        (
            "司法解释",
            "最高人民法院关于适用《中华人民共和国民法典》的解释",
            "最高人民法院",
            "解释",
            JUDICIAL_INTERPRETATION,
        ),
        (
            "法律解释",
            "关于《中华人民共和国刑法》的解释",
            "全国人民代表大会常务委员会",
            "解释",
            JUDICIAL_INTERPRETATION,
        ),  # 更正 3
        ("行政法规", "集成电路布图设计保护条例", "国务院", "条例", ADMIN_REGULATION),
        ("部门规章", "注册会计师行业严重失信主体名单管理办法", "财政部", "办法", DEPT_RULE),
        (
            "地方法规",
            "江苏省地方金融条例",
            "江苏省人民代表大会常务委员会",
            "条例",
            LOCAL_REGULATION,
        ),
        (
            "部门规范性文件（内部便函）",
            "关于落实保险销售行为可回溯管理的通知",
            "中国银保监会",
            "通知",
            DEPT_NORMATIVE,
        ),
        ("行业自律文本", "保险行业自律公约", "中国保险行业协会", "公约", INDUSTRY_RULE),
        ("政策法规(本级)", "某规范性文件", "", "通知", OTHER),
    ]
    for rc, t, ag, dt, expect in cases:
        r = classify_category(rc, t, ag, dt)
        mark = "✓" if r["category"] == expect else "✗"
        print(
            f"  {mark} {rc[:14]:<16} → {r['category']:<28} (期望 {expect}) | {r['verification_source'][:20]}"
            + (f" | 修饰:{r['modifier']}" if r["modifier"] else "")
        )
