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

**日期归一分层**（F-D06，2026-09-13；与文号同级收敛约定；W4 2026-10 补第三类登记）：
  - 解析/清洗层唯一实现：`scraper_std.cleaner.normalize_date`
    （strptime 多格式 → YYYY-MM-DD[ HH:MM:SS]，无法解析原样保留——保守策略）；
  - 抽取型特化（豁免）：`scraper_std.crawler_common.normalize_date`
    （从长文本正则抽取 → YYYY-MM-DD 或 ""，用于列表页/详情页混排文本）；
  - 字段规范化型特化（豁免）：`collectors.mof_attachments.normalize_date`
    （字段值规范化：list [y,m,d] / '2024-11-03 00:00:00' / None → 'YYYY-MM-DD' 或 None；
    与解析层的差异在 **None 语义**（缺省≠空串）与 list 入参，B3/W4 登记）；
  - 摄取预处理（豁免）：`collectors.supp_ingest_batch._norm_date`（前缀截取 + 年月日抽取）；
  - 比较键（豁免）：`scraper_std.pkulaw_cli._norm_date`（→ (y, m, d) tuple，仅供排序/比对）。
  收敛基线测试：tests/test_ssot_convergence.py（跨实现兼容断言，防再分叉）。
  门禁：`gate_no_duplicate_libs` 对上述 5 符号（公开+私有）执行「非共享库须豁免标记 /
  共享库内多定义须全标记」判定（W4 §7.6.1）——标记即登记，未标记即违规。
