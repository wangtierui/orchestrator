# -*- coding: utf-8 -*-
"""std_lib.common_lib.relations — 依据关系 / 废止关系 通用抽取（**唯一事实源**）

定位
----
跨**监管文件**与**内部制度**两类文本的通用关系抽取层：只依赖 `std_lib`（归一 SSOT）与
`config.enums`（受控枚举 SSOT），**不依赖任何 modules/ 业务包**（分层方向：modules → std_lib）。
实体解析（名称/文号 → RFN/IPN）属编排层职责（`tools/extract_relations.py`），本层只输出
"文本中出现了什么关系、指向哪个名称/文号"。

设计（参照《政府文件依据关系与废止关系通用抽取器》七层结构，并做本项目适配）
--------------------------------------------------------------------
1. **配置层** `RelationConfig`：触发词/动作词/位阶词/废止动作/废止原因/排除上下文/程序性依据
   词组全部外置，换领域只换配置。
2. **工具层** `preprocess` / `normalize_doc_name` / `split_sentences` / `extract_docnos`。
3. **数据结构层** `BasisRelation` / `RepealRelation` / `ExtractionResult`（含 `to_dict`）。
4. **抽取器层** `RelationExtractor`：六类主模式 + 三类辅助模式。
5. **别名解析层** `AliasResolver`。
6. **管道层** `RelationPipeline`：`extract` + 别名解析 → 统一 dict。

相对参照实现的**本项目适配**（差异逐条说明，便于审计）
----------------------------------------------------
- **归一收口**：名称归一改用项目 SSOT `std_lib.common_lib.norm.norm_title_strict`（保守层，
  与归属表精确匹配同语义），文号归一用 `norm_docno`；不另造归一实现（参照实现自带一份）。
- **枚举收口**：`basis_type` / `scope` / `action` / `relation` 取值一律来自 `config.enums`
  （受控枚举 SSOT；项目纪律"值统一小写英文"，故参照实现的中文值改为
  `substantive`/`procedural`、`whole`/`partial`/`attachment`、`repeal`/`invalidate`/…）。
- **文号形态扩展**：法规库形态（`中华人民共和国国务院令第262号`、`令2011年第N号`、
  `〔2011〕N号`、`[2011]N号`）——参照实现只认括号式。
- **否定/未生效排除**：`拟废止`/`建议废止`/`征求意见`/`（草案）` 等**不作为**废止关系
  （真实语料中 `replacement_document` 邻近文本常含"拟"，参照实现无此防护）。
- **来源偏移** `offset`：每条关系记录命中位置字符偏移，供审计与正文定位（参照实现只有片段）。
- **`unresolved` 不臆造**：名称/文号命中不到实体时**保留原文与 `matched_by="unresolved"`**，
  绝不猜测 RFN（与 drafter `verify_regulatory_citations` 的"禁止臆造"同纪律）。

消费方（三者共用本层）
--------------------
- `regulatory_classifier`：明细表「立法依据/条款引用」、关系图谱报告；
- `internal_policy_base`：`merged_view.associated_rfns`（制度×监管依据）；
- `internal_policy_drafter`：条款对照素材的"依据/废止"段、引用核验。

用法
----
    from std_lib.common_lib.relations import RelationPipeline

    out = RelationPipeline().run(text)
    out["basis"]    # [{target_name, article, basis_type, ...}]
    out["repeal"]   # [{target_name, target_docno, action, scope, ...}]
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_STD_LIB = os.path.join(_ROOT, "std_lib")
if _STD_LIB not in sys.path:
    sys.path.insert(0, _STD_LIB)

from config.enums import (  # noqa: E402
    BASIS_TYPE_PROCEDURAL,
    BASIS_TYPE_SUBSTANTIVE,
    RELATION_DOC_KIND,
    RELATION_KIND,
    REPEAL_ACTION_CANCEL,
    REPEAL_ACTION_CEASE,
    REPEAL_ACTION_INAPPLICABLE,
    REPEAL_ACTION_INVALIDATE,
    REPEAL_ACTION_REPEAL,
    REPEAL_SCOPE_PARTIAL,
    REPEAL_SCOPE_WHOLE,
)
from std_lib.common_lib.norm import norm_docno, norm_title_strict  # noqa: E402

SCHEMA_VERSION = "1.0"
# 2026-09-20 → 1.1：relation_id 派生纳入判别字段（article/action/scope/reason/dst_docno/
# dst_kind/src_key）并在写入前做确定性唯一化——修复"同一 id 命中多行"的既有缺陷。
EXTRACTOR_VERSION = "relations-1.1"

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
    # 文号形态（法规库/公文两类；用于"废止关系"的目标文号与目标解析）
    docno_patterns: tuple[str, ...] = (
        r"[\u4e00-\u9fa5]{2,20}(?:〔|\[|【)\s*\d{4}\s*(?:〕|\]|】)\s*第?\s*\d{1,4}\s*号",
        r"[\u4e00-\u9fa5]{2,20}(?:令|公告|通告)\s*第\s*\d{1,4}\s*号",
        r"[\u4e00-\u9fa5]{2,20}令\s*\d{4}\s*年第\s*\d{1,4}\s*号",
    )


def default_config() -> RelationConfig:
    return RelationConfig()


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
    """按中文句读切分句子，保留分隔符。"""
    if not text:
        return []
    parts = re.split(r"(?<=[。．.；;！!？?])\s*", text)
    return [p.strip() for p in parts if p.strip()]


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


def quote_title_span(min_len: int = QUOTE_TITLE_MIN, max_len: int = QUOTE_TITLE_MAX) -> str:
    """书名号标题**跨度**（无捕获组），如 `《[^《》]{2,40}》`。"""
    return rf"《[^《》]{{{min_len},{max_len}}}》"


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


# ===========================================================================
# 三、数据结构层
# ===========================================================================
@dataclass
class BasisRelation:
    """依据关系：本文以《target_name》为制定/起草依据。"""

    target_name: str
    normalized_name: str = ""
    target_docno: str = ""
    article: str = ""
    basis_type: str = BASIS_TYPE_SUBSTANTIVE
    is_explicit: bool = True
    source_snippet: str = ""
    offset: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepealRelation:
    """废止关系：本文废止/宣布失效《target_name》。"""

    target_name: str
    normalized_name: str = ""
    target_docno: str = ""
    action: str = REPEAL_ACTION_REPEAL
    scope: str = REPEAL_SCOPE_WHOLE
    article: str = ""
    reason: str = ""
    source_snippet: str = ""
    offset: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    """单篇文件的抽取结果。"""

    basis: list[BasisRelation] = field(default_factory=list)
    repeal: list[RepealRelation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 被过滤的"纯类型泛指词"目标数（`《条例》`/`《办法》`，2026-09-14 净化，见 is_generic_target）
    filtered_generic: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "basis": [b.to_dict() for b in self.basis],
            "repeal": [r.to_dict() for r in self.repeal],
            "warnings": list(self.warnings),
            "filtered_generic": self.filtered_generic,
        }


# ===========================================================================
# 四、抽取器层
# ===========================================================================
class RelationExtractor:
    """通用依据/废止关系抽取器（六类主模式 + 三类辅助模式）。"""

    def __init__(self, config: RelationConfig | None = None):
        self.cfg = config or default_config()
        self._filtered_generic = 0
        self._compile()

    def _skip_generic(self, name: str) -> bool:
        """纯类型泛指词目标（`《条例》`/`《办法》`）→ 过滤并计数（2026-09-14 净化）。

        这类书名号在原文中就是**泛指**（如"依据有关条例"），不指向任何具体文件；
        保留会污染"未定位文件"统计（实测 15 条）。
        """
        if is_generic_target(name):
            self._filtered_generic += 1
            return True
        return False

    def _compile(self) -> None:
        cfg = self.cfg
        trg = "|".join(map(re.escape, cfg.basis_triggers))
        act = "|".join(map(re.escape, cfg.basis_actions))
        hier = "|".join(map(re.escape, cfg.hierarchy_words))
        sep = r"[、，,和及与以及]"
        q, qc = cfg.open_quote, cfg.close_quote
        one = rf"{q}[^{qc}]+{qc}"
        # 书名号列表：**分隔符可缺省**（真实公文中 `《A》《B》` 常紧邻，参照实现要求分隔符会漏抽）
        reflist = rf"{one}(?:\s*(?:{sep})?\s*{one})*"
        # 中间连接段：等? + 位阶词（可连写，如"法律法规"）+ 的+规定/要求/精神? + 逗号?
        mid = (
            rf"\s*(?:等)?\s*(?:(?:有关)?(?:{hier})+)?\s*"
            rf"(?:的\s*(?:相关|有关)?(?:规定|要求|精神|条款|内容|通知|办法|意见|细则))?\s*[，,]?\s*"
        )

        # ① 依据主模式：触发词 + 书名号列表 + 中间连接段 + 动作词
        self.basis_pattern = re.compile(
            rf"(?:{trg})\s*(?P<refs>{reflist}){mid}(?P<action>{act})(?:了|本|该|这)?"
        )
        # ② 条款级依据：触发词 + 《名》 + 第X条
        self.basis_article_pattern = re.compile(
            rf"(?:{trg})\s*{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}\s*"
            rf"第(?P<article>{_CN_NUM_RE}+)条"
        )
        # ③ 程序性依据：经 + **机关名** + 同意/批准。
        # 2026-09-14 收紧（实测反例）：原 `[\u4e00-\u9fa5]{2,20}?` 最短匹配会跨词捕获，
        # 产出 `批准或者未按照`、`依法`、`部门负责人` 等非机关噪声。现要求：
        # 机关名须以**法定机关后缀**结尾，且**不含连接虚词**（或/未/按照/…）。
        ptrg = "|".join(map(re.escape, cfg.procedural_triggers))
        pact = "|".join(map(re.escape, cfg.procedural_actions))
        org_suffix = r"(?:总局|委员会|监管总局|保监会|银保监会|证监会|国资委|人民银行|银行|分行|局|委|部|厅|院|署|政府|机构|公司|中心|办公室)"
        bad = r"或|未|按照|和|与|等|应当|可以|并|及|由|对|在|以|所|的|有批准权|其他"
        self.procedural_pattern = re.compile(
            rf"(?:{ptrg})(?P<authority>(?:(?!{bad})[\u4e00-\u9fa5]){{2,18}}{org_suffix})(?:{pact})"
        )
        # ④ 废止单文件：（《名》）? （文号）? 动作
        ract = "|".join(map(re.escape, [k for k, _v in cfg.repeal_actions]))
        dn = "|".join(f"(?:{p})" for p in cfg.docno_patterns)
        self.repeal_pattern = re.compile(
            rf"{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}"
            rf"(?:\s*[（(\[](?P<number>[^）)\]]*)[）)\]]\s*[，,]?)?\s*"
            rf"(?P<action>{ract})"
        )
        # ⑤ 废止列表：动作 + 书名号列表
        self.repeal_list_pattern = re.compile(rf"(?P<action>{ract})\s*(?P<refs>{reflist})")
        _ = dn  # （保留占位说明：文号形态见 self.docno_re，本处不内联以降低正则复杂度）
        # ⑥ 部分废止：《名》 第X条 动作
        self.partial_repeal_pattern = re.compile(
            rf"{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}\s*(?:中|的)?\s*"
            rf"第(?P<article>{_CN_NUM_RE}+)条(?:\s*(?:至|—|-)\s*第{_CN_NUM_RE}+条)?\s*"
            rf"(?P<action>{ract})"
        )
        # 辅助① 专项废止列表头
        self.repeal_list_header = re.compile(
            rf"(?:决定|现决定)?\s*(?:予以)?(?:废止|宣布失效)\s*(?:以下|下列|如下)?\s*"
            rf"(?P<count>{_CN_NUM_RE}+)?\s*(?:部|件|个|份)?\s*(?:规章|规范性文件|文件|规定|办法)\s*[：:]"
        )
        # 辅助② 列表条目：一、《名称》（文号）。
        self.list_item_pattern = re.compile(
            rf"(?P<idx>{_CN_NUM_RE}+)\s*[、，,．.]\s*{cfg.open_quote}(?P<name>[^{cfg.close_quote}]+){cfg.close_quote}"
            rf"(?:\s*[（(\[](?P<number>[^）)\]]*)[）)\]]\s*[。．.]?)?"
        )
        # 辅助③ 附件提示
        self.attachment_hint = re.compile(r"(?:见|详见|参见)\s*(?:附件|附表|附后|附录)")
        # 文号（废止目标的文号回填）
        self.docno_re = re.compile("|".join(f"(?:{p})" for p in cfg.docno_patterns))
        # 废止原因
        self.reason_res = [re.compile(p) for p in cfg.repeal_reason_patterns]
        self._ract_pairs = cfg.repeal_actions

    # ---- 主入口 ----
    def extract(self, text: str) -> ExtractionResult:
        t = preprocess(text)
        self._filtered_generic = 0
        res = ExtractionResult()
        res.basis = self._extract_basis(t)
        res.repeal, warns = self._extract_repeal(t)
        res.warnings.extend(warns)
        res.filtered_generic = self._filtered_generic
        if self.attachment_hint.search(t):
            res.warnings.append('检测到"见附件"提示，废止/依据清单可能位于附件中')
        return res

    # ---- 依据 ----
    def _extract_basis(self, text: str) -> list[BasisRelation]:
        out: list[BasisRelation] = []
        seen: set[tuple[str, str]] = set()

        for m in self.basis_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            for name in self._quotes(m.group("refs")):
                if self._skip_generic(name):
                    continue
                key = (normalize_doc_name(name), "basis")
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    BasisRelation(
                        target_name=name.strip(),
                        normalized_name=key[0],
                        source_snippet=self._snippet(text, m.start()),
                        offset=m.start(),
                    )
                )

        for m in self.basis_article_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            name = m.group("name").strip()
            if self._skip_generic(name):
                continue
            art = "第" + m.group("article") + "条"
            key = (normalize_doc_name(name), "basis")
            if key in seen:
                for r in out:
                    if r.normalized_name == key[0] and not r.article:
                        r.article = art
                        break
                continue
            seen.add(key)
            out.append(
                BasisRelation(
                    target_name=name,
                    normalized_name=key[0],
                    article=art,
                    source_snippet=self._snippet(text, m.start()),
                    offset=m.start(),
                )
            )

        for m in self.procedural_pattern.finditer(text):
            if self._excluded(text, m.start()):
                continue
            auth = m.group("authority").strip()
            if not auth:
                continue
            key = (normalize_doc_name(auth), "procedural")
            if key in seen:
                continue
            seen.add(key)
            out.append(
                BasisRelation(
                    target_name=auth,
                    normalized_name=key[0],
                    basis_type=BASIS_TYPE_PROCEDURAL,
                    source_snippet=self._snippet(text, m.start()),
                    offset=m.start(),
                )
            )
        return out

    # ---- 废止 ----
    def _extract_repeal(self, text: str) -> tuple[list[RepealRelation], list[str]]:
        out: list[RepealRelation] = []
        warns: list[str] = []
        seen: set[tuple[str, str]] = set()
        reason = self._reason(text)

        def _add(
            name: str,
            action_cn: str,
            *,
            number: str = "",
            scope: str = REPEAL_ACTION_REPEAL,
            article: str = "",
            snippet: str,
            offset: int,
        ) -> None:
            action = dict(self._ract_pairs).get(action_cn, REPEAL_ACTION_REPEAL)
            if not name.strip() or self._skip_generic(name):
                return
            key = (normalize_doc_name(name), action)
            if key in seen:
                return
            seen.add(key)
            out.append(
                RepealRelation(
                    target_name=name.strip(),
                    normalized_name=key[0],
                    target_docno=(number or "").strip(),
                    action=action,
                    scope=scope,
                    article=article,
                    reason=reason,
                    source_snippet=snippet,
                    offset=offset,
                )
            )

        # ① 单文件 + 动作
        for m in self.repeal_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            num = m.group("number") or ""
            if not num:  # 《名》后紧跟动作但无括注 → 从邻域回填文号
                num = self._nearby_docno(text, m.start(), m.end())
            _add(
                m.group("name"),
                m.group("action"),
                number=num,
                scope=REPEAL_SCOPE_WHOLE,
                snippet=self._snippet(text, m.start()),
                offset=m.start(),
            )

        # ② 动作 + 多文件列表
        for m in self.repeal_list_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            for name in self._quotes(m.group("refs")):
                _add(
                    name,
                    m.group("action"),
                    scope=REPEAL_SCOPE_WHOLE,
                    snippet=self._snippet(text, m.start()),
                    offset=m.start(),
                )

        # ③ 部分废止
        for m in self.partial_repeal_pattern.finditer(text):
            if self._excluded(text, m.start()) or self._negated(text, m.start()):
                continue
            _add(
                m.group("name"),
                m.group("action"),
                scope=REPEAL_SCOPE_PARTIAL,
                article="第" + m.group("article") + "条",
                snippet=self._snippet(text, m.start()),
                offset=m.start(),
            )

        # ④ 专项废止列表（表头 + 编号条目）
        if self.repeal_list_header.search(text):
            items = self._parse_repeal_list(text)
            for it in items:
                _add(
                    it["name"],
                    "废止",
                    number=it["number"],
                    scope=REPEAL_SCOPE_WHOLE,
                    snippet=it["source"],
                    offset=it["offset"],
                )
            if not items:
                warns.append("检测到废止列表头，但未解析出条目，请检查格式")
        return out, warns

    def _parse_repeal_list(self, text: str) -> list[dict]:
        head = self.repeal_list_header.search(text)
        if not head:
            return []
        out = []
        for m in self.list_item_pattern.finditer(text[head.end() :]):
            name = m.group("name").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "number": (m.group("number") or "").strip(),
                    "source": m.group(0).strip(),
                    "offset": head.end() + m.start(),
                }
            )
        return out

    # ---- 辅助 ----
    def _reason(self, text: str) -> str:
        for rp in self.reason_res:
            m = rp.search(text)
            if m:
                return m.group(0).strip()
        return ""

    def _nearby_docno(self, text: str, start: int, end: int, *, window: int = 60) -> str:
        seg = text[max(0, start - window) : min(len(text), end + window)]
        m = self.docno_re.search(seg)
        return re.sub(r"\s+", "", m.group(0)) if m else ""

    def _excluded(self, text: str, pos: int) -> bool:
        w = self.cfg.exclude_window
        ctx = text[max(0, pos - w) : pos + w]
        return any(k in ctx for k in self.cfg.exclude_contexts)

    def _negated(self, text: str, pos: int) -> bool:
        """否定/未生效语境（拟废止、征求意见、草案…）→ 不算废止关系。"""
        w = self.cfg.exclude_window
        ctx = text[max(0, pos - w) : pos + w]
        return any(k in ctx for k in self.cfg.negations)

    def _snippet(self, text: str, pos: int) -> str:
        h = self.cfg.snippet_window // 2
        return text[max(0, pos - h) : min(len(text), pos + h)].strip()

    def _quotes(self, seg: str) -> list[str]:
        return re.findall(
            rf"{self.cfg.open_quote}([^{self.cfg.close_quote}]+){self.cfg.close_quote}", seg or ""
        )


# ===========================================================================
# 五、别名解析层
# ===========================================================================
class AliasResolver:
    """文件名称别名解析：精确 → 归一 → 包含（长度阈值默认 4，防"办法"类短词误配）。"""

    def __init__(self, alias_map: dict[str, str] | None = None, min_substr_len: int = 4):
        self.alias_map: dict[str, str] = dict(alias_map or {})
        self.min_substr_len = min_substr_len
        self._index: dict[str, str] = {}
        for k, v in self.alias_map.items():
            self._index[normalize_doc_name(k)] = v
            self._index[normalize_doc_name(v)] = v

    def add(self, alias: str, full_name: str) -> None:
        self.alias_map[alias] = full_name
        self._index[normalize_doc_name(alias)] = full_name
        self._index[normalize_doc_name(full_name)] = full_name

    def resolve(self, name: str) -> str | None:
        if not name:
            return None
        n = normalize_doc_name(name)
        if not n:
            return None
        if n in self._index:
            return self._index[n]
        for key, full in self._index.items():
            if len(key) < self.min_substr_len:
                continue
            if key in n or n in key:
                return full
        return None


# ===========================================================================
# 六、管道层
# ===========================================================================
@dataclass
class DocumentMeta:
    """文件元信息（与项目实体键对齐：RFN=监管文件，IPN=内部制度）。"""

    doc_kind: str = "regulatory"
    ref: str = ""  # RFN-xxx / IPN-xxx
    name: str = ""
    doc_number: str = ""
    source: str = ""
    publish_date: str = ""
    effective_date: str = ""

    def __post_init__(self) -> None:
        if self.doc_kind not in RELATION_DOC_KIND:
            raise ValueError(f"doc_kind 须 ∈ {sorted(RELATION_DOC_KIND)}，得到 {self.doc_kind!r}")


class RelationPipeline:
    """抽取管道：原文 → 抽取 → （可选）别名解析 → 统一 dict。"""

    def __init__(
        self, config: RelationConfig | None = None, alias_resolver: AliasResolver | None = None
    ):
        self.cfg = config or default_config()
        self.extractor = RelationExtractor(self.cfg)
        self.alias_resolver = alias_resolver

    def run(self, text: str, meta: DocumentMeta | None = None) -> dict:
        res = self.extractor.extract(text)
        out = res.to_dict()
        if self.alias_resolver:
            for item in out["basis"] + out["repeal"]:
                item["resolved_name"] = self.alias_resolver.resolve(item["target_name"])
        if meta is not None:
            out["document"] = asdict(meta)
        return out


# ===========================================================================
# 七、自检
# ===========================================================================
def _self_check() -> None:
    p = RelationPipeline()
    r = p.run(
        "为规范管理，根据《中华人民共和国保险法》《中华人民共和国银行业监督管理法》"
        "等法律法规，制定本办法。《商业银行市场风险管理指引》（银监发〔2004〕10号）同时废止。"
    )
    names = [b["target_name"] for b in r["basis"]]
    assert "中华人民共和国保险法" in names, names
    assert r["repeal"] and r["repeal"][0]["target_docno"].startswith("银监发"), r["repeal"]
    # 否定语境不得算废止
    r2 = p.run("现拟废止《旧办法》（拟稿），并征求各分公司意见。")
    assert not r2["repeal"], r2["repeal"]
    # 枚举闭包
    assert BASIS_TYPE_SUBSTANTIVE in {"substantive"} and REPEAL_SCOPE_PARTIAL in {"partial"}
    assert RELATION_KIND == {"basis", "repeal"}
    assert docno_signature("银保监发〔2019〕19号") == "201919", docno_signature(
        "银保监发〔2019〕19号"
    )
    print(f"[common_lib.relations] 自检通过（{EXTRACTOR_VERSION}）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _self_check()
    print(
        json.dumps(
            RelationPipeline().run(
                "根据《中华人民共和国预算法》，制定本规定。《政府采购信息公告管理办法》"
                "（财库〔2010〕20号）予以废止。"
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
