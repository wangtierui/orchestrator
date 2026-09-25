# -*- coding: utf-8 -*-
"""
std_lib.scraper_std.document_structure — 法规/制度条文结构解析（R21 落地 + 2026-09-18 解析适配）

统一解析中文法规/制度正文的「章-条」骨架（docx_utils 既有 split_into_articles 的
通用化 + 章识别 + 条文语义正文拆分），供：
  - 五源 cleaned 监管文件（match_theme_docs 的条款引用/报告明细）
  - internal_policy_base 内部制度（merged_view / drafter 条款对照）
两处复用，消除散落的第X条正则。

能力：
  extract_structure(text) -> {
     chapters: [{no, title, article_index}]  # 第X章 定位（可为空）
     articles: [{no, number, body}]          # 第X条 起行的条文（body=自条文起至下一条文/章前）
     structure: [{level,number,title,content,items,children}]  # 非条文体的层级结构（见下）
     article_count, chapter_count, structure_count, method
  }
  条文行识别兼容：第N条 / 第[一二三…]条 / 第一条…（阿拉伯或中文数字）。
  method ∈ {regex} 恒为 regex 解析（OCR/抽取噪声场景由调用方先 normalize）。

2026-09-18 适配（依据参考实现 5 个修复件 + auto_degrade_parser，逐条对齐并记录取舍）
------------------------------------------------------------------------------
采纳（与参考一致）：
  ① **自动降级解析**（参考 `auto_degrade_parser.AutoDegradeParser`）：law/bulletin/plan/notice/
     plain 多模式 + `ModeScorer`（coverage 0.5 / legality 0.3 / continuity 0.2）+ 阈值 0.75 +
     fallback。解决"文件解析结果为空"：通知/公告/批复类正文无「第X条」时不再空产出。
  ② **法条合并修复**（参考 `5.1 行合并修复`）：换行截断 + 交叉引用被误判为条首 → 合并回上一条。
  ③ **编号去重**（参考 `5.2 编号去重与重排`）。
  ④ **章节映射校验与修复**（参考 `5.3 章节映射校验与修复`）。
  ⑤ **条款校验器**（参考 `3.1 校验规则清单` + `3.2 核心校验函数` + `3.3 校验输出格式`）：
     V001–V007 七条规则 + `{status, issues:[{rule_id,severity,message,location}]}` 输出。

**取舍（参考与本项目冲突处，逐条说明依据）**：
  - **不重排条号**：参考 `ArticleRenumber` 把 `no` 重排为 1..N、并把 `number` 按序生成
    「第N条」。本项目 `articles[].no` 承载**文中真实条号**语义（下游按条号引用/展示：
    `match_theme_docs` 条款证据、drafter「《X》第N条」对照、`base_publish.external_clauses`
    的 `article_no`），重排会**篡改法律条号**（如把真实的"第五十五条"改写成"第五十四条"）。
    → 只做**去重**，不重排、不重铸 `number`（去重后条号天然唯一）。
  - **不把层级体重标为「第X条」**：参考 `NoticeParser` 把「一、」重标为「第一条」。
    本项目拒绝该重标（同"禁止臆造标识"纪律：下游会据此生成不存在的条号引用），
    改为在 `structure` 中**原样保留序号**（`number="一"` / `"（一）"` / `"1"`）。
  - **`plain` 兜底不物化段落**：参考 `PlainParser` 把每段变成一条。本项目正文全文已由
    cleaned `body_text` 承载，clause 产物再存一份纯属冗余（"内容与索引分离"纪律）
    → plain 仅记 `paragraphs` 计数，`structure` 留空。
  - **模式选择保守化**：参考在全模式中取 argmax 且低于阈值即 fallback。本项目
    **law 模式一旦解析出条文即采用 law**（保证既有 1000+ 法规文档零回归），
    仅在 law 产出为空时才进入降级评分择优；law 得分低于阈值时仅记诊断标记不改变行为。
  - **V002 严重度分档**：参考将"编号不连续"一律判 ERROR。本项目语料含**节选/修正案**
    （真实跳号，如仅收录"第十条起"）→ 跳号降为 WARN；而**重复编号/非单调**保留 ERROR
    （那才是解析缺陷本身）。
  - **V001 严重度分档**：参考 6 个必需字段一律 ERROR。本项目 `effective_date`（1,701 份）
    与 `document_number`（313 份）为空源于**源数据属性**而非本次解析缺陷 → 身份字段
    （`dedup_key`/`title`）ERROR，其余 4 项 WARN（避免上千条噪声淹没真实缺陷）。

本项目原有适配（保留）：
  - `segment_body` 把**无换行**的 cleaned body_text 按章/条真起始切行（cleaned 多被归一为
    单段）。2026-09-18 修正判据：原「前一字符非汉字」过宽（"总 则第一条"被漏切、"、"后的
    交叉引用被误切），改为**前一字符须为句末标点/空白，或前文为章标题**（`_prev_allows_head`）。
"""
from __future__ import annotations

import re

from config.enums import CLAUSE_ISSUE_SEVERITY, CLAUSE_PARSE_MODES  # noqa: F401

# 中文数字 1..9999 支持
_CN = "一二三四五六七八九十百千万零〇两"
_CN_CLS = "[%s]" % _CN
# 公开导出：中文数字字符集/字符类（**单一事实源**）——`clause_index.validate_schema` 的
# 条号形态正则须与解析侧同源，否则「第一百零一条」会被误判为形态异常
# （原正则漏 `零`/`万`，正是 2026-09-18 解析面扩展后暴露的既有缺口）。
CN_NUM_CHARS = _CN
CN_NUM_CLASS = _CN_CLS
_ARTICLE_RE = re.compile(r"^第\s*([0-9]+|%s+)\s*条" % _CN_CLS)
_CHAPTER_RE = re.compile(r"^第\s*([0-9]+|%s+)\s*章" % _CN_CLS)
# 附件/附则起标记（视为正文尾）
_TAIL_RE = re.compile(r"^(附\s*则|附件|附表|附\s*录|附录|关于.*的通知$|关于印发.*的通知$)")
# 章/条标题尾部目录点线 + 页码（如「第1章 基本管理....1」「第一条 ….3」）净化
_TRAIL_DOTS_RE = re.compile(r"[.。·•…\s]*\d*\s*$")
_HEADER_DROP = re.compile(r"^(目录|目\s*录|卷首语|扉页|编写说明)")

# --------------------------------------------------------------------------- #
# 〇、解析输入归一（F5a）+ 文档级尾部截断（F3）—— 2026-09-20 问题驱动
# --------------------------------------------------------------------------- #
# 异常空白：NBSP(U+00A0)/EN SPACE(U+2002)/EM/THIN/IDEOGRAPHIC/零宽。clean 层
# `cleaner._MULTI_SPACE = [ \t\u3000]+` 未覆盖这些字符，而源端（网页 `&nbsp;`、
# PDF 抽取）与 `sentence_split.fix_cn_en_spacing` 会引入 → 实测五源 clauses 产物
# 残留 23,974 个（nfra 15,064），污染标题/正文且干扰文号与引用比对。
_EXOTIC_SPACE = re.compile(
    r"[\u00a0\u2002\u2003\u2007\u2009\u200a\u202f\u205f\u200b\u200c\u200d\ufeff]")
# 两侧均为汉字的异常空白：直接删除（不插空格）
_EXOTIC_SPACE_CJK = re.compile(
    r"(?<=[\u4e00-\u9fff])[\u00a0\u2002\u2003\u2007\u2009\u200a\u202f\u205f\u200b\u200c\u200d\ufeff]+"
    r"(?=[\u4e00-\u9fff])")
# 断句加工（`sentence_split.sentence_break` / `fix_cn_en_spacing`）注入的空格：
# ①`，；、：,` 后 + 空格；②汉字↔ASCII 数字/字母之间 + 空格。
# **仅用于解析输入归一，不写回 cleaned 事实源**（事实源保真由 clean 层纪律保证）。
_INJECT_AFTER_PUNCT = re.compile(r"([，；、：,;])[ \t]+(?=[\u4e00-\u9fff0-9A-Za-z（(])")
_INJECT_CN_ASCII = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[0-9A-Za-z])")
_INJECT_ASCII_CN = re.compile(r"(?<=[0-9A-Za-z])[ \t]+(?=[\u4e00-\u9fff])")


def normalize_parse_text(text: str) -> str:
    """解析输入归一（F5a，**只读**）：异常空白归一/删除；去除断句注入的空格。

    只改变空白字符，不增删实义字符 → 与事实源的"去空白比对"仍然成立（回源核验口径不变）。
    异常空白处理分两档：**两侧均为汉字 → 直接删除**（版面/折行残渣，插入空格会切断词形）；
    其余 → 归一为半角空格（再由下方规则决定是否删除）。
    注意**不**触碰源端的普通半角空格（如章标题「总 则」的版式空格，属原文形态）。
    """
    if not text:
        return ""
    t = _EXOTIC_SPACE_CJK.sub("", text)
    t = _EXOTIC_SPACE.sub(" ", t)
    t = _INJECT_AFTER_PUNCT.sub(r"\1", t)
    t = _INJECT_CN_ASCII.sub("", t)
    t = _INJECT_ASCII_CN.sub("", t)
    return t


