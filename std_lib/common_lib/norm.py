# -*- coding: utf-8 -*-
"""
std_lib.common_lib.norm — 归一化唯一实现（专项三；A-10 分层收敛 2026-09-12）

**归一化 SSOT 分层**（A-10 副本清零的唯一调用面，禁止各处再实现）：

  | 函数                | 语义层 | 用于 |
  |---|---|---|
  | norm_docno          | 标准文号 | 唯一键/匹配比对（去括号空白 + 去尾"号" + 空占位→""）|
  | norm_title          | 激进标题 | 去噪全文匹配（去尾注 + 去常见标点 + lower，ASCII 兼容）|
  | norm_title_strict   | 保守标题 | 归属表/索引**精确匹配**（仅去尾注 + 去《》引号空白；**不**去标点/不 lower）|

  特化豁免（显式 `norm-specialization` 标记，门禁识别；少数有真实语义差异的例外）：
  - rfn.registry._norm_title（RFN 事实源内部，防索引漂移）；
  - reconcile_clean_drift._norm_docno（"第/年/号"字宽容预处理）；
  - apply/consolidate 的修复型文号（normalize_doc_number 残渣剥离 + 本层归一）；
  - scraper_std.pkulaw_cli._norm_title（法宝库标题比较专用）；
  - scraper_std.pkulaw_cli.norm_docno（F-D06 2026-09-13 登记：法宝库文号比较专用形态——
    全角→半角 + 〔〕→[] 后去空白，与该库返回值对齐比较用，不产出本层标准文号）；
  - recall_audit.scanner / build_outputs._norm_docno（F-D06 2026-09-13 登记：空占位判定型——
    仅判空/NULL 占位，非全量归一；用于行键比对，保持原语义防 recall 结果漂移）。

**日期归一分层**（F-D06，2026-09-13；与文号同级收敛约定）：
  - 解析/清洗层唯一实现：`scraper_std.cleaner.normalize_date`
    （strptime 多格式 → YYYY-MM-DD[ HH:MM:SS]，无法解析原样保留——保守策略）；
  - 抽取型特化（豁免）：`scraper_std.crawler_common.normalize_date`
    （从长文本正则抽取 → YYYY-MM-DD 或 ""，用于列表页/详情页混排文本）；
  - 摄取预处理（豁免）：`collectors.supp_ingest_batch._norm_date`（前缀截取 + 年月日抽取）；
  - 比较键（豁免）：`scraper_std.pkulaw_cli._norm_date`（→ (y, m, d) tuple，仅供排序/比对）。
  收敛基线测试：tests/test_ssot_convergence.py（跨实现兼容断言，防再分叉）。
"""
from __future__ import annotations

import re

_NULL_TOKENS = {"", "n/a", "na", "无", "-", "none", "null", "未注明", "不详"}
# ASCII null 占位大小写不敏感判定（N/A、Null、NONE 等）
_ASCII_NULL_LOWER = {"n/a", "na", "none", "null", "n.a."}
_DOC_CLEAN = re.compile(r"[〔\[\]（）()〕\s]")
_TITLE_SUFFIX = re.compile(r"[（(](已废止|已失效|试行|修订)[）)]\s*$")
_TITLE_PUNCT = re.compile(r'[\s（）()、《》"\'，。：:；;,.!！?？、_/-]')
# 保守层：仅去《》与引号字符（保留其余标点、不 lower）——与 rfn.__init__ 原语义一致
_TITLE_STRICT_CLEAN = re.compile(r'[《》"\u201c\u201d\s]')


def norm_docno(docno) -> str:
    """文号归一（标准层）：去除括号/空白 → 返回纯串（去掉尾部'号'）。空/N/A 类占位返回空串。"""
    if not docno:
        return ""
    s = str(docno).strip()
    if s in _NULL_TOKENS:
        return ""
    if s.lower() in _ASCII_NULL_LOWER:
        return ""
    s = _DOC_CLEAN.sub("", s)
    return s.rstrip("号")


def norm_title(title) -> str:
    """标题归一（激进层）：去括号尾注/书名号/空白/常见标点，转小写（ASCII）。"""
    if not title:
        return ""
    t = str(title).strip()
    if t in _NULL_TOKENS:
        return ""
    t = _TITLE_SUFFIX.sub("", t)
    t = _TITLE_PUNCT.sub("", t)
    return t.lower()


def norm_title_strict(title) -> str:
    """标题归一（保守层）：仅去尾注（已废止|已失效|试行|修订）+ 去《》与引号/空白。

    —— 归属表/RFN 索引等**精确匹配**场景（标点与大小写保真，避免过度归并引发误配；
    与 rfn.__init__._norm_title / merged / reconcile / build_detail_tables / 门禁同语义）。
    """
    if not title:
        return ""
    t = str(title).strip()
    if t in _NULL_TOKENS:
        return ""
    t = _TITLE_SUFFIX.sub("", t)
    return _TITLE_STRICT_CLEAN.sub("", t)


if __name__ == "__main__":  # 离线自检
    assert norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
    assert norm_title("《保险销售行为管理办法》（试行）") == "保险销售行为管理办法"
    assert norm_title_strict("中国银保监会办公厅关于A、B事项的通知") == "中国银保监会办公厅关于A、B事项的通知"
    assert norm_title_strict("《XX办法》（试行）") == "XX办法"
    print("[common_lib.norm] 自检通过")