"""

from __future__ import annotations

import re
import unicodedata

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


# ===========================================================================
# 关系解析增强层（2026-10-23，第一百一十二批）
# ---------------------------------------------------------------------------
# 为什么需要独立一层：关系抽取面对的文本是**正文引用**（不是清洗后的字段），
# 噪声形态和"字段比对"完全不同——HTML 实体残留、尖括号、各类引号、
# 版本后缀（2022年修订版）、全角数字、文号前的标题残渣。
#
# 为什么**不改** norm_title_strict / norm_docno：那两个是归属表/RFN 索引等
# 「字段精确匹配」面的 SSOT，保真是有意设计；放宽会波及 1000+ 既有行的匹配语义
# （归一化分叉的教训：单侧改动造成新失配）。故本层是**新增的、只用于关系解析**的实现，
# 且调用方必须**索引侧与查询侧同时使用**（只改一侧＝单边失配）。
# ===========================================================================
_HTML_ENT = re.compile(r"&(lt|gt|amp|quot|apos|nbsp);", re.I)
_HTML_MAP = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'", "nbsp": " "}
#: 关系语境噪声：尖括号/书名号外的一切引号与反引号（正文引用常带）
_REL_TITLE_JUNK = re.compile(r"[<>〈〉“”‘’\u2032`]")
#: 版本/版次/稿别后缀：(2022年修订版) （2023版） (试行) （暂行） V2 …
_REL_VERSION = re.compile(
    r"(?:[（(]\s*(?:(?:19|20)\d{2}\s*年?\s*)?"
    r"(?:修订版?|修正版?|版|年修订|年修正|试行|暂行|征求意见稿|草案|送审稿)"
    r"\s*[)）])"
    r"|(?:[-_\s]?[Vv]\d+(?:\.\d+)*)$"
)
#: "令第X号"里的中文序号（国务院令第二百六十二号）
_CN_SEQ = re.compile(r"第([零一二三四五六七八九十百]+)号")
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
#: 文号尾部形态（用于剥掉正文/标题残渣）：`…〔2021〕64号` / `…令第262号`
_DOCNO_TAIL_BRACKET = re.compile(
    r"([^〔\[（(，。；\s]{1,24}[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号?)$")
_DOCNO_TAIL_ORDER = re.compile(r"([^，。；\s]{1,24}令第\d+号)$")


def _cn_seq_to_int(txt: str) -> str:
    """中文序号 → 阿拉伯（仅处理 0~999 的常规写法；无法识别则原样返回）。"""
    if not txt or not re.fullmatch(r"[零一二三四五六七八九十百]+", txt):
        return txt
    section, num, total = 0, 0, 0
    for ch in txt:
        if ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        else:
            num = _CN_DIGIT.get(ch, 0)
    total = section + num
    return str(total) if total else txt


def _halfwidth(s: str) -> str:
    """全角数字/字母 → 半角（NFKC）。**仅用于文号**，不用于中文标题。"""
    return unicodedata.normalize("NFKC", s)


def strip_title_version(title) -> str:
    """剥掉版本/版次/稿别后缀（可叠加，最多 4 轮）。空输入返回空串。"""
    if not title:
        return ""
    t = str(title).strip()
    for _ in range(4):
        prev = t
        t = _REL_VERSION.sub("", t).strip()
        if t == prev:
            break
    return t


def drop_cn_prefix(title: str) -> str:
    """去掉「中华人民共和国」/「中国」前缀（用于"公司法""保险法"这类法定简称）。

    只在**剩余长度 ≥ 2** 时剥（防 `中国` 这类词被剥成空）；无前缀原样返回。
    """
    t = (title or "").strip()
    for p in ("中华人民共和国", "中国"):
        if t.startswith(p) and len(t) > len(p) + 1:
            return t[len(p):]
    return t


def norm_title_rel(title) -> str:
    """关系解析专用标题归一：解 HTML 实体 → 去尖括号/引号 → 去空白 → `norm_title_strict`。

    注意：**不剥版本后缀、不剥国名前缀**——那两步是独立的可组合步骤
    （`strip_title_version` / `drop_cn_prefix`），因为它们在索引侧要**各自建键**
    （精确键、去版本键、去前缀键是三种不同强度的匹配）。
    """
    if not title:
        return ""
    t = _HTML_ENT.sub(lambda m: _HTML_MAP[m.group(1).lower()], str(title)).strip()
    t = _REL_TITLE_JUNK.sub("", t)
    t = re.sub(r"\s+", "", t)
    return norm_title_strict(t)


def norm_docno_strong(docno) -> str:
    """关系解析专用文号归一：解实体 → 全角转半角 → 去空白 → 中文序号转阿拉伯
    → `norm_docno`。

    与 `norm_docno` 的差异（两类，都是正文引用引入、且**判定安全**的形态）：
      ① `财教〔２０２１〕６４号`（全角数字）—— `norm_docno` 不转，会与库内半角值失配；
      ② `中华人民共和国国务院令第二百六十二号`（中文序号）—— 无法与 `…第262号` 对齐。

    **刻意不做"剥离文号前残渣"**（2026-10-23 实测后撤回）：
      正文里确有 `保险机构参与中国金融期货证监会公告〔2020〕12号` 这种"前后夹着正文"的形态，
      但"从哪里截"没有可靠判据 —— 机关代字可长可短（`证监会` / `中国银行保险监督管理委员会`），
      任何按长度或按分隔符的启发式都会**切掉合法机关代字**，把两个不同文号归成一个
      （错配比未匹配更糟）。这类形态由**签名层** `docno_signature`（取"四位年+序号数字尾"）
      处理，它天然不受前缀影响。守卫 `test_no_residue_stripping` 固定这个决定。
    不做括号形态归一（`〔〕[]（）`）——`norm_docno` 已统一去除。
    """
    if not docno:
        return ""
    t = _HTML_ENT.sub(lambda m: _HTML_MAP[m.group(1).lower()], str(docno)).strip()
    t = _halfwidth(t)
    t = re.sub(r"\s+", "", t)
    t = _CN_SEQ.sub(lambda m: "第" + _cn_seq_to_int(m.group(1)) + "号", t)
    return norm_docno(t)


if __name__ == "__main__":  # 离线自检
    assert norm_docno("银保监办发〔2019〕19号") == "银保监办发201919"
    assert norm_title("《保险销售行为管理办法》（试行）") == "保险销售行为管理办法"
    assert (
        norm_title_strict("中国银保监会办公厅关于A、B事项的通知")
        == "中国银保监会办公厅关于A、B事项的通知"
    )
    assert norm_title_strict("《XX办法》（试行）") == "XX办法"
    # 关系解析增强层（2026-10-23）
    assert norm_title_rel("关于印发<阳光保险集团工会经费收支管理规范>的通知") == \
        "关于印发阳光保险集团工会经费收支管理规范的通知"
    # 关系层刻意**不**在 norm_title_rel 里剥版本（版本键是独立一档），需显式组合
    assert strip_title_version(norm_title_rel("阳光人寿实物资产管理办法（2021版）")) == \
        "阳光人寿实物资产管理办法"
    assert strip_title_version("X管理办法(2022年修订版)") == "X管理办法"
    assert strip_title_version("X管理办法（试行）") == "X管理办法"
    assert drop_cn_prefix("中华人民共和国公司法") == "公司法"
    assert drop_cn_prefix("中华人民共和国保险法") == "保险法"
    assert drop_cn_prefix("保险公司股权管理办法") == "保险公司股权管理办法"
    assert norm_docno_strong("阳光人寿发〔２０２３〕２０７ 号") == "阳光人寿发2023207"
    assert norm_docno_strong("银监发[2014]35号") == "银监发201435"
    print("[common_lib.norm] 自检通过")