# 尾部噪声起标记（行内形态；原 `_TAIL_RE` 只认行首，网页拼接的 inline 形态漏检）
_TAIL_INLINE_RES = (
    re.compile(r"附\s*[：:]"),          # 「…同时废止。 附：中国银保监会发布《…》」
    re.compile(r"答记者问"),            # 「…就《…》答记者问（链接）」
    re.compile(r"此件发至"),            # 「（此件发至银保监分局与地方法人银行保险机构）」
)
# 发文机关（可多个）+ 发文日期 的署名块
_TAIL_ORGS = (r"中国银保监会|中国银监会|中国保监会|中国银行保险监督管理委员会|银保监会|银监会|"
              r"中国人民银行|人民银行|金融监管总局|国家金融监督管理总局|国务院|财政部|证监会|"
              r"民政部|发展改革委|税务总局|国家外汇管理局|审计署")
_TAIL_SIGN_RE = re.compile(
    r"(?:%s)(?:\s*(?:%s))*\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日" % (_TAIL_ORGS, _TAIL_ORGS))
# 网页噪声：站方拼接的「XX 联合发布《…》」（常跟 URL）
_TAIL_WEB_RE = re.compile(r"(?:%s)[^。！？]{0,16}?联合发布《[^》]{0,80}》" % _TAIL_ORGS)
# 署名块之后允许存在的尾巴（括注/空白）；出现句末标点说明是正文，不判署名
_TAIL_TAIL_OK = re.compile(r"^[\s\u3000（(【\[].{0,80}?[）)】\]]?$")


def strip_document_tail(text: str, *, zone_ratio: float = 0.15,
                        zone_min: int = 400) -> tuple[str, dict]:
    """文档级尾部截断（F3）：截去附件/答记者问起标记、发文机关署名日期、网页噪声。

    只作用于**尾区**（末 400 字与末 15% 取大者），命中即截断并返回诊断
    （marker/at/removed）供 `parse_meta.tail_cut` 落盘——**可追溯、不静默**。
    署名块须满足「其后仅剩括注/空白」，避免误截正文中出现的合法日期（如
    「本条例自2010年6月1日起施行。」——不含机关名前置，天然不命中）。
    """
    t = (text or "").rstrip()
    if not t:
        return t, {}
    start = max(0, len(t) - max(zone_min, int(len(t) * zone_ratio)))
    cut, marker = len(t), ""
    for rx in _TAIL_INLINE_RES + (_TAIL_WEB_RE,):
        m = rx.search(t, start)
        if m and m.start() < cut:
            cut, marker = m.start(), rx.pattern[:24]
    for m in _TAIL_SIGN_RE.finditer(t, start):
        if _TAIL_TAIL_OK.match(t[m.end():]):
            if m.start() < cut:
                cut, marker = m.start(), "signature"
    if cut >= len(t):
        return t, {}
    return (t[:cut].strip(),
            {"marker": marker, "at": cut, "removed": len(t) - cut})


# --------------------------------------------------------------------------- #
# 一、锚切分（章/条真起始 + 层级序号）
# --------------------------------------------------------------------------- #
# 「第X章/条」锚（节不切：节标题后必跟条，切节会造出无条的碎片行）
_ANCHOR_RE = re.compile(r"第\s*([0-9]+|%s+)\s*(章|条)" % _CN_CLS)
# 句末标点（真条文句读边界）
_TERMINATORS = "。！？…"
# **不切**（视为内嵌引用 / 续行）的前置字符集 —— 采用**否定清单**（只列举"像引用"的情形），
# 而非"允许清单"：后者会把正文里的**项目符号/私用区字符**（Word 列表符号 `\ue004` 等）误判为
# 非边界 → 条文被吞进上一条（实测 nfra《个人贷款管理办法》52→35、《流动资金贷款管理办法》51→35）。
# ① 连接性标点：`、`/`，`/`；`/`：` 后的「第X条」几乎必是交叉引用（`第五十三条、第五十四条`）；
# ② 引号/书名号收尾：`《保险法》第五条`；
# ③ 汉字/字母/数字：`本法第五十三条`。
_CONNECTIVE_BEFORE = "、，,;；:："
_CLOSERS_HARD = "”」』》"
_CLOSERS_SOFT = "）)】]"
_CJK_ALNUM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9０-９Ａ-Ｚａ-ｚ]")


def _prev_allows_head(text: str, i: int, kind: str = "条") -> bool:
    """`text[i:]` 处的「第X章/条」是否为**真结构首**（而非内嵌引用）。

    判 False（不切）当且仅当前一字符属于"像引用"的三类：
      连接性标点 / 引号书名号收尾 / 汉字字母数字。
    其余一律判 True（切）——含空白、句末标点、**项目符号与私用区字符**、其他符号。
    """
    if i == 0:
        return True
    p = text[i - 1]
    if p in _CONNECTIVE_BEFORE:
        return False
    if p in _CLOSERS_HARD:
        return False
    if p in _CLOSERS_SOFT:
        # 括号收尾：其后接**章标题**属结构边界（`…的决定》修订)第一章 总则`）；
        # 接「第X条」时须其前为句末标点（`（以下简称贷款人）第三条` 判为引用）。
        return kind == "章" or (i >= 2 and text[i - 2] in _TERMINATORS)
    if p.isspace() or p in _TERMINATORS:
        return True
    return not _CJK_ALNUM_RE.match(p)


def segment_body(body: str) -> str:
    """把正文按 **章/条真起始** 切为多行（供行式解析识别）。

    - 章标题行后强制断行：`第一章 总 则第一条 为了…` → 章行 + `第一条 …`
      （章标题以 CJK 结尾，若只靠 `_prev_allows_head` 会漏切首条 → 由章态豁免）；
    - 内嵌引用不断行（`本法第五十三条、第五十四条规定的行为` 保持同条正文）；
    - 段内二次切分（F1）：章行内粘连的「第X章/第X节」由 `resplit_embedded_anchors` 切开。
    """
    t = body or ""
    if not t.strip():
        return t
    out: list[str] = []
    buf = 0
    after_chapter = False
    for m in _ANCHOR_RE.finditer(t):
        kind = m.group(2)
        if not (_prev_allows_head(t, m.start(), kind) or (after_chapter and kind == "条")):
            continue
        out.append(t[buf:m.start()])
        buf = m.start()
        after_chapter = kind == "章"
    out.append(t[buf:])
    seg = "\n".join(x.strip() for x in out if x.strip())
    return resplit_embedded_anchors(seg, kind="law")


# 层级序号锚（一、/（一）/1、/——），用于非条文体（通知/通报/规划）
_OUTLINE_ANCHOR_RE = re.compile(
    r"(?<=[。！？…；:：\s])("
    r"%s{1,6}[、．.]"                       # 一、
    r"|[（(【\[]\s*%s{1,6}\s*[）)】\]]"      # （一）
    r"|\d{1,3}\s*[、．.](?!\d)"             # 1、/1.（排除小数 1.5）
    r"|[—\-–]{1,2}(?!\d)"                   # ——
    r")" % (_CN_CLS, _CN_CLS))


def segment_outline(text: str) -> str:
    """把正文按**层级序号**（一、/（一）/1、/——）切行，再做**段内二次切分**（F1）。

    本项目语料 body_text 多为**单段无换行**（与参考实现假定的"已换行文本"不同），
    故非条文体解析前须先做锚切分，否则 `^一、` 行式匹配只能命中零星行。
    锚切分要求锚前为句末标点/空白 → 「二、基本原则（一）服务…」中紧贴标题末字的
    `（一）` 漏切 → 由 `resplit_embedded_anchors` 二次切分补足。
    """
    t = text or ""
    if not t.strip():
        return t
    seg = "\n".join(x.strip() for x in _OUTLINE_ANCHOR_RE.sub(r"\n\1", t).split("\n")
                    if x.strip())
    return resplit_embedded_anchors(seg, kind="outline")


_HEAD_SHAPED_BAD = re.compile(r"[。！？，；、：:]")          # law 模式（章标题短名）
_HEAD_SHAPED_BAD_OUTLINE = re.compile(r"[。！？]")          # outline 模式（允许「，、」）
_EMBED_HEAD_MAX = 40
_EMBED_OUTLINE_RE = re.compile(
    r"%s{1,6}[、．.](?!\d)" % _CN_CLS
    + r"|[（(【\[]\s*%s{1,6}\s*[）)】\]]" % _CN_CLS
    + r"|\d{1,3}\s*[、．.](?!\d)")
