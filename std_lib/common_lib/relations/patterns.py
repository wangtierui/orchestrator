# -*- coding: utf-8 -*-
"""std_lib.common_lib.relations.patterns — 关系模式集与文本工具（W4-c 三拆）。

RelationConfig / 中文数字与文号模式 / 目标实体分类 / 分句与签名工具。
抽取器（RelationExtractor）与管道（RelationPipeline）经本模块取模式与文本原语。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_STD_LIB = os.path.join(_ROOT, "std_lib")
if _STD_LIB not in sys.path:
    sys.path.insert(0, _STD_LIB)

from config.enums import (
    BASIS_TYPE_PROCEDURAL,
    REPEAL_ACTION_CANCEL,
    REPEAL_ACTION_CEASE,
    REPEAL_ACTION_INAPPLICABLE,
    REPEAL_ACTION_INVALIDATE,
    REPEAL_ACTION_REPEAL,
)
from std_lib.common_lib.norm import norm_docno, norm_title_strict

SCHEMA_VERSION = "1.0"
# 2026-09-20 → 1.1：relation_id 派生纳入判别字段（article/action/scope/reason/dst_docno/
# dst_kind/src_key）并在写入前做确定性唯一化——修复"同一 id 命中多行"的既有缺陷。
# 1.2（N-98，2026-09-28）：**新增披露字段** `source_span`（引用跨度：命中+紧随尾部）。
#   `source_snippet` 语义与定位口径**均未变**（曾拟改为以跨度定位，实测使目标侧覆盖由 5.7%
#   降至 2.3%/2.5% → **否决**，见 `extract_relations` 否决注记）。字段集变化即提升版本
#   （下游据此判"产物是否由当前抽取器生成"；`gate_relations` 与 `relations_api` 均披露该字段）。
EXTRACTOR_VERSION = "relations-1.2"

CN_NUM = "一二三四五六七八九十百千零两"
_CN_NUM_RE = rf"[{CN_NUM}\d]"


# ===========================================================================
# 一、配置层
# ===========================================================================
@dataclass
class RelationConfig:
    """关系抽取配置（词表全部外置；换领域只换本对象）。"""

    # ---- 依据关系 ----
    basis_triggers: tuple[str, ...] = (
        "根据",
        "依据",
        "依照",
        "按照",
        "遵照",
        "基于",
        "循",
        "参照",
    )
    basis_actions: tuple[str, ...] = (
        "制定",
        "起草",
        "发布",
        "印发",
        "出台",
        "拟定",
        "编制",
        "制订",
        "颁发",
        "下发",
        "修订",
    )
    hierarchy_words: tuple[str, ...] = (
        "法律",
        "法规",
        "规章",
        "规定",
        "条例",
        "办法",
        "意见",
        "通知",
        "文件",
        "决定",
        "命令",
    )
    # ---- 废止关系（**长词在前**，防"同时废止"被切为"废止"）----
    repeal_actions: tuple[tuple[str, str], ...] = (
        ("同时废止", REPEAL_ACTION_REPEAL),
        ("予以废止", REPEAL_ACTION_REPEAL),
        ("宣布废止", REPEAL_ACTION_REPEAL),
        ("宣布失效", REPEAL_ACTION_INVALIDATE),
        ("停止执行", REPEAL_ACTION_CEASE),
        ("不再适用", REPEAL_ACTION_INAPPLICABLE),
        ("予以取消", REPEAL_ACTION_CANCEL),
        ("废止", REPEAL_ACTION_REPEAL),
        ("失效", REPEAL_ACTION_INVALIDATE),
        ("取消", REPEAL_ACTION_CANCEL),
    )
    repeal_reason_patterns: tuple[str, ...] = (
        r"文件内容被新规定取代",
        r"与(?:监管|管理)?实践不符",
        r"阶段性工作已结束",
        r"上位法(?:已)?(?:废止|失效)",
        r"调整对象已消失",
        r"任务已完成",
        r"主要内容与现行[^。，,]{0,20}相抵触",
        r"被《[^》]+》替代",
    )
    # ---- 排除上下文（命中窗口内含其一 → 不视为依据/废止关系）----
    exclude_contexts: tuple[str, ...] = (
        "负责解释",
        "解释权",
        "授权解释",
        "委托解释",
        "行政复议",
        "行政诉讼",
        "申请复议",
        "提起诉讼",
        "法律适用",
        "参考资料",
    )
    # ---- 否定/未生效语境（命中窗口内含其一 → 不视为废止关系；本项目新增）----
    negations: tuple[str, ...] = (
        "拟废止",
        "建议废止",
        "征求意见",
        "草案",
        "待废止",
        "是否废止",
    )
    # ---- 程序性依据 ----
    procedural_triggers: tuple[str, ...] = ("经", "报经")
    procedural_actions: tuple[str, ...] = (
        "同意",
        "批准",
        "审核同意",
        "审议通过",
        "批复",
    )
    # ---- 引用与句法 ----
    open_quote: str = "《"
    close_quote: str = "》"
    exclude_window: int = 100
    snippet_window: int = 200
    # N-98（2026-09-28）：引用跨度的**向后**延伸上限（仅向后，不向前）。
    # 为何需要：多数引用模式（依据式等）只匹配到 `《名》`，**目标条号紧随其后**
    # （"根据《民法典》第186条"）→ 只取命中文本会把条号切掉。实测：只取 `m.group(0)`
    # 使目标侧定位由 5.7% **倒退**至 2.3%；改"命中+向后 40 字符（截至句读）"后方可保住。
    span_tail_window: int = 40
    # 文号形态（法规库/公文两类；用于"废止关系"的目标文号与目标解析）
    docno_patterns: tuple[str, ...] = (
        r"[\u4e00-\u9fa5]{2,20}(?:〔|\[|【)\s*\d{4}\s*(?:〕|\]|】)\s*第?\s*\d{1,4}\s*号",
        r"[\u4e00-\u9fa5]{2,20}(?:令|公告|通告)\s*第\s*\d{1,4}\s*号",
        r"[\u4e00-\u9fa5]{2,20}令\s*\d{4}\s*年第\s*\d{1,4}\s*号",
    )


def default_config() -> RelationConfig:
    return RelationConfig()


def snippet_half_window(cfg: RelationConfig | None = None) -> int:
    """`_snippet` 的**半宽** ＝ 窗口字符串内「引用起点」的下标基准（N-109）。

    用途：写入侧/诊断侧需把窗口切成"引用**之前**"与"引用**之后**"两段（N-109 **前文剔除**）。
    抽成函数以保证**半宽只有一处推导**（此前诊断侧与写入侧各自 `// 2` → 重复口径、易漂移）。
    """
    return (cfg or RelationConfig()).snippet_window // 2


# ===========================================================================
# 二、工具层
# ===========================================================================
_FULLWIDTH_PAREN = str.maketrans({"（": "(", "）": ")"})
_BRACKET_TAIL = re.compile(r"[（(\[][^）)\]]*[）)\]]\s*$")


def preprocess(text: str) -> str:
    """文本预处理：全角括号→半角、全角空格→空格、压缩空白。**保留书名号**（识别核心标志）。"""
    if not text:
        return ""
    t = str(text).translate(_FULLWIDTH_PAREN).replace("\u3000", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def normalize_doc_name(name: str) -> str:
    """文件名称归一（去重/匹配用）：去书名号 → 去文号括注 → 去空白 → 经 norm_title_strict。

    与归属表/RFN 索引的**精确匹配**同语义（保守层），避免过度归并引发误配。
    """
    if not name:
        return ""
    t = str(name).replace("《", "").replace("》", "").replace("〈", "").replace("〉", "")
    t = _BRACKET_TAIL.sub("", t)
    t = re.sub(r"\s+", "", t)
    return norm_title_strict(t)


def docno_signature(docno: str) -> str:
    """文号签名 = 归一后取"四位年 + 序号"数字尾（与 `merged` 的 `docno_sig` 同义）。

    实测：`银保监发〔2019〕19号` → `201919`；`中华人民共和国国务院令第262号` → ``（无四位年，
    不走签名匹配，改走精确/标题匹配）。
    """
    d = norm_docno(docno)
    if not d:
        return ""
    m = re.search(r"(\d{4})\D*?(\d{1,4})$", d)
    if m:
        return m.group(1) + m.group(2)
    return ""


def iter_quote_titles(text: str, *, min_len: int = 4, max_len: int = 40) -> list[str]:
    """抽取正文中的书名号标题（去重、保持出现顺序）。

    R-F01 收敛点：`internal_policy_base.merged` 的 `_TITLE_REF`（`《…》`，长度 4-40）与
    `relations` 内部的 `_quotes` 曾各自实现——现统一走本函数（参数化长度区间以保持既有语义）。
    """
    out: list[str] = []
    for m in re.finditer(r"《([^《》\n]+)》", text or ""):
        t = m.group(1).strip()
        if len(t) < min_len or (max_len and len(t) > max_len):
            continue
        if t not in out:
            out.append(t)
    return out


def iter_docno_signatures(text: str, *, min_len: int = 6) -> list[str]:
    """从正文抽取**文号签名**（四位年 + 序号，如 `201919`），含**裸括号式**（无机关代字）。

    R-F01 收敛点：`merged.extract_rfns` 的 `docno_sig` 语义即"正文文号签名 → 归属表文号数字尾"
    匹配；原正则 `[〔\\[(（]\\s*(\\d{4})\\s*[〕\\])）]\\s*(\\d{1,4})\\s*号` 只认裸括号式，
    本函数保持该语义（区别于 `extract_docnos` 的"带机关代字完整文号"口径）。
    """
    out: list[str] = []
    for m in re.finditer(r"[〔\[(（]\s*(\d{4})\s*[〕\])）]\s*(\d{1,4})\s*号", text or ""):
        sig = m.group(1) + m.group(2)
        if len(sig) >= min_len and sig not in out:
            out.append(sig)
    return out


def split_sentences(text: str) -> list[str]:
    """按中文句读切分句子，保留分隔符。

    N-90（2026-09-28）：句读集合改取**唯一事实源** `std_lib.common_lib.sentence_boundary`
    （原为本地字面量 `[。．.；;！!？?]`，与清洗层各写一份 → 口径二重）。
    **行为不变**：走同模块 `split_by(text, "loose")`，其字符集与原字面量**逐字符相同**。

    N-162（2026-09-30）：**接线 P1 分句增强**（`P1-3 分句/结构增强层`，挂接节点 `relations:gen`）。
    调用链改为经**执行门面** `semantic_enhance.split_sentences(text, level="loose")`：
      · **默认（ML 关）** → 仍走 `sentence_boundary.split_by(text,"loose")` ⇒ **逐字节对等，零回归**；
      · 置 `REG_ORCH_SEMANTIC_SPLIT=1` → 走 LTP 分词锚切句（**增强**），失败**自动回退**并留痕。
    启用 ML 前须在**可评样本**上量出 P/R（v2 纪律：无度量不得上线）—— 故默认关闭。
    """
    from std_lib.common_lib import semantic_enhance as _se

    return _se.split_sentences(text, level="loose").sentences


def extract_docnos(text: str, config: RelationConfig | None = None) -> list[str]:
    """抽取文本中的文号（含法规库形态）。返回出现顺序去重列表。"""
    cfg = config or default_config()
    out: list[str] = []
    for pat in cfg.docno_patterns:
        for m in re.finditer(pat, text or ""):
            v = re.sub(r"\s+", "", m.group(0))
            if v not in out:
                out.append(v)
    return out


# ===========================================================================
# 二·B、共享口径（R-F01 收敛点）
# ---------------------------------------------------------------------------
# 背景：以下"公文语体口径"此前在 **3 处旧实现**中各写一份字面量，口径靠人眼保持一致：
#   ① `regulatory_classifier/scripts/build_clause_graph.py`（P1-P5 条款级引用图）
#   ② `regulatory_classifier/scripts/build_detail_tables.py`（明细表「立法依据/条款引用」列）
#   ③ `internal_policy_drafter/scripts/verify_regulatory_citations.py`（引用核验门禁）
# 现统一由本模块导出，三处改为 import —— 口径单点演进（改一处即全链生效），
# **且保持各自原有语义**（本层只做"去重"，不改变既有匹配行为）。
# ===========================================================================
# 条款序号字符集（clause_graph.NUM / detail_tables 字面量同源；**不含"两"**，
# 与既有产物严格一致——本模块内部另有 `CN_NUM` 含"两"，属更宽口径，勿混）
CN_NUM_CHARS = "零一二三四五六七八九十百千"
ARTICLE_NUM = rf"[0-9{CN_NUM_CHARS}]+"
# 条款链：第N条[第M款][第K项]（无捕获组版 / 带捕获组版）
ARTICLE_CHAIN = rf"第{ARTICLE_NUM}条(?:第{ARTICLE_NUM}款)?(?:第{ARTICLE_NUM}项)?"
ARTICLE_CHAIN_CAPTURE = rf"第({ARTICLE_NUM})条(?:第({ARTICLE_NUM})款)?(?:第({ARTICLE_NUM})项)?"
# 书名号标题跨度（4 处旧实现均为 2..40）
QUOTE_TITLE_MIN, QUOTE_TITLE_MAX = 2, 40
# 依据触发词**核心 4 词**：条款级/明细级抽取的稳定口径。
# `RelationConfig.basis_triggers` 默认值 = 本元组 + 扩展词（遵照/基于/循/参照）。
BASIS_TRIGGER_CORE: tuple[str, ...] = ("根据", "依据", "依照", "按照")
# 自指词（P2 自指条款：本办法第N条）
SELF_REF_WORDS: tuple[str, ...] = (
    "本办法",
    "本规定",
    "本通知",
    "本指引",
    "本细则",
    "本条例",
    "本规则",
)
# 立法类名称结尾（明细表「立法依据」列筛选）
LAW_SUFFIX_ALT = "法|条例|规定|决定|解释|细则"
# 文号**核心形态**（不含机关代字约束）：`〔20xx〕N号 / [20xx]N号 / （20xx）N号 /
# 令20xx年第N号 / 国务院令第N号` —— verify 门禁的 DOCNO_CORE_PAT 与此同源；
# 本模块 `RelationConfig.docno_patterns` 是**带机关代字的扩展形态**（用于正文文号定位）。
DOCNO_CORE_PATTERN = (
    r"[〔\[(（]\s*(\d{4})\s*[〕\])）]\s*(\d+)\s*号?"
    r"|令\s*(\d{4})\s*年第\s*(\d+)\s*号"
    r"|国务院令第(\d+)号"
)
# 监管发文机关词表（原 verify.ORGAN_PREFIXES 迁入）：括号式文号前存在这些词才判定为
# "监管文件文号"进入引用门禁；无机关词的裸文号（如〔2023〕687号）视为内部 OA 文号。
# 新增监管机关时在此扩展（按最长优先匹配）。
ORGAN_WORDS: tuple[str, ...] = (
    "国家金融监督管理总局办公厅",
    "中国银行保险监督管理委员会办公厅",
    "中国保险监督管理委员会办公厅",
    "国家金融监督管理总局",
    "中国银行保险监督管理委员会",
    "中国保险监督管理委员会",
    "银保监会办公厅",
    "中国人民银行等八部门公告",
    "最高人民法院",
    "国务院办公厅",
    "人民银行",
    "银保监会",
    "保监会",
    "银监发",
    "银监办发",
    "银监通",
    "银监办通",
    "银监复",
    "银监函",
    "保监发",
    "保监厅发",
    "保监产险",
    "保监财会",
    "保监稽查",
    "保监消保",
    "保监厅函",
    "保监复",
    "保监函",
    "银保监发",
    "银保监办发",
    "金办发",
    "金办便函",
    "金规",
    "银发",
    "国发",
    "国办发",
    "财金",
    "发改",
    "银监会令",
    "保监会令",
    "银保监会令",
    "国务院令",
    "证监会",
    "外汇局",
    "网信办",
    "知识产权局",
    "工信部",
    "市场监管总局",
    "中保协",
    "八部门公告",
)


def quote_title_capture(min_len: int = QUOTE_TITLE_MIN, max_len: int = QUOTE_TITLE_MAX) -> str:
    """书名号标题**捕获**形态，如 `《([^《》]{2,40})》`（第 1 组 = 标题）。"""
    return rf"《([^《》]{{{min_len},{max_len}}})》"


def quote_title_re(min_len: int = QUOTE_TITLE_MIN, max_len: int = QUOTE_TITLE_MAX) -> re.Pattern:
    """编译版书名号标题（`TITLE_PAT` 等旧实现同源）。"""
    return re.compile(quote_title_capture(min_len, max_len))


def basis_trigger_alt(triggers: tuple[str, ...] = BASIS_TRIGGER_CORE) -> str:
    """依据触发词的正则**择一分支**（`根据|依据|依照|按照`）。"""
    return "|".join(map(re.escape, triggers))


def docno_core_re() -> re.Pattern:
    """文号核心形态编译版（verify 门禁 `DOCNO_CORE_PAT` 同源）。"""
    return re.compile(DOCNO_CORE_PATTERN)


# ===========================================================================
# 二·C、目标分类（R-F01 语义分层，2026-09-14）
# ---------------------------------------------------------------------------
# 动机（实测口径修正）：初版把"未解析"一律算作解析失败，但实测 1018 条未解析里
# **大量不是文件引用**——`国务院` 182×、`国务院银行业监督管理机构` 23×、`本级人民政府`、
# `其总公司` 等**机关名**（程序性依据的目标本就是机关），以及 `条例`/`办法` 这类**泛指词**
# （`《条例》` 原文即泛指，属抽取噪声）。把它们计入"文件解析率"会**低估**真实覆盖度。
# 故对每条关系的目标做性质分类，只在 `external`（语料外文件）上计算"未定位"。
# ===========================================================================
TARGET_ENTITY = "entity"  # 强实体：解析到 RFN / IPN（可 join 底座）
TARGET_CORPUS = "corpus"  # 弱引用：命中 cleaned 全集（dedup_key），RFN 未登记
TARGET_ORGAN = "organ"  # 机关名（程序性依据目标，**非文件**）
TARGET_GENERIC = "generic"  # 纯类型泛指词（`《条例》`/`《办法》`），抽取噪声
TARGET_EXTERNAL = "external"  # 语料外文件（法律/行政法规/司法解释等，客观未采集）

TARGET_CLASSES: tuple[str, ...] = (
    TARGET_ENTITY,
    TARGET_CORPUS,
    TARGET_ORGAN,
    TARGET_GENERIC,
    TARGET_EXTERNAL,
)

# 机关名后缀（判定"目标是否为机关而非文件"；须同时不命中制度类关键词，见 _is_organ_target）
_ORGAN_TAIL_RE = re.compile(
    r"(?:国务院|人民政府|政府|委员会|管理委员会|银行业监督管理机构|保险监督管理机构|监督管理机构"
    r"|监管机构|总公司|分公司|公司|银行|总行|法院|检察院|院|署|部|厅|局|中心|协会|联合会"
    r"|交易所|办公室|事业部)$"
)
# 制度类关键词（判定目标是否"文件"而非"机关"：命中即不判为机关）
_DOC_KW_RE = re.compile(
    r"办法|规定|通知|条例|细则|指引|制度|方案|规程|准则|标准|意见|决定|公告|通告|规则|规范"
    r"|批复|解释|协议|清单|手册|备忘录|要点|规划|计划|报告|文书|凭证|承诺书|确认书"
)
# 纯类型泛指词（书名号内**只有**类型词 → 不是具体文件）
GENERIC_ONLY: frozenset[str] = frozenset(
    {
        "条例",
        "办法",
        "规定",
        "细则",
        "通知",
        "决定",
        "意见",
        "指引",
        "制度",
        "方案",
        "规程",
        "准则",
        "标准",
        "法",
        "公告",
        "通告",
        "规则",
        "规范",
        "批复",
        "函",
        "命令",
    }
)


def is_generic_target(name: str) -> bool:
    """是否纯类型泛指词（`《条例》`/`《办法》`）——抽取侧据此**过滤**（噪声，不可定位）。"""
    n = re.sub(r"\s+", "", (name or "").strip("《》〈〉"))
    return n in GENERIC_ONLY


def is_organ_target(name: str) -> bool:
    """是否机关名（程序性依据的目标是机关；**不是**文件引用）。"""
    n = re.sub(r"\s+", "", (name or "").strip("《》〈〉"))
    if len(n) < 2 or len(n) > 24:
        return False
    if _ORGAN_TAIL_RE.search(n) and not _DOC_KW_RE.search(n):
        return True
    return False


def classify_target(
    *, dst_ref: str = "", dst_key: str = "", name: str = "", basis_type: str = ""
) -> str:
    """关系目标的性质分类（见 TARGET_CLASSES）。

    判定顺序：强实体 → 弱引用 → 程序性依据（目标即机关）→ 机关名 → 泛指词 → 语料外文件。
    """
    if dst_ref:
        return TARGET_ENTITY
    if dst_key:
        return TARGET_CORPUS
    if basis_type == BASIS_TYPE_PROCEDURAL and is_organ_target(name):
        return TARGET_ORGAN
    if is_organ_target(name):
        return TARGET_ORGAN
    if is_generic_target(name):
        return TARGET_GENERIC
    return TARGET_EXTERNAL


# 编译版常量（供三处旧实现直接 import，避免各自 compile 字面量）
SELF_REF_RE = re.compile(
    rf"(?:{'|'.join(map(re.escape, SELF_REF_WORDS))})[^。；\n]{{0,6}}?第({ARTICLE_NUM})条"
)
BARE_ARTICLE_RE = re.compile(rf"(?<![0-9{CN_NUM_CHARS}])第({ARTICLE_NUM})条")
