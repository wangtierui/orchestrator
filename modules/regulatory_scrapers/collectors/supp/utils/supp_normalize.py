# -*- coding: utf-8 -*-
"""
supp_normalize.py —— 补充法规库字段一致化规则（supplementary_regulations_scraper）

定位：在统一清洗管道「cleaner 注入 N/A」之前，对原始记录做字段级规范化，
      消除 supplementary_regulations_scraper 数据中长期存在的 7 类不一致问题：

  I3 文号写入规则不一致      → doc_no 无正式文号统一为 `（无正式文号[: 原因]）` 形态
  I4 文号写入错误（法律/指导意见）→ 法律补「主席令」；无文号文件明确标记，杜绝 N/A
  I5 doc_type/category 大量 N/A → 缺失时按「标题文种 + 发布机构」推导（高置信启发式）
  I6 source 枚举值不统一     → 收敛为受控词表 {gov.cn补充 / 官方发布 / 本地补充 / 本地非公开}
  I7 status/timeliness 双列  → status 统一中文化（现行有效…），timeliness 保留英文枚举

设计原则（对齐用户数据治理红线）：
  * 事实性字段（真实文号、URL、正文）一律以 raw JSON 权威源为准，本模块不臆造；
  * 推导型字段（doc_type/category）仅做「标题文种/发布机构」高置信映射，
    并在 _raw_fields 标注「推导自…」以便审计与回滚；
  * 不修改共享库 std_lib（保持四项目漂移 0），本模块仅服务于 supp 项目。

供 scripts/run_clean_pipeline.py 的 map_supp 与离线修复脚本 scripts/repair_supp_cleaned.py
复用，保证「重跑管道」与「外科手术式修复」结果一致。
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import re
from typing import Any

# ---------------------------------------------------------------------------
# I6 source 受控词表：将历史冗长/自由文本枚举收敛为 4 个规范值
# ---------------------------------------------------------------------------
SOURCE_CANON = {
    "gov.cn补充": "gov.cn补充",
    "官方发布": "官方发布",
    "本地补充": "本地补充",
    "本地非公开": "本地非公开",
    # —— 历史冗长写法 → 规范值 ——
    "nfra.gov.cn官方归档（正文经北大法宝获取，官方页面标题核验一致）": "gov.cn补充",
    "nfra.gov.cn官方归档（正文经官方页面全文核验一致）": "gov.cn补充",
    "本地非公开（公司产品备案系统）": "本地非公开",
    "本地非公开（行业示范文本）": "本地非公开",
}

# ---------------------------------------------------------------------------
# I7 status 英文化受控词表（规范 v3 3.3：status 由 timeliness_status 派生，
# 数据层统一英文枚举，中文标签仅展示层转换）
# ---------------------------------------------------------------------------
STATUS_CANON_EN = {
    "valid": "valid",
    "现行有效": "valid",
    "有效": "valid",
    "已废止": "repealed",
    "废止": "repealed",
    "repealed": "repealed",
    "已修订": "amended",
    "修订": "amended",
    "amended": "amended",
    "失效": "expired",
    "expired": "expired",
    "已失效": "expired",
    "待生效": "pending",
    "pending": "pending",
    "尚未施行": "pending",
    "不确定": "uncertain",
    "uncertain": "uncertain",
    "部分废止或失效": "partially_repealed",
    "partially_repealed": "partially_repealed",
}

# ---------------------------------------------------------------------------
# I5 doc_type 推导：按标题文种关键词优先级匹配（先具体后泛化）
# 顺序即优先级；命中第一个即返回，避免「通知」吞掉「办法/规定」等。
# ---------------------------------------------------------------------------
_DOC_TYPE_RULES = [
    ("主席令", "法律"),
    ("法律", "法律"),          # 标题形如《中华人民共和国XX法》
    ("示范文本", "行业示范文本"),
    ("令", "部门规章"),         # X令（保监会令/委员会令/人民银行令/发改委令）
    ("办法", "办法"),
    ("规定", "规定"),
    ("批复", "批复"),
    ("复函", "复函"),
    ("公告", "公告"),
    ("指引", "指引"),
    ("标准", "标准"),
    ("意见", "意见"),
    ("通知", "通知"),          # 兜底（绝大多数监管文件为通知）
]

# category 推导：依据 doc_type + 发布机构
_CATEGORY_BY_DOC_TYPE = {
    "法律": "上位法",
    "部门规章": "部门规章",
    "行业示范文本": "行业自律文本",
    "办法": "部门规范性文件",
    "规定": "部门规范性文件",
    "批复": "部门规范性文件",
    "复函": "部门规范性文件",
    "公告": "部门规范性文件",
    "指引": "部门规范性文件",
    "标准": "部门规范性文件",
    "意见": "部门规范性文件",
    "通知": "部门规范性文件",
}

# 发布机构 → category 覆盖（更高优先级，处理国务院/行业协会特例）
_CATEGORY_BY_ORGAN = {
    "国务院办公厅": "制定依据",
    "国务院": "制定依据",
    "中国保险行业协会": "行业自律文本",
    "中国银行业协会": "行业自律文本",
    "行业协会": "行业自律文本",
}

def _blank(v: Any) -> bool:
    return v is None or str(v).strip() in ("", "N/A", "nan", "None")

# ---------------------------------------------------------------------------
# 错行修复（CSV 行对齐）：消除字段值内部的换行/回车符
#
# 根因：body_text / summary 等文本字段在抓取阶段保留了原始正文的 \n / \r\n，
#       清洗后写入 CSV 时被 csv 模块合法地包裹在引号内，但字段内部仍含裸 \n。
#       记录分隔符用 \r\n（57 处 = 表头+56 行），而字段内部另有 ~10070 处裸 \n。
#       任何按 \n 切分的读取器（Excel 多种配置、grep/diff 类 QC、LF 模式 pandas、
#       纯文本编辑器）会把字段内的 \n 误判为行分隔，表现为「错行/上万行」。
#
# 修复：在 cleaner 注入 N/A 之前，将所有字符串字段值内部的 \r\n / \r / \n
#       归一为单个空格并压缩连续空白。CSV 即退化为严格的「一行一记录」
#       （仅 57 个 \r\n 记录分隔符），彻底消除错行；JSONL 同为字段级归一，
#       正文结构由 raw JSON / 落盘 PDF 完整保留，不损失事实性内容。
# ---------------------------------------------------------------------------
def _flatten_text(v: Any) -> Any:
    if isinstance(v, str):
        # 统一换行符为 \n，再整体替换为空格，最后压缩连续空白
        normalized = v.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized
    if isinstance(v, dict):
        return {k: _flatten_text(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_flatten_text(x) for x in v)
    return v

def normalize_doc_no(rec: dict[str, Any]) -> str:
    """I3/I4：文号一致化。

    * 真实文号（含 主席令）→ 原样保留（事实以 raw 为准，此处不臆造）；
    * 无正式文号的历史写法 `YYYY年（…）` / `（公布编号）` 等 → 收敛为
      `（无正式文号[: 原因]）` 单一形态；
    * 空文号（法律/指导意见确无文号）→ `（无发文字号）`，杜绝 N/A 占位。
    """
    raw = str(rec.get("document_number") or "").strip()
    title = str(rec.get("title") or "")
    if not raw or raw == "N/A":
        # 法律类必有主席令（已在 raw 修正）；其余无文号文件明确标记
        if "法" in title and ("主席令" in title or title.startswith("中华人民共和国")):
            return "（无发文字号）"  # 兜底，正常不应触发（raw 应已补主席令）
        return "（无发文字号）"

    # 已是规范文号（含 〔〕 或 主席令 或 数字+号 如 11号/265号/第5号）→ 保留
    # 注意：须用「数字+号」判定，避免误吞「无正式文号」「公司内部备案号」中的「号」
    if "〔" in raw or "令" in raw or re.search(r"\d号", raw):
        # 仅清洗首尾空白/全角括号
        return raw.replace("（", "(").replace("）", ")").strip()

    # `YYYY年（无正式文号）` / `YYYY年（公司内部备案号）` 等 → 收敛
    m = re.match(r"^(\d{4})年[（(](.*?)[）)]\s*$", raw)
    if m:
        reason = m.group(2).strip()
        if "无正式文号" in reason or not reason:
            return "（无正式文号）"
        return f"（无正式文号：{reason}）"
    if "无正式文号" in raw or "公司内部备案" in raw or "公布编号" in raw:
        reason = raw
        for pat in (r"^\d{4}年", r"[（(]", r"[）)]"):
            reason = re.sub(pat, "", reason).strip(" ：:")
        if "无正式文号" in reason or not reason:
            return "（无正式文号）"
        return f"（无正式文号：{reason}）"
    return raw.strip()

def derive_doc_type(rec: dict[str, Any]) -> str:
    """I5：doc_type 缺失时按标题文种推导（高置信）。"""
    raw = str(rec.get("doc_type") or "").strip()
    if raw and raw != "N/A":
        return raw
    title = str(rec.get("title") or "")
    for kw, dt in _DOC_TYPE_RULES:
        if kw in title:
            return dt
    return "规范性文件"  # 兜底

def derive_category(rec: dict[str, Any], doc_type: str) -> str:
    """I5：category 缺失时按 doc_type + 发布机构推导（高置信）。"""
    raw = str(rec.get("category") or "").strip()
    if raw and raw != "N/A":
        return raw
    organ = str(rec.get("issue_organ") or "")
    for key, cat in _CATEGORY_BY_ORGAN.items():
        if key in organ:
            return cat
    return _CATEGORY_BY_DOC_TYPE.get(doc_type, "部门规范性文件")

def normalize_source(rec: dict[str, Any]) -> str:
    """I6：source 收敛为受控词表 4 值；原冗长写法移入 _raw_fields.来源详情。"""
    raw = str(rec.get("source") or "").strip()
    if not raw or raw == "N/A":
        return "gov.cn补充"
    canon = SOURCE_CANON.get(raw, raw)
    if canon not in SOURCE_CANON:
        # 未知值：尽量按关键词归类，避免引入新枚举
        if "nfra" in raw or "gov.cn" in raw:
            canon = "gov.cn补充"
        elif "非公开" in raw or "内部" in raw:
            canon = "本地非公开"
        elif "本地" in raw:
            canon = "本地补充"
        elif "官方" in raw:
            canon = "官方发布"
        else:
            canon = "gov.cn补充"
    return canon

def normalize_status(rec: dict[str, Any]) -> str:
    """I7：status 英文化（规范 v3 3.3：与 timeliness_status 对齐，中文仅展示层）。"""
    raw = str(rec.get("status") or "").strip()
    if not raw or raw == "N/A":
        # 优先与 timeliness_status 对齐，缺失默认 valid
        return str(rec.get("timeliness_status") or "valid").strip() or "valid"
    return STATUS_CANON_EN.get(raw, raw)

def normalize_raw(rec: dict[str, Any]) -> dict[str, Any]:
    """对单条原始记录做完整字段一致化，返回（可能新增 _raw_fields 标注的）新 dict。

    幂等：已规范的值保持不变；仅对缺失/非规范值做推导，并标注推导来源。
    """
    out = dict(rec)
    raw_fields = dict(rec.get("_raw_fields") or {})

    # —— doc_no（I3/I4）——
    new_doc_no = normalize_doc_no(out)
    if new_doc_no != str(out.get("document_number") or "").strip():
        if str(out.get("document_number") or "").strip() in ("", "N/A"):
            raw_fields.setdefault("文号推导", "无正式文号 → 标记（无发文字号）")
        out["document_number"] = new_doc_no

    # —— doc_type（I5）——
    new_dt = derive_doc_type(out)
    if not str(out.get("doc_type") or "").strip() or str(out.get("doc_type") or "").strip() == "N/A":
        if new_dt != "规范性文件":
            raw_fields.setdefault("doc_type推导", f"按标题文种推导 → {new_dt}")
        out["doc_type"] = new_dt

    # —— category（I5）——
    new_cat = derive_category(out, new_dt)
    if not str(out.get("category") or "").strip() or str(out.get("category") or "").strip() == "N/A":
        raw_fields.setdefault("category推导", f"按 doc_type[{new_dt}]+发布机构推导 → {new_cat}")
        out["category"] = new_cat

    # —— source（I6）——
    old_src = str(out.get("source") or "").strip()
    new_src = normalize_source(out)
    if new_src != old_src and old_src:
        raw_fields.setdefault("来源详情", old_src)  # 保留原始冗长写法溯源
    out["source"] = new_src

    # —— status（I7）——
    new_st = normalize_status(out)
    if new_st != str(out.get("status") or "").strip():
        raw_fields.setdefault("status推导", f"{str(out.get('status') or '').strip() or '空'} → {new_st}")
    out["status"] = new_st

    # —— 错行修复：消除所有字段值内部的换行/回车（最后一道，覆盖全部字符串列）——
    for k in list(out.keys()):
        out[k] = _flatten_text(out[k])
    if raw_fields:
        out["_raw_fields"] = _flatten_text(raw_fields)

    return out

if __name__ == "__main__":  # 离线自检
    samples = [
        {"title": "中国银保监会办公厅关于推广人身保险电子化回访工作的通知",
         "document_number": "银保监办发〔2020〕11号", "source": "gov.cn补充",
         "doc_type": "通知", "category": "部门规范性文件", "status": "现行有效"},
        {"title": "关于银保产品管理有关事宜的通知", "document_number": "2023年（无正式文号）",
         "source": "本地补充", "status": "valid"},
        {"title": "关于规范银行代理渠道保险产品的通知", "document_number": "2023年（公司内部备案号）",
         "source": "本地非公开（公司产品备案系统）"},
        {"title": "中华人民共和国个人信息保护法", "document_number": "中华人民共和国主席令第七十二号",
         "source": "gov.cn补充", "doc_type": "法律", "category": "上位法"},
        {"title": "关于推动中长期资金入市的指导意见", "document_number": "",
         "source": "gov.cn补充"},
        {"title": "中国保险行业协会财产再保险比例及非比例合同范本（中文版）",
         "document_number": "中保协（行业示范文本）", "source": "本地非公开（行业示范文本）",
         "doc_type": "行业示范文本", "category": "行业自律文本"},
        {"title": "保险资产管理产品管理暂行办法", "document_number": "中国银行保险监督管理委员会令（2020年第5号）",
         "source": "nfra.gov.cn官方归档（正文经官方页面全文核验一致）"},
    ]
    for s in samples:
        o = normalize_raw(s)
        print(f"  {o['title'][:24]:26} | doc_no={o['document_number']!r:22} src={o['source']!r:10} "
              f"dt={o['doc_type']!r:12} cat={o['category']!r:16} st={o['status']!r}")
    print("[supp_normalize] 离线自检通过")