_EMBED_LAW_RE = re.compile(r"第\s*(?:[0-9]+|%s+)\s*[章节]" % _CN_CLS)
# 交叉引用否证：标记后紧接「项/款/目/条/章/节/个/是」或并列标点 → 内联引用，不切
_EMBED_XREF_AFTER = re.compile(r"^\s*[项款目条章节个是、，]")
_EMBED_MIN_BODY = 20      # 标记之后须有 ≥20 字实质正文
_EMBED_BODY_END = re.compile(r"[。；]")
# 节标题（结构分隔；正文不保留节名，仅留痕计数）
_SECTION_RE = re.compile(r"^第\s*(?:[0-9]+|%s+)\s*节" % _CN_CLS)


def _heading_shaped(seg: str, max_len: int = _EMBED_HEAD_MAX, *, kind: str = "law") -> bool:
    """短标题形态：非空、≤max_len 字、不含句末标点（outline 允许「，、」）。"""
    s = (seg or "").strip()
    if not s or len(s) > max_len:
        return False
    bad = _HEAD_SHAPED_BAD if kind == "law" else _HEAD_SHAPED_BAD_OUTLINE
    return not bad.search(s)


_LAW_REST_BAD = re.compile(r"^\s*[的之和与及等之所中内前后、，。；:：]")


def _law_title_side_ok(rest: str, max_len: int = 60) -> bool:
    """law 模式二次切分右侧（章/节标题本身）形态校验：非虚词残片、无句末标点、不超长。"""
    s = (rest or "").strip()
    return bool(s) and len(s) <= max_len and not _HEAD_SHAPED_BAD.search(s) \
        and not _LAW_REST_BAD.match(s)


def _mk_kind(mk: str) -> str:
    """层级序号标记的层级种类：L1（一、）/ L2（（一））/ L3（1.）。"""
    mk = (mk or "").strip()
    if not mk:
        return ""
    if mk[0] in "（(【[":
        return "L2"
    if mk[0].isdigit():
        return "L3"
    return "L1"


def _resplit_level_ok(lead_mk: str, emb_mk: str) -> bool:
    """F1 层级一致性：只允许切出**更深层级**（或同层但不同号）的序号。

    否证「一、负责一、二类口岸开放或关闭的审查…」（同层同号 = 词组，非层级）；
    「1.」行内不再切分（已是末级）。
    """
    lk, ek = _mk_kind(lead_mk), _mk_kind(emb_mk)
    if lk == "L3":
        return False
    if lk == "L1":
        return ek in ("L2", "L3")
    return ek == "L3" or (ek == "L2" and emb_mk.strip() != lead_mk.strip())


def resplit_embedded_anchors(text: str, *, kind: str = "outline") -> str:
    """段内二次切分（F1）：把**已锚定行**中嵌着的层级序号切到新行。

    动因（2026-09-20）：`_OUTLINE_ANCHOR_RE` 的 lookbehind 要求锚前为句末标点/空白，
    源文本「二、基本原则（一）服务国家战略…」中 `（一）` 紧贴标题末字 → 不切行 →
    整行成为一级节点文本、`（一）` 节点丢失（实测五源 574 处 / 182 份）。
    同类：章标题粘连（「总 则第一章 总 则」「…要求 第一节 产品募集信息披露」）。

    三重判据（防误切，全部为"必须同时满足"）：
      ① **head 短标题形态**：待切标记之前 ≤40 字且不含句末标点（outline 额外允许 `，、`）；
      ② **标记后有实质正文**：≥20 字且含 `。`/`；`；
      ③ **标记后不得是内联引用**：紧接 `项/款/目/条/章/节/个/是/、/，` 即否证。
    law 模式额外要求该行以**章标记**开头（条行内的「第五章」必是引用，禁止切）。
    经否证校核：`负责一、二类口岸…`、`上述(一)、(二)项…`、`本通知第（一）（二）…`、
    `以完善（一）项规定…` 均不切。
    """
    rx = _EMBED_LAW_RE if kind == "law" else _EMBED_OUTLINE_RE
    out: list[str] = []
    for raw in (text or "").split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        cur, guard = ln, 0
        # law 模式只对章行做二次切分（条行内的章/节字样一律视为引用）
        if kind == "law" and not _CHAPTER_RE.match(ln):
            out.append(ln)
            continue
        while guard < 20:
            guard += 1
            head_m = rx.match(cur)
            start = head_m.end() if head_m else 0   # 行首标记本身不计入标题形态判定
            m = rx.search(cur, start)
            if not m:
                break
            rest = cur[m.end():]
            if not _heading_shaped(cur[start:m.start()], kind=kind):
                break
            if kind == "law":
                # 章/节标题形态：标记两侧均须「标题样」（无句末标点、不超长、非虚词残片）
                if not _law_title_side_ok(rest):
                    break
            else:
                if len(rest.strip()) < _EMBED_MIN_BODY or not _EMBED_BODY_END.search(rest):
                    break
                if _EMBED_XREF_AFTER.match(rest):
                    break
                if not _resplit_level_ok(head_m.group(0) if head_m else "", m.group(0)):
                    break
            out.append(cur[:m.start()].strip())
            cur = cur[m.start():].strip()
        out.append(cur)
    return "\n".join(x for x in out if x)


def _to_int(num: str) -> int:
    """中文数字/阿拉伯数字 → int（粗解析，百千内够用）。失败返回 0。"""
    if num.isdigit():
        return int(num)
    table = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
    unit = {"十": 10, "百": 100, "千": 1000}
    total = cur = 0
    for ch in num:
        if ch in table:
            cur = table[ch]
        elif ch in unit:
            if cur == 0:
                cur = 1
            total += cur * unit[ch]
            cur = 0
        else:
            return 0
    return total + cur


def is_article_line(line: str) -> bool:
    return bool(_ARTICLE_RE.match(line.strip()))


def extract_article_no(line: str) -> str:
    m = _ARTICLE_RE.match(line.strip())
    return m.group(0) if m else ""


def is_chapter_line(line: str) -> bool:
    return bool(_CHAPTER_RE.match(line.strip()))


def _drop_toc_chapters(chapters: list[dict]) -> list[dict]:
    """剔除目录页残留章标题（2026-09-08 增补）。

    带目录文档中，目录区先罗列全部章标题（无任何条文跟随），随后正文再出现各章。
    特征：章号重复出现——保留每组重复章中的**最晚**者（正文在文档后部，且其
    article_index 指向真实条文区间），其前同名目录章删除。无重复章号的文档不受影响。
    """
    if not chapters:
        return chapters
    seen_no = {}
    for i, ch in enumerate(chapters):
        seen_no.setdefault(ch.get("no"), []).append(i)
    dup_no = {no for no, idxs in seen_no.items() if len(idxs) > 1}
    if not dup_no:
        return chapters
    # 保留全部非重复章 + 每重复组的最晚者
    drop = {i for no, idxs in seen_no.items()
            if no in dup_no for i in idxs[:-1]}
    return [ch for i, ch in enumerate(chapters) if i not in drop]


# --------------------------------------------------------------------------- #
# 二、法条合并修复（参考 `5.1 行合并修复`）
# --------------------------------------------------------------------------- #
_XREF_HEAD_RE = re.compile(r"^第\s*([0-9]+|%s+)\s*条" % _CN_CLS)
# 续行开头特征（剥离「第X条」前缀后的正文）——**纯分隔符 / 回溯指代**。
# 刻意**不含** `前款`/`本款`/`以上`：它们常出现在**真实条文**开头（如"第二十条 前款规定的…"）。
# 真实条文正文不会以 `、`/`的`/`规定` 起首——这正是不误并嵌套文档首条的判据。
_BACKREF_RE = re.compile(r"^(?:[、，,；;。：:）)】\]]|的|规定)")
# 连接性结尾（上一条以这些字符收尾 ⇒ 下一条是续行而非新条）
_CONNECTIVE_TAIL = "、，,;；及和与至的"


def _merge_reason(content: str, is_dup: bool, is_xref: bool, prev_body: str) -> str | None:
    """判定该「条」应收编为**上一条的续行**（返回原因）还是**独立条文**（返回 None）。

    依据（全部来自实测案例）：
      - `fragment`      ：空 / 极短且无句号 → 残片；
      - `backref`       ：以纯分隔符或 `的`/`规定` 起首 → 回溯指代（真实条文不如此开头）；
      - `xref_no_body`  ：以「第X条」起首且无实质内容（参考 `is_cross_ref and not has_content`）；
      - `prev_connective`：条号重复或以「第X条」起首，**且上一条以连接符收尾**
                          （参考未覆盖：`…本法第五十四条、` + `第五十六条规定进行处罚`）。
    不命中即独立条文——**嵌套文档首条**（如 `第一条 为了规范…，制定本办法。`）由此保全。
    """
    c = (content or "").strip()
    if not c:
        return "fragment"
    if len(c) <= 20 and "。" not in c:
        return "fragment"
    if _BACKREF_RE.match(c):
        return "backref"
    has_content = len(c) > 20 and "。" in c
    if is_xref and not has_content:
        return "xref_no_body"
    if (is_dup or is_xref) and (prev_body or "").rstrip().endswith(tuple(_CONNECTIVE_TAIL)):
        return "prev_connective"
    return None


