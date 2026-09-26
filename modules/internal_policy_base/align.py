# -*- coding: utf-8 -*-
"""
internal_policy_base.align — 内部制度 × 监管主题（T0–T10）对齐（P6 align / R18）

定位：
  - 目标：把内部制度映射到 regulatory_classifier 的监管主题体系（THEME_MAP T0 上位法锚点 + T1–T10），
    使 drafter 能把「制度条款 ↔ 监管文件/RFN」建立关联视图（merged_view，P7）。
  - 无对应主题 → 归 UNALIGNED 桶（R18，不丢失、不进主视图）。
  - 枚举纪律（R18）：只增不删；对齐结果值 ∈ THEME_MAP 键集 ∪ {UNALIGNED}。

方法（启发式 + 可人工覆写）：
  1) 标题关键词命中表（内部制度常见管理对象 → 监管主题）：
     - T1 销售行为与消费者保护：营销员/销售/代理人/消费者/销售误导/佣金
     - T2 产品与精算：产品/精算/费率/保险条款/基本法（利益）
     - T4 公司治理与股权关联交易：公司治理/印章/授权/关联交易/合规管理/授权管理
     - T5 资金运用与资产负债：资金运用/投资/资产负债
     - T6 养老与健康保险专项：养老/健康/年金
     - T7 反洗钱与反恐怖融资：反洗钱/洗钱/恐怖融资
     - T8 机构准入与组织监管：机构/网点/组织/职级
     - T9 风险处置与案件合规：风险/案件/违规/问责/处罚
     - T10 数据治理与信息披露：数据/信息/披露/保密/敏感信息
  2) 标题无命中 → 正文低频词佐证（前 400 字窗口关键词计数取最高）；仍无 → UNALIGNED。
  3) 每文件可多主题（primary + secondary[]）——主主题取命中最强。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_THIS = os.path.dirname(os.path.abspath(__file__))       # modules/internal_policy_base
_MODULES = os.path.dirname(_THIS)
_ORCH_ROOT = os.path.dirname(_MODULES)
# 阶段 3（2026-09-18）：不再插入兄弟模块目录；跨模块经 interfaces。
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)


_DATA = os.path.join(_THIS, "data")
_PROCESSED = os.path.join(_DATA, "processed")
_INDEX_PATH = os.path.join(_DATA, "internal_policy_index.json")
_ALIGN_PATH = os.path.join(_DATA, "align_result.json")
UNALIGNED = "UNALIGNED"

# 主题 → 标题/正文关键词（从 THEME_MAP 语义提炼；不重复定义主题全名）
THEME_TITLE_KW = {
    "T1": ["营销员", "销售", "代理人", "消费者", "销售误导", "佣金", "考勤", "展业", "基本法",
           "录音录像", "双录", "品质管理", "执业登记", "品质", "宣传", "互联网营销", "增员"],
    "T2": ["产品", "精算", "费率", "保险条款", "条款", "定价", "产品闭环"],
    "T4": ["公司治理", "关联交易", "合规", "授权", "印章", "董事", "股权", "治理", "合同", "签约"],
    "T5": ["资金运用", "投资", "资产负债", "资产管理"],
    "T6": ["养老", "健康", "年金", "长期护理"],
    "T7": ["反洗钱", "洗钱", "恐怖融资"],
    "T8": ["机构", "网点", "组织", "职级", "分公司", "营业部"],
    "T9": ["风险", "案件", "违规", "问责", "处罚", "套利", "舞弊", "举报", "档案", "理赔",
           "投诉", "回溯", "回访"],
    "T10": ["数据", "信息", "披露", "保密", "敏感信息", "客户信息", "个人信息", "隐私", "名单"],
}


def _themes_map() -> dict:
    try:
        from interfaces.rfn_api import theme_map  # noqa: PLC0415
        return dict(theme_map())
    except Exception:
        return {}


def _load_index() -> dict:
    with open(_INDEX_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _load_fulltext(ipn: str) -> str:
    p = os.path.join(_PROCESSED, ipn + "_fulltext.json")
    if os.path.exists(p):
        try:
            return (json.load(open(p, encoding="utf-8")) or {}).get("text", "")
        except Exception:
            return ""
    return ""


def score_text(text: str) -> Counter:
    """按主题关键词统计正文命中（前 2000 字窗口，标题语义为主时仅作佐证）。"""
    c: Counter = Counter()
    win = (text or "")[:2000]
    for theme, kws in THEME_TITLE_KW.items():
        for kw in kws:
            if kw in win:
                c[theme] += 1
    return c


def align_one(title: str, text: str) -> dict:
    """单文件对齐 → {primary, secondary[]}。标题命中优先；正文仅标题未命中时用。"""
    hit_title: Counter = Counter()
    for theme, kws in THEME_TITLE_KW.items():
        for kw in kws:
            if kw in (title or ""):
                hit_title[theme] += 1
    if hit_title:
        primary = hit_title.most_common(1)[0][0]
        secondary = [t for t, _ in hit_title.most_common() if t != primary][:3]
        return {"primary": primary, "secondary": secondary, "method": "title"}
    body = score_text(text)
    if body:
        primary = body.most_common(1)[0][0]
        secondary = [t for t, _ in body.most_common() if t != primary][:2]
        return {"primary": primary, "secondary": secondary, "method": "body"}
    return {"primary": UNALIGNED, "secondary": [], "method": "unaligned"}


def align_all() -> dict:
    """全量对齐 → align_result.json。records 增补 primary_theme/secondary_themes。"""
    themes = _themes_map()
    index = _load_index()
    out_records = []
    stat = Counter()
    for r in index.get("records", []):
        text = _load_fulltext(r["ipn"])
        a = align_one(r.get("title", ""), text)
        r = dict(r)
        r["primary_theme"] = a["primary"]
        r["secondary_themes"] = a["secondary"]
        r["align_method"] = a["method"]
        out_records.append(r)
        stat[a["primary"]] += 1
    result = {
        "schema_version": "1.0",
        "themes": themes,
        "unaligned_bucket": UNALIGNED,
        "records": out_records,
        "count": len(out_records),
        "theme_stat": dict(stat),
    }
    _wl_unaligned(out_records)
    os.makedirs(_DATA, exist_ok=True)
    tmp = _ALIGN_PATH + ".tmp"
    json.dump(result, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, _ALIGN_PATH)
    # 同步回主索引（含主题列），供 internal_policy_api/merged_view 消费
    index["records"] = [{k: r.get(k, "") for k in r} for r in out_records]
    json.dump(index, open(_INDEX_PATH + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(_INDEX_PATH + ".tmp", _INDEX_PATH)
    return {"count": len(out_records), "theme_stat": dict(stat), "align_path": _ALIGN_PATH}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    s = align_all()
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


def _wl_unaligned(records) -> None:
    """P2-5（D2，v2 §3.14.3）：无对应监管主题（UNALIGNED）的内部制度登记待办。

    原状：只进 `align_result.json` 的 unaligned_bucket（R18：不进主视图），**无处置入口**。
    现登记进 worklist（处置：补主题关键词或人工指定；确认"确无对应主题"可 dismiss）。
    """
    try:
        from std_lib.common_lib import governance_store as _gs  # noqa: PLC0415
        n = 0
        for r in records:
            if (r.get("primary_theme") or "") != UNALIGNED:
                continue
            ipn = str(r.get("ipn", ""))
            if not ipn:
                continue
            _gs.worklist_add(
                "internal_unaligned", ipn, stage="7.5", artifact_key="internal_align",
                payload={"ipn": ipn, "title": r.get("title", ""), "method": r.get("align_method", "")},
                suggestion="补 `internal_policy_base.align.THEME_TITLE_KW` 关键词或人工指定主题；"
                           "确认确无对应监管主题 → dismiss（保留在 UNALIGNED 桶）")
            n += 1
        if n:
            print(f"[align] 待办：{n} 条 UNALIGNED 已登记 worklist（cli.py worklist resolve）")
    except Exception:  # noqa: BLE001  旁路设施：登记失败不得中断对齐
        pass