def _is_continuation(content: str) -> bool:
    """该「条」正文是否为**续行形态**（供外部诊断；判定走 `_merge_reason`）。"""
    return _merge_reason(content, False, False, "") is not None


def repair_articles(articles: list[dict]) -> tuple[list[dict], dict]:
    """合并「换行截断产生的伪条」，返回 (清洗后条文, 统计)。

    伪条判据（参考 `ArticleMerger.merge` 语义 + 本项目"续行判据"加固；`content` 指剥离
    「第X条」前缀后的正文）：
      - `is_duplicate`：该条号已出现过（交叉引用行被当成新条 → 条号撞车）；或
      - `is_cross_ref`：正文本身以「第X条」开头（引用续写形态）；
      **且** `_is_continuation(content)` 成立（回溯指代/纯分隔符/极短残片）。
    命中即**并入上一条**（拼接正文），使被换行切断的句子复原。

    ⚠️ 加固动因（与参考的差异，依据）：参考**仅凭条号重复即合并**，遇到"一份文件内嵌套另一份
    完整文件"（如《…关于修改〈X办法〉的决定》把被修订办法全文附后，条号从 1 重启）会把
    嵌套正文**整块并入主文件**（实测 mof 两份共并掉 104 条，内容错位）。
    故增加续行判据：嵌套文档的首条（如"第一条 为了规范…，制定本办法。"）不匹配回溯指代，
    不合并 → **保留**（条号重复作为"多文档嵌套"信号交 V003 报出，不静默改写）。

    可追溯：统计含 `merged / merged_nos / reasons`，写入产物 `parse_meta.repair`。
    真源（cleaned `body_text`）不受影响，误并可由该统计回溯（clause 产物是派生索引）。
    """
    if not articles:
        return articles, {"merged": 0, "merged_nos": [], "reasons": []}
    out: list[dict] = []
    seen: set = set()
    stat = {"merged": 0, "merged_nos": [], "reasons": []}
    for art in articles:
        no = art.get("no")
        content = _article_body(art.get("body") or "")
        is_dup = no in seen
        is_xref = bool(_XREF_HEAD_RE.match(content))
        reason = (_merge_reason(content, is_dup, is_xref, out[-1].get("body") or "")
                  if out else None)
        if reason:
            prev = out[-1]
            # ⚠️ 拼接**完整 body**（含伪条自带的「第X条」），而非剥离后的 content ——
            # 本项目 body 以条号起首，剥离会丢掉原文中的那个「第X条」字样
            # （如 `…第五十三条、` + `第五十四条规定的行为…` 必须保留"第五十四条"）。
            prev["body"] = (prev.get("body") or "").rstrip() + (art.get("body") or "").strip()
            stat["merged"] += 1
            stat["merged_nos"].append(no)
            stat["reasons"].append(reason)
            continue
        out.append(art)
        seen.add(no)
    return out, stat


def dedup_articles(articles: list[dict]) -> tuple[list[dict], dict]:
    """编号去重（参考 `5.2 编号去重与重排` 的**去重部分**）。

    与参考的差异：**不重排、不重铸 `number`**（依据见模块 docstring 取舍节）。
    且**只丢弃「同条号 + 同正文」的完全重复**（真冗余）；「同条号但正文不同」属
    **多文档嵌套**（条号重启），保留全文并由 V003 报出（不静默丢数据）。
    """
    seen: set = set()
    out: list[dict] = []
    dropped: list = []
    kept_dup: list = []
    for art in articles:
        key = (art.get("no"), (art.get("body") or "").strip())
        if key in seen:
            dropped.append(art.get("no"))
            continue
        seen.add(key)
        out.append(art)
    nos = [a.get("no") for a in out]
    kept_dup = sorted({n for n in nos if nos.count(n) > 1})
    return out, {"deduplicated": len(dropped), "dropped_nos": dropped,
                 "restart_nos": kept_dup}


# --------------------------------------------------------------------------- #
# 三、章节映射校验与修复（参考 `5.3 章节映射校验与修复`）
# --------------------------------------------------------------------------- #
def fix_chapter_index(chapters: list[dict], articles: list[dict]) -> tuple[list[dict], dict]:
    """修正 `chapters[].article_index` 越界（参考实现语义：越界钳到有效范围）。

    本项目补充：同时记录**非单调**（后章索引小于前章）——只记录不修改，交由 V005 报出
    （静默改写章节边界会掩盖解析缺陷）。
    """
    stat = {"clamped": 0, "non_monotonic": []}
    if not chapters or not articles:
        return chapters, stat
    last = len(articles) - 1
    prev = -1
    for ch in chapters:
        idx = ch.get("article_index", 0)
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            # ⚠️ 只钳位 + 计数，**不得写入额外键**——`chapters[]` 键集由
            # `contract.CLAUSE_CHAPTER_FIELDS` 固定（{no,title,article_index}），
            # 多键会被 `clause_index.validate_schema` 判为"键漂移"。
            ch["article_index"] = max(0, min(idx if isinstance(idx, int) else 0, last))
            stat["clamped"] += 1
        if ch["article_index"] < prev:
            stat["non_monotonic"].append(ch.get("no"))
        prev = ch["article_index"]
    return chapters, stat


# --------------------------------------------------------------------------- #
# 四、解析器（law 主模式 + 层级体降级 + 纯段兜底）
# --------------------------------------------------------------------------- #
def _parse_law(text: str) -> dict:
    """法令体：第X章 / 第X节 / 第X条（本项目主模式）。"""
    empty = {"chapters": [], "articles": [], "structure": [], "tail_marker": "",
             "sections": []}
    if not text or not text.strip():
        return empty
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    chapters: list[dict] = []
    articles: list[dict] = []
    tail_marker = ""
    sections: list[str] = []
    cur: dict | None = None

    def _flush_article():
        nonlocal cur
        if cur is not None:
            articles.append({"no": cur["no"], "number": cur["number"],
                             "body": "\n".join(cur["_buf"]).strip()})
            cur = None

    for ln in lines:
        if _HEADER_DROP.match(ln) or _TAIL_RE.match(ln):
            # 2026-09-20（F4 联动修复）：附件/附则/目录行**只跳过、不再 break**。
            # 动因：段落边界保真后这些标记各自成行（旧实现依赖"整篇被折叠为单行"，
            # 故几乎不命中）。原 break 语义会在首个附件行处终止整篇解析：
            #   · 文档标题行「关于印发《…》的通知」命中 `^关于印发.*的通知$` → 首条前即中断
            #     （gov《…企业国有资产交易规则》99 条 → 0 条、66 条 → 0 条）；
            #   · 「修改决定」类文档的附件内嵌被修订全文 → 在首个附件行处截断
            #     （gov《关于修改部分证券期货规范性文件的决定》835 条 → 45 条）。
            # 现语义：跳过标记行（不并入正文），继续按章/条解析 —— 与"多文档嵌套不得
            # 静默丢弃"（V003 报出）的既有纪律一致；**尾部噪声**由 F3 `strip_document_tail`
            # 在文档级按尾区截断负责，两者职责互补。
            tail_marker = tail_marker or ln
            continue
        if _SECTION_RE.match(ln):
            # 节标题 = 结构分隔（2026-09-20 相邻项修复）：原实现「节不切」→
            # 「第二节 风险管理」被并入上一条正文（条款内混入结构标题）。
            # 现：flush 当前条文、丢弃节名（chapters 键集恒定，节不入 chapters），
            # 仅计数留痕（parse_meta.repair.sections_dropped），可回溯。
            _flush_article()
            sections.append(ln[:24])
            continue
        cm = _CHAPTER_RE.match(ln)
        if cm:
            _flush_article()
            ch_title = ln[cm.end():].strip()
            # 净化为「章名」，剥离章首页版式点线与页码（如「基本管理....1」→「基本管理」）
            ch_title = re.sub(r"[.。·•…\s]{2,}\d*\s*$", "", ch_title).strip()
            chapters.append({"no": _to_int(cm.group(1)) or len(chapters) + 1,
                             "title": ch_title,
                             "article_index": len(articles)})
            continue
        am = _ARTICLE_RE.match(ln)
        if am:
            _flush_article()
            cur = {"no": _to_int(am.group(1)) or len(articles) + 1,
                   "number": am.group(0), "_buf": [ln]}
            continue
        if cur is not None:
            cur["_buf"].append(ln)
        # 章标题后的章总述/条文间过渡文本（非章非条且无 open article）——忽略（结构噪声）
    _flush_article()
    chapters = _drop_toc_chapters(chapters)
    return {"chapters": chapters, "articles": articles, "structure": [],
            "tail_marker": tail_marker[:20], "sections": sections}


# 层级体各模式的补充模式（与参考 BulletinParser / PlanParser 的差异点一致）
_OUTLINE_EXTRA = {
    "notice": (),
    "bulletin": (("三是", re.compile(r"^([一二三四五六七八九十]{1,3})是\s*(.*)$")),),
    "plan": (("破折", re.compile(r"^[—\-–]{1,2}\s*(.*)$")),),
}
_OUTLINE_L1_RE = re.compile(r"^([%s]{1,6})[、．.]\s*(.*)$" % _CN)
_OUTLINE_L2_RE = re.compile(r"^[（(【\[]\s*([%s]{1,6})\s*[）)】\]]\s*(.*)$" % _CN)
_OUTLINE_NUM_RE = re.compile(r"^(\d{1,3})\s*[、．.]\s*(.*)$")


def _node(level: str, number: str, title: str = "") -> dict:
    """层级节点（**键集恒定**，便于下游消费与校验）。"""
    return {"level": level, "number": number, "title": title,
            "content": "", "items": [], "children": []}


# 标题形态（F2）：≤30 字且不含句末标点/逗号类标点
_NODE_TITLE_MAX = 30
_NODE_TITLE_BAD = re.compile(r"[。！？，；、：:]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 中文标点（拼接时两侧不插空格）
_CJK_PUNCT = "，。！？；：、（）《》【】「」“”‘’…—～·"


def _split_node_title_body(rest: str, max_len: int = _NODE_TITLE_MAX) -> tuple[str, str]:
    """层级节点「marker 之后的文本」→ `(title, content)`（F2，2026-09-20）。

    动因：原实现把 marker 后**整行剩余文本**无条件写入 `title`（`:459`/`:463`），
    于是「（一）总体目标 借鉴国际金融监管…」整段正文变成标题，
    「五、发行人应充分、及时披露…」本无标题的条款也被塞进 `title`
    （实测五源 81,489 处 / 7,923 份，nfra 10,396 处）。

    判据：候选①=marker 后整段；候选②=首个空白前的片段（清洗层把源端换行折叠为空格、
    或源端本就「标题 正文」同行）。取**首个标题形态**者为 `title`，其余为 `content`；
    都不成形则 `title=""`、全部作为正文 —— **不臆造标题**。
    """
    s = (rest or "").strip()
    if not s:
        return "", ""
    cands = [s]
    m = re.search(r"[ \u00a0\u2002\u2003\u3000]", s)
    if m and m.start() > 0:
        cands.append(s[:m.start()].strip())
    for c in cands:
        if c and len(c) <= max_len and not _NODE_TITLE_BAD.search(c):
            return c, s[len(c):].strip()
    return "", s


def _join_para(a: str, b: str) -> str:
    """段落拼接（中文边界不插空格，避免清洗注入型空格回流到 content）。

    2026-09-20（F5a 联动）：**标点边界也不插空格**——否则段落保真后逐段拼接会把
    「…评估，」+「并履行…」拼成「…评估， 并履行…」（实测五源残留 3,622 处
    「标点+空格」，与问题三"无效空格"同源）。
    """
    a, b = (a or "").strip(), (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    if _CJK_RE.match(a[-1]) and _CJK_RE.match(b[0]):
        return a + b
    if a[-1] in _CJK_PUNCT or b[0] in _CJK_PUNCT:
        return a + b
    return a + " " + b



def _parse_outline(text: str, mode: str) -> dict:
    """层级序号体（notice / bulletin / plan）：一、/（一）/1、[+ X是 / ——] → structure。

    序号**原样保留**（不重标为「第X条」，依据见模块 docstring）。
    """
    empty = {"chapters": [], "articles": [], "structure": [], "tail_marker": ""}
    if not text or not text.strip():
        return empty
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    extras = _OUTLINE_EXTRA.get(mode, ())
    structure: list[dict] = []
    l1 = l2 = None
    for ln in lines:
        m1 = _OUTLINE_L1_RE.match(ln)
        m2 = _OUTLINE_L2_RE.match(ln)
        mn = _OUTLINE_NUM_RE.match(ln)
        mx = None
        for _name, rx in extras:
            mx = rx.match(ln)
            if mx:
                break
        if m1:
            _t, _c = _split_node_title_body(m1.group(2))
            l1 = _node("一级", m1.group(1), _t)
            if _c:
                l1["content"] = _c
            structure.append(l1)
            l2 = None
        elif m2:
            _t, _c = _split_node_title_body(m2.group(2))
            l2 = _node("二级", f"（{m2.group(1)}）", _t)
            if _c:
                l2["content"] = _c
            if l1 is not None:
                l1["children"].append(l2)
            else:
                structure.append(l2)
        elif mn or mx is not None:
            if mn:
                item = {"number": mn.group(1), "content": mn.group(2).strip()}
            else:
                grp = mx.groups()
                item = ({"number": f"{grp[0]}是", "content": (grp[1] or "").strip()}
                        if len(grp) == 2
                        else {"number": "——", "content": (grp[0] or "").strip()})
            tgt = l2 if l2 is not None else l1
            if tgt is not None:
                tgt["items"].append(item)
        else:
            tgt = l2 if l2 is not None else l1
            if tgt is not None:
                tgt["content"] = _join_para(tgt["content"], ln)
    return {"chapters": [], "articles": [], "structure": structure, "tail_marker": "",
            "sections": []}


def _parse_plain(text: str) -> dict:
    """纯段兜底：**不物化段落**（正文全文已由 cleaned 承载），仅记段落数。"""
    n = len([ln for ln in (text or "").split("\n") if ln.strip()])
    return {"chapters": [], "articles": [], "structure": [], "tail_marker": "",
            "plain_paragraphs": n}


# --------------------------------------------------------------------------- #
# 四之二、条内层级（F6，2026-09-20）：条 → 项（（一））→ 目（1.）
# --------------------------------------------------------------------------- #
_ITEM_L2_RE = re.compile(r"^[（(【\[]\s*([%s]{1,6})\s*[）)】\]]\s*(.*)$" % _CN)
_ITEM_L3_RE = re.compile(r"^(\d{1,3})\s*[.．、]\s*(.*)$")
# 仅在**句末标点/冒号之后**切（`（一）`/`1.` 的开头形态），避免切到小数与行内引用
_ITEM_CUT_L2 = re.compile(r"(?<=[。；：:])[（(【\[]\s*([%s]{1,6})\s*[）)】\]]" % _CN)
_ITEM_CUT_L3 = re.compile(r"(?<=[。；：:])(\d{1,3})\s*[.．、](?!\d)")
_ITEM_ANY_L2 = re.compile(r"[（(【\[]\s*[%s]{1,6}\s*[）)】\]]" % _CN)


def _parse_item_nodes(body: str) -> tuple[list[dict], str]:
    """条文 body → `(项节点列表, 引导段)`；项内含「目」挂 `items[]`。

    序号**原样保留**（`（一）` / `1`），不重标为「第X条」；键集 = `_node()` 恒定。
    """
    t = (body or "").strip()
    m0 = _ARTICLE_RE.match(t)
    if m0:
        t = t[m0.end():].strip()
    t = _ITEM_CUT_L2.sub(r"\n（\1）", t)
    t = _ITEM_CUT_L3.sub(r"\n\1.", t)
    items: list[dict] = []
    lead: list[str] = []
    cur2: dict | None = None
    for raw in t.split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        m2 = _ITEM_L2_RE.match(ln)
        m3 = _ITEM_L3_RE.match(ln)
        if m2:
            cur2 = _node("项", "（%s）" % m2.group(1))
            cur2["content"] = m2.group(2).strip()
            items.append(cur2)
        elif m3 and cur2 is not None:
            sub = _node("目", m3.group(1))
            sub["content"] = m3.group(2).strip()
            cur2["items"].append(sub)
        elif cur2 is not None:
            cur2["content"] = _join_para(cur2["content"], ln)
        else:
            lead.append(ln)
    return items, "\n".join(lead).strip()


def parse_article_structure(articles) -> list[dict]:
    """条文列表 → 行级 `article_structure`（F6）：每个**含条内层级**的条文一个「条」节点。

    只对命中 `（X）` 的条文产出（五源实测 17,795 条 / 107,520 条），避免产物体积翻倍。
    无条内层级的条文不产出节点（`articles[].body` 已承载原文，不重复）。
    """
    out: list[dict] = []
    for art in articles or []:
        body = art.get("body") or ""
        if not _ITEM_ANY_L2.search(body):
            continue
        items, lead = _parse_item_nodes(body)
        if not items:
            continue
        node = _node("条", (art.get("number") or "").strip())
        node["content"] = lead
        node["items"] = items
        out.append(node)
    return out


_MODES: tuple[tuple[str, int], ...] = (("notice", 2), ("bulletin", 3), ("plan", 4))
_THRESHOLD = 0.75
_WEIGHTS = {"coverage": 0.5, "legality": 0.3, "continuity": 0.2}
# 结构行模式（参考 ModeScorer._is_structure_line）
_STRUCT_LINE_RES = (
    re.compile(r"第[%s]{1,6}[编章节条]" % _CN),
    re.compile(r"^[%s]{1,6}[、．.]" % _CN),
    re.compile(r"^[（(][%s]{1,6}[）)]" % _CN),
    re.compile(r"^[一二三四五六七八九十]+是"),
    re.compile(r"^[—\-–]{1,2}"),
    re.compile(r"^\s*\d+\s*[\.、．]"),
)


def _is_structure_line(line: str) -> bool:
    return any(rx.match(line) for rx in _STRUCT_LINE_RES)


def score_parse(result: dict, text: str) -> dict:
    """模式评分（参考 `ModeScorer`）：coverage 0.5 / legality 0.3 / continuity 0.2。

    适配点：
      - **continuity 取该模式自身结构单元的序号连续性**——law 用 `articles[].no`；
        层级体（无 articles）用 `structure` 一级节点序号。参考对"无 articles"一律给 0，
        会使层级体恒低于阈值而失去降级意义；
      - **coverage 计入"结构单元续行"**（2026-09-20，F4 联动）：段落边界保真后一行即
        一整段正文（旧实现被折叠为单行/句级行），若只认锚行则 coverage 被人为压低，
        大量真实通知类文档跌破阈值退化为 `plain`（实测 notice 8,154 → 4,950、
        plain 5,853 → 9,069）。现口径：锚行 + 其后续非锚行均计入覆盖（前者为结构首、
        后者归属该结构单元）；未被任何结构单元覆盖的**前导行**（标题/前言）不计。
    """
    lines = [ln for ln in (text or "").split("\n") if ln.strip()]
    matched = 0
    pending = False
    for ln in lines:
        if _is_structure_line(ln):
            matched += 1
            pending = True
        elif pending:
            matched += 1
    coverage = matched / len(lines) if lines else 0.0

    nodes = 0
    legal = 0
    for ch in result.get("chapters") or []:
        nodes += 1
        legal += 1 if ch.get("title") is not None else 0
    for a in result.get("articles") or []:
        nodes += 1
        legal += 1 if (a.get("no") and a.get("body")) else 0
    for n in _iter_nodes(result.get("structure") or []):
        nodes += 1
        legal += 1 if n.get("number") else 0
    legality = legal / nodes if nodes else 0.0

    nums: list[int] = []
    if result.get("articles"):
        nums = [a.get("no", 0) for a in result["articles"] if isinstance(a.get("no"), int)]
    else:
        nums = [_to_int(n.get("number", "")) for n in (result.get("structure") or [])
                if n.get("level") == "一级"]
        nums = [x for x in nums if x > 0]
    if len(nums) <= 1:
        continuity = 1.0 if nums else 0.0
    else:
        s = sorted(nums)
        continuity = sum(1 for i in range(1, len(s)) if s[i] == s[i - 1] + 1) / (len(s) - 1)
    total = (_WEIGHTS["coverage"] * coverage + _WEIGHTS["legality"] * legality
             + _WEIGHTS["continuity"] * continuity)
    return {"total": round(total, 4), "coverage": round(coverage, 4),
            "legality": round(legality, 4), "continuity": round(continuity, 4)}


def _iter_nodes(nodes):
    for n in nodes:
        yield n
        # R7（2026-09-26）：`yield from` 替代 for+yield（等价且更简洁）
        yield from n.get("children") or []


def parse_document(text: str) -> dict:
    """**自动降级解析主入口**（参考 `AutoDegradeParser.parse` 适配）。

    返回（**扁平超集**，兼作 `extract_structure` 的返回形态）：
      {mode, score, all_scores, is_fallback, chapters, articles, chapter_count,
       article_count, structure, structure_count, plain_paragraphs, method,
       tail_marker, repair, tail_cut, article_structure}

    2026-09-20 增补（问题驱动）：
      - 入口先做 `normalize_parse_text`（F5a 异常空白/注入空格归一，只读）与
        `strip_document_tail`（F3 文档级尾部截断），命中信息落 `tail_cut`；
      - `article_structure`（F6）：law 模式的条内层级（项/目）。
    """
    empty = {"mode": "empty", "score": {"total": 0.0}, "all_scores": {},
             "is_fallback": True, "chapters": [], "articles": [], "chapter_count": 0,
             "article_count": 0, "structure": [], "structure_count": 0,
             "plain_paragraphs": 0, "method": "regex", "tail_marker": "",
             "threshold": _THRESHOLD, "tail_cut": {}, "article_structure": [],
             "repair": {"merged": 0, "merged_nos": [], "reasons": []}}
    if not text or not text.strip():
        return empty

    text = normalize_parse_text(text)               # F5a：解析输入归一（只读）
    text, tail_cut = strip_document_tail(text)      # F3：文档级尾部截断
    if not text.strip():
        return {**empty, "tail_cut": tail_cut}

    law_text = segment_body(text)
    law = _parse_law(law_text)
    arts, rep = repair_articles(law["articles"])
    arts, ded = dedup_articles(arts)
    if ded["deduplicated"]:
        rep["deduplicated"] = ded["deduplicated"]
    chapters, chfix = fix_chapter_index(law["chapters"], arts)
    if chfix["clamped"]:
        rep["chapter_clamped"] = chfix["clamped"]
    if law.get("sections"):
        rep["sections_dropped"] = len(law["sections"])
    law_score = score_parse({"chapters": chapters, "articles": arts}, law_text)
    all_scores = {"law": law_score["total"]}

    def _pack(mode: str, res: dict, score: dict, is_fallback: bool) -> dict:
        arts_ = res["articles"]
        return {"mode": mode, "score": score, "all_scores": all_scores,
                "is_fallback": is_fallback,
                "chapters": res["chapters"], "articles": arts_,
                "chapter_count": len(res["chapters"]), "article_count": len(arts_),
                "structure": res["structure"], "structure_count": len(res["structure"]),
                "plain_paragraphs": res.get("plain_paragraphs", 0),
                "method": "regex", "tail_marker": res.get("tail_marker", ""),
                "threshold": _THRESHOLD, "repair": rep, "tail_cut": tail_cut,
                "article_structure": parse_article_structure(arts_)}

    # ① 主模式（law）一旦出条即采用 —— 保证既有法规文档解析零回归
    if arts:
        return _pack("law", {"chapters": chapters, "articles": arts,
                             "structure": [], "tail_marker": law["tail_marker"]},
                     law_score, False)

    # ② 无条文 → 自动降级：层级体（通知/通报/规划）取分择优
    out_text = segment_outline(text)
    cands = []
    for name, prio in _MODES:
        res = _parse_outline(out_text, name)
        if not res["structure"]:
            continue
        sc = score_parse(res, out_text)
        all_scores[name] = sc["total"]
        cands.append({"mode": name, "res": res, "score": sc, "prio": prio})
    if cands:
        cands.sort(key=lambda x: (-x["score"]["total"], x["prio"]))
        best = cands[0]
        if best["score"]["total"] >= _THRESHOLD:
            return _pack(best["mode"], best["res"], best["score"], False)

    # ③ 兜底：纯段（不物化段落，仅计数）
    plain = _parse_plain(text)
    plain_score = score_parse(plain, text)
    all_scores["plain"] = plain_score["total"]
    return _pack("plain", plain, plain_score, True)


# --------------------------------------------------------------------------- #
# 五、条款校验器（参考 `3.1 校验规则清单` + `3.2 核心校验函数` + `3.3 校验输出格式`）
#      + 2026-09-20 结构语义体检（F7：V008–V010）
# --------------------------------------------------------------------------- #
# 结构语义体检判据（唯一实现；clause_index.validate_schema 与 V008–V010 共用）
_STRUCT_SWALLOW_RE = re.compile(
    r"^[^。！？，；：]{1,40}(?:[（(][%s]{1,3}[）)]|[%s]{1,3}[、．.](?!\d)).{30,}" % (_CN, _CN))
_SPACE_CONTAM_RE = re.compile(
    r"[，；、：,][ \u00a0\u2002\u3000](?=[\u4e00-\u9fff0-9])|[\u00a0\u2002\u3000\u200b\ufeff]")
_ITEM_L2_ANY = re.compile(r"[（(【\[]\s*[%s]{1,6}\s*[）)】\]]" % _CN)


def structure_semantics(row: dict) -> dict:
    """结构语义体检（2026-09-20 F7）：一次遍历给出四类计数，供 V008–V010 与产物自检共用。

    返回 `{swallowed, tail, space, items}`：
      swallowed：`structure` 节点 title 内嵌层级序号（短标题段 + 序号 + ≥30 字正文）——
                 即 2026-09-20 问题一"子层级被吞进父级标题"的判据；
      tail     ：条文/节点文本仍混入 `附：`/`答记者问`/`此件发至`/「机关名+日期」署名
                 （问题三-bis、问题四判据）；
      space    ：文本含「标点 + 空格」或异常空白（NBSP/U+2002/U+3000/零宽）（问题三判据）；
      items    ：**未抽取条内层级**的条文数（body 含 `（X）` 但无对应 `article_structure`
                 节点）——F6 落地后应趋近 0。
    """
    n = {"swallowed": 0, "tail": 0, "space": 0, "items": 0}
    astr_nos = {(nd.get("number") or "") for nd in (row.get("article_structure") or [])}

    def _scan(text: str) -> None:
        if not text:
            return
        if any(rx.search(text) for rx in _TAIL_INLINE_RES) or _TAIL_WEB_RE.search(text):
            n["tail"] += 1
        else:
            m = _TAIL_SIGN_RE.search(text)
            if m and _TAIL_TAIL_OK.match(text[m.end():]):
                n["tail"] += 1
        if _SPACE_CONTAM_RE.search(text):
            n["space"] += 1

    def _walk(nodes, *, structural: bool) -> None:
        for nd in nodes:
            if structural and _STRUCT_SWALLOW_RE.match(nd.get("title") or ""):
                n["swallowed"] += 1
            _scan(nd.get("title") or "")
            _scan(nd.get("content") or "")
            for it in nd.get("items") or []:
                _scan(it.get("content") or "")
            _walk(nd.get("children") or [], structural=structural)

    _walk(row.get("structure") or [], structural=True)
    _walk(row.get("article_structure") or [], structural=False)
    for a in row.get("articles") or []:
        body = a.get("body") or ""
        _scan(body)
        if _ITEM_L2_ANY.search(body) and (a.get("number") or "") not in astr_nos:
            n["items"] += 1
    return n


def _sem_of(row: dict) -> dict:
    """取校验期缓存的语义体检结果（`validate_clauses` 单次遍历注入），缺失即现算。"""
    return row.get("_semantics") or structure_semantics(row)


def _check_structure_integrity(row: dict):
    sem = _sem_of(row)
    if sem["swallowed"]:
        return (False, f"{sem['swallowed']} 个结构节点的标题内嵌层级序号（子层级被吞，"
                       f"层级未展开）", "WARN")
    return True, "结构层级完整", None


def _check_tail_contamination(row: dict):
    sem = _sem_of(row)
    if sem["tail"]:
        return False, f"{sem['tail']} 处文本混入附件/答记者问/发文署名（尾部未截断）", "WARN"
    return True, "无尾部污染", None


def _check_space_contamination(row: dict):
    sem = _sem_of(row)
    if sem["space"]:
        return False, f"{sem['space']} 处文本含无效空格/异常空白（NBSP 等）", "WARN"
    return True, "无空白污染", None

# 身份字段（空 ⇒ 产物不可用）；其余 4 项为源数据属性（空 ⇒ WARN，依据见模块 docstring）
_REQUIRED_STRICT = ("dedup_key", "title")
_REQUIRED_SOFT = ("source_url", "document_number", "publish_date", "effective_date")
_RFN_RE = re.compile(r"^RFN-[0-9a-f]{16}$")
_MAX_ISSUES = 6


def _check_required_fields(row: dict):
    miss = [k for k in _REQUIRED_STRICT if not str(row.get(k) or "").strip()]
    if miss:
        return False, f"必需字段为空：{miss}", "ERROR"
    soft = [k for k in _REQUIRED_SOFT if not str(row.get(k) or "").strip()]
    if soft:
        return False, f"源数据字段为空：{soft}", "WARN"
    return True, "必需字段非空", None


def _restart_positions(nos: list) -> list[int]:
    """条号回退（重启）位置：一份文件内嵌套另一份完整文件时的特征。"""
    return [i for i in range(1, len(nos)) if nos[i] <= nos[i - 1]]


def _check_number_continuity(row: dict):
    arts = row.get("articles") or []
    if not arts:
        return True, "无条款，跳过", None
    nos = [a.get("no") for a in arts]
    if any(not isinstance(n, int) for n in nos):
        return False, f"条号非整数：{nos[:5]}", "ERROR"
    bad = _restart_positions(nos)
    if bad:
        restart = [i for i in bad if nos[i] == 1]
        if len(restart) == len(bad):
            return (False, f"条号重启 {len(restart)} 处（疑似多文档嵌套：主文件 + 被修订/附件全文），"
                           f"位置[{bad[:5]}]——已保留全部正文，未静默改写", "ERROR")
        return False, f"编号非单调递增：位置[{bad[:5]}] 如 {nos[bad[0]]} <= {nos[bad[0] - 1]}", "ERROR"
    expect = list(range(nos[0], nos[0] + len(nos)))
    if nos != expect:
        miss = sorted(set(range(min(nos[0], 1), max(nos) + 1)) - set(nos))[:5]
        return (False, f"编号不连续（真实跳号）：期望第{expect[0]}条起连续，"
                       f"缺 {miss}", "WARN")
    if nos[0] != 1:
        return False, f"编号不连续：期望第1条，实际第{nos[0]}条", "WARN"
    return True, "编号连续", None


def _check_number_uniqueness(row: dict):
    arts = row.get("articles") or []
    seen: dict = {}
    dup: list = []
    for a in arts:
        no = a.get("no")
        if no in seen:
            dup.append(no)
        seen[no] = True
    if dup:
        if set(dup) == {1}:
            return (False, f"条号重启（多文档嵌套）：第1条出现 {len(dup) + 1} 次；"
                           "已保留全部正文，人工确认是否需拆分为独立文件", "ERROR")
        return False, f"重复编号：{sorted(set(dup))[:8]}", "ERROR"
    return True, "编号唯一", None


def _check_count_consistency(row: dict):
    declared = row.get("article_count", 0)
    actual = len(row.get("articles") or [])
    if declared != actual:
        return False, f"声明{declared}条，实际{actual}条", "WARN"
    sc_declared = row.get("structure_count")
    if sc_declared is not None and sc_declared != len(row.get("structure") or []):
        return False, f"声明{sc_declared}个结构单元，实际{len(row.get('structure') or [])}", "WARN"
    return True, "计数一致", None


def _check_chapter_mapping(row: dict):
    chapters = row.get("chapters") or []
    articles = row.get("articles") or []
    errors: list[str] = []
    prev = -1
    for ch in chapters:
        idx = ch.get("article_index", 0)
        if not isinstance(idx, int):
            errors.append(f"第{ch.get('no')}章索引非整数")
        elif articles and (idx < 0 or idx >= len(articles)):
            errors.append(f"第{ch.get('no')}章索引{idx}越界")
        elif idx < prev:
            errors.append(f"第{ch.get('no')}章索引{idx}小于前章{prev}（章节边界非单调）")
        prev = idx if isinstance(idx, int) else prev
    if errors:
        return False, "; ".join(errors[:3]), "ERROR"
    return True, "章节映射合法", None


def _check_body_not_empty(row: dict):
    empty = [a.get("no") for a in (row.get("articles") or [])
             if not _article_body(a.get("body") or "").strip()]
    if empty:
        return False, f"正文为空：{empty[:8]}", "WARN"
    return True, "正文非空", None


def _check_rfn_valid(row: dict):
    rfn = str(row.get("rfn") or "").strip()
    if not rfn:
        return False, "RFN 未登记（可经 reconcile/桥表回填）", "WARN"
    if not _RFN_RE.match(rfn):
        return False, f"RFN 形态非法：{rfn!r}", "WARN"
    return True, "RFN 有效", None


# (规则ID, 规则名称, 检查函数, 默认严重度) —— 参考 `ClauseValidator.RULES`
CLAUSE_VALIDATION_RULES = (
    ("V001", "必需字段非空", _check_required_fields, "ERROR"),
    ("V002", "编号连续性", _check_number_continuity, "ERROR"),
    ("V003", "编号唯一性", _check_number_uniqueness, "ERROR"),
    ("V004", "计数一致性", _check_count_consistency, "WARN"),
    ("V005", "章节映射合法性", _check_chapter_mapping, "ERROR"),
    ("V006", "正文非空", _check_body_not_empty, "WARN"),
    ("V007", "RFN有效性", _check_rfn_valid, "WARN"),
    # 2026-09-20 F7：结构语义三条（原 V001–V007 不读 structure 语义 → 问题一静默通过）
    ("V008", "结构层级完整性", _check_structure_integrity, "WARN"),
    ("V009", "尾部污染", _check_tail_contamination, "WARN"),
    ("V010", "空白污染", _check_space_contamination, "WARN"),
)


def validate_clauses(row: dict) -> dict:
    """条款校验（参考 `3.3 校验输出格式`）：返回 {status, issues, error_count, warn_count}。

    `status`：有 ERROR → FAILED；仅 WARN → PASSED_WITH_WARNINGS；否则 PASSED。
    `issues` 最多保留 `_MAX_ISSUES` 条（ERROR 优先）——全量计数另由 error/warn_count 给出，
    避免产物体积被 WARN 噪声放大。

    2026-09-20（F7）：结构语义体检（`structure_semantics`）**单次遍历**后经临时键
    `_semantics` 供 V008–V010 复用；`finally` 中移除，保证产物行不含该键（键集契约不变）。
    """
    issues: list[dict] = []
    e = w = 0
    row["_semantics"] = structure_semantics(row)
    try:
        for rid, name, fn, sev_default in CLAUSE_VALIDATION_RULES:
            try:
                ok, msg, sev_override = fn(row)
            except Exception as ex:  # noqa: BLE001  校验异常不得影响产物生成
                ok, msg, sev_override = False, f"校验异常：{ex!r}", "WARN"
            if ok:
                continue
            sev = sev_override or sev_default
            if sev not in CLAUSE_ISSUE_SEVERITY:
                sev = "WARN"
            e += 1 if sev == "ERROR" else 0
            w += 1 if sev == "WARN" else 0
            issues.append({"rule_id": rid, "rule": name, "severity": sev, "message": msg})
    finally:
        row.pop("_semantics", None)
    issues.sort(key=lambda x: 0 if x["severity"] == "ERROR" else 1)
    status = "FAILED" if e else ("PASSED_WITH_WARNINGS" if w else "PASSED")
    return {"status": status, "error_count": e, "warn_count": w,
            "issues": issues[:_MAX_ISSUES]}


# --------------------------------------------------------------------------- #
# 六、兼容层（保持既有调用方零改动）
# --------------------------------------------------------------------------- #
_ARTICLE_HEAD_RE = re.compile(r"^第\s*(?:[0-9]+|[%s]+)\s*条[、．.\s]?" % _CN)


def _article_body(body: str) -> str:
    """条文 body 剥离行首「第X条」前缀（body 以起条文行开头时），保留其余正文。"""
    s = (body or "").strip()
    m = _ARTICLE_HEAD_RE.match(s)
    return s[m.end():].strip() if m else s


def extract_structure(text: str, *, repair: bool = True) -> dict:
    """主入口（**law 模式**）：解析规范化正文 → 章/条结构。

    与 `parse_document` 的分工：本函数是 `internal_policy_base` / `clause_index` 的既有
    调用面，语义为"法令体优先"，不做跨模式降级（降级由 `parse_document` 提供）。
    返回键为 `parse_document` 的超集子集：`chapters/articles/chapter_count/article_count/
    structure/structure_count/method/tail_marker/repair/mode/score/is_fallback`。

    `repair=True` 时执行换行截断合并 + 编号去重 + 章节索引修正（2026-09-18 新增，默认开启）。
    """
    if not text or not text.strip():
        return {"chapters": [], "articles": [], "chapter_count": 0, "article_count": 0,
                "structure": [], "structure_count": 0, "method": "regex", "tail_marker": "",
                "mode": "empty", "score": {"total": 0.0}, "is_fallback": True,
                "tail_cut": {}, "article_structure": [],
                "repair": {"merged": 0, "merged_nos": [], "reasons": []}}
    text = normalize_parse_text(text)               # F5a（与 parse_document 同口径）
    text, tail_cut = strip_document_tail(text)      # F3
    law = _parse_law(segment_body(text))
    if repair:
        arts, rep = repair_articles(law["articles"])
    else:
        arts = law["articles"]
        rep = {"merged": 0, "merged_nos": [], "reasons": []}
    if repair:
        arts, ded = dedup_articles(arts)
        if ded["deduplicated"]:
            rep["deduplicated"] = ded["deduplicated"]
    chapters, chfix = fix_chapter_index(law["chapters"], arts)
    if chfix["clamped"]:
        rep["chapter_clamped"] = chfix["clamped"]
    if law.get("sections"):
        rep["sections_dropped"] = len(law["sections"])
    return {"chapters": chapters, "articles": arts,
            "chapter_count": len(chapters), "article_count": len(arts),
            "structure": [], "structure_count": 0, "method": "regex",
            "tail_marker": law["tail_marker"], "mode": "law",
            "score": score_parse({"chapters": chapters, "articles": arts}, text),
            "is_fallback": False, "repair": rep, "tail_cut": tail_cut,
            "article_structure": parse_article_structure(arts)}


def render_markdown(stru: dict, title: str = "") -> str:
    """结构 → Markdown 视图（供 drafter 条款对照/人工审阅/LLM 检视）。

    JSON 为规范源（结构化可程序消费），MD 为渲染视图——二者由同一解析结果派生，
    不另造解析歧义。条文按章分组输出：
      # <title>
      ## 第一章 总则
      **第一条** 条文正文…
    条内含「项/目」层级的条文按层级缩进渲染（F6，2026-09-20）：
      **第二十九条** 引导段…
        - **（一）** …
            - 1 …
    非条文体（`structure` 非空）渲染为层级列表——
      + **一、** 标题
        - （一）二级标题
          - 1 条目
    """
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    chapters = stru.get("chapters") or []
    articles = stru.get("articles") or []
    structure = stru.get("structure") or []
    as_map = {n.get("number"): n for n in (stru.get("article_structure") or [])}
    if not chapters and not articles and structure:
        for n in structure:
            _render_node(n, lines, 0)
        return "\n".join(lines).strip()
    if not chapters:
        for a in articles:
            _emit_article(a, lines, as_map)
        return "\n".join(lines).strip()
    # 章边界：chapter.article_index 为该章首条在 articles 的索引
    bounds = [c.get("article_index", 0) for c in chapters]
    for i, ch in enumerate(chapters):
        start = bounds[i] if i < len(bounds) else len(articles)
        end = bounds[i + 1] if i + 1 < len(bounds) else len(articles)
        ch_title = ch.get("title") or ""
        ch_no = ch.get("no", i + 1)
        lines.append(f"## 第{ch_no}章 {ch_title}".rstrip())
        lines.append("")
        for a in articles[start:end]:
            _emit_article(a, lines, as_map)
    # 兜底：无任何章（前面已 return）或最后一个章未覆盖到尾部时的章外条文
    # （正常时最后章 end=len(articles) 已覆盖，无需重复追加）
    return "\n".join(lines).strip()


def _emit_article(a: dict, out: list[str], as_map: dict) -> None:
    """条文 → MD 行（F6：有条内层级时按「项/目」缩进；否则平铺原文）。"""
    num = a.get("number", "")
    node = as_map.get(num) if num else None
    if node and node.get("items"):
        out.append(f"**{num}** {node.get('content') or ''}".rstrip())
        for it in node.get("items") or []:
            out.append(f"  - **{it.get('number', '')}** {it.get('content', '')}".rstrip())
            for sub in it.get("items") or []:
                out.append(f"    - {sub.get('number', '')} {sub.get('content', '')}".rstrip())
    else:
        out.append(f"**{num}** {_article_body(a.get('body', ''))}")
    out.append("")


def _render_node(node: dict, out: list[str], depth: int) -> None:
    """层级节点 → MD 列表（depth 0=一级 / 1=二级）。"""
    pad = "  " * depth
    num = node.get("number", "")
    title = (node.get("title") or "").strip()
    content = (node.get("content") or "").strip()
    head = f"{num}、{title}" if num and not num.endswith(("）", ")")) else f"{num}{title}"
    out.append(f"{pad}- **{head}**" + (f" {content}" if content else ""))
    for it in node.get("items") or []:
        n = it.get("number") or ""
        out.append(f"{pad}  - {n} {it.get('content', '')}".rstrip())
    for ch in node.get("children") or []:
        _render_node(ch, out, depth + 1)
    out.append("")


if __name__ == "__main__":  # 离线自检
    demo = (
        "第一章 总则\n第一条 为了规范…，制定本规定。\n"
        "第二条 本规定适用于…。\n第二章 分则\n第三条 机构应当…\n"
        "附件 表单\n附表1"
    )
    r = extract_structure(demo)
    assert r["chapter_count"] == 2 and r["article_count"] == 3, r
    assert r["chapters"][1]["title"] == "分则"
    assert r["articles"][0]["number"] == "第一条"
    md = render_markdown(r, title="测试规定")
    assert "## 第一章 总则" in md and "**第一条** 为了规范" in md, md
    # 2026-09-18：章标题与首条粘连必须切开
    r2 = extract_structure("第一章 总 则第一条 为了…。\n第二条 应当…。")
    assert [a["no"] for a in r2["articles"]] == [1, 2], r2["articles"]
    # 2026-09-18：交叉引用不得开新条（合并回上一条）
    r3 = extract_structure("第五十五条 金融机构有本法第五十三条、\n"
                           "第五十四条规定的行为，致使后果发生的。")
    assert len(r3["articles"]) == 1, r3["articles"]
    assert "第五十四条规定的行为" in r3["articles"][0]["body"]
    # 2026-09-18：非条文体自动降级（不再空产出）
    r4 = parse_document("一、 第一项工作要点。 1、 具体措施。\n二、 第二项工作要点。")
    assert r4["mode"] == "notice" and r4["structure_count"] == 2, r4
    print("[document_structure] 自检通过：%d 章 / %d 条 + MD 渲染 + 降级/合并/校验"
          % (r["chapter_count"], r["article_count"]))
