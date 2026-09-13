# -*- coding: utf-8 -*-
"""
internal_policy_base.scan — 内部制度源目录扫描与文件名解构（P6 scanner）

输入：制度源目录（默认可经 INTERNAL_POLICY_ROOT 或 --source-dir 指定，不硬编码路径）。
输出：文件级元数据清单（不解读正文）：
      {
        "ipn": "IPN-<16hex>",            # D-03：独立编号，md5(文号|标题)[:16]
        "file_name": "...",               # 原文件名
        "extension": "pdf|doc|docx|xlsx|xls",
        "docno": "阳光人寿办发〔2023〕58号",  # 文件名解构；无 → ""
        "title": "...",                   # 文件名解构（去文号/扩展名/前导_）；无 → 去扩展名
        "source_path": "绝对路径(源目录)",
        "size_bytes": 123,
        "sha256": "hex",                  # 内容指纹（摄取去重）
        "scanned_at": "YYYY-MM-DD HH:MM:SS",
      }

文件名形态（实测 107 文件）：
  1) 有文号：`阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf`
  2) 无文号：`_个险中心城市渠道营销员考勤管理办法（2023版）.pdf`（前导 _）
  3) 无文号无下划线：`某某管理办法.pdf`
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

# 文号形态：机关代字〔年〕序号 号（含 全半角括号、纯数字年、可无代字）
_DOCNO_RE = re.compile(
    r"(?P<docno>[\u4e00-\u9fa5]{1,20}(?:〔|\()\d{4}(?:〕|\))\s*第?\s*\d{1,4}\s*号)"
)
# 年份括注（如 （2023版）/（2023年））不属文号
_EXT_RE = re.compile(r"\.(pdf|doc|docx|xlsx|xls|xlsm|csv|txt|wps|rtf)$", re.I)
_SUPPORTED = {".pdf", ".doc", ".docx", ".xlsx", ".xls", ".xlsm"}

# 非制度正文（检视台账/清单）过滤（2026-09-13 · P2）：
# 本语料制度正文载体为 pdf/doc/docx；xls/xlsx 均为附表/台账（实测索引 102 条 xls/xlsx 无一为正文）。
# 台账类（名称含下列关键词者）不再纳入制度索引——原实现会把「制度检视自查情况表」「制度清单」
# 等台账当作制度摄入（实测 24 条最终成为"内容已不在原件库"的失效记录，见
# reports/内部制度原件双份存储与索引漂移分析_20260913.md §4.6）。
# 关闭开关：scan_directory(..., exclude_ledgers=False)
_LEDGER_EXTS = {".xls", ".xlsx"}
_LEDGER_HINTS = ("制度清单", "自查情况表", "台账", "统计表", "收集表", "制度检视", "清单")


def is_non_policy_ledger(file_name: str) -> bool:
    """台账/清单类（非制度正文）判定：xls/xlsx 且名称命中台账关键词。"""
    stem, ext = os.path.splitext(file_name or "")
    return ext.lower() in _LEDGER_EXTS and any(k in stem for k in _LEDGER_HINTS)


def parse_filename(name: str) -> dict:
    """文件名 → {docno, title}（解构锚点，非正文权威）。
    有文号：docno=文号, title=下划线后段（去扩展名）；
    无文号：title=去前导'_'/'-' 后整名（去扩展名）。
    """
    base = _EXT_RE.sub("", name.strip())
    m = _DOCNO_RE.search(name)
    docno = m.group("docno").strip() if m else ""
    if docno:
        # 文号后通常是 _ 分隔标题；兼容"标题（文号）"尾括号形态：
        # 文号后无实质内容（仅"）"等）→ 取文号前段（2026-09-12 实证 4 例）
        after = name[m.end():].lstrip("_").strip("）)（( ")
        after = _EXT_RE.sub("", after).strip()
        if len(after) >= 2:
            title = after
        else:
            before = name[:m.start()].strip("_（( ")
            title = _EXT_RE.sub("", before).strip() if before else _EXT_RE.sub("", base)
    else:
        title = _EXT_RE.sub("", base).lstrip("_").lstrip("-").strip()
    return {"docno": docno, "title": title}


def ipn_of(docno: str, title: str, extension: str = "") -> str:
    """内部制度编号派生（D-03，独立 RFN 空间）：md5(文号|标题|介质) 前 16 位。

    键 = 文号 + '|' + 标题 + (可选 '|' + 扩展名)（2026-09-08 修复）：
      - 同一 OA 文号常整批发文（如「阳光人寿发〔2025〕293号」下发 17 份档案表单），
        若只用文号会全部冲突；
      - 同一制度常见双介质（同名 pdf + xlsx 并存），扩展名参与消歧；
    无文号文件回退标题。与 rfn.unique_key 同构但前缀分离（IPN-）。
    """
    docno = (docno or "").strip()
    title = (title or "").strip()
    ext = (extension or "").strip().lower().lstrip(".")
    key = f"{docno}|{title}|{ext}" if docno else f"{title}|{ext}"
    return "IPN-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


# ============ 正文身份识别（内容权威，2026-09-12） ============
# 背景：部门制度文件名词干噪声多（"23."/"编号1."/"…_盖章"/"-清洁版V3"），文号与标题
# 须以**正文**为准；提取失败回退文件名解构（parse_filename，见模块头）。
_CONTENT_DOCNO_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,20}(?:〔|【|（|\(|\[)\s*\d{4}\s*(?:〕|】|）|\)|\])\s*第?\s*\d{1,4}\s*号)")
# 说明：括号形态须含**半角方括号** `[2017]`——实测反例（2026-09-13 用户报告）：
#   `阳光人寿发[2017]2357号关于印发《…公文处理办法（2017修订版）》的通知`
#   `普通阳光人寿保险股份有限公司阳光人寿发[2014]42号关于下发《…》的通知`
#   二者文号均用半角方括号，旧正则不含 `[]` → 文号完全提取不到（名称还残留内嵌文号）。
# 说明：「文件编号：XXX」（体系文件编号，如 SLOC-0203-05）**非发文号**，不作 docno 提取

# ---- 文号提取纪律（用户规则 2026-09-13）----------------------------------------
# ① "文号只会在文档标题处出现"：仅在前 title_area_chars 字的**标题区域**提取，
#    且排除引用语境（`（保监发〔2013〕40号）`、`同步废止…673号文件` 等）。
# ② "公司内部文号均以『阳光人寿』『阳光保险』开头"：发文机关前缀白名单，非白名单一律判否。
DOCNO_PREFIX_WHITELIST = ("阳光人寿", "阳光保险")
# 引用语境标记：文号前 14 字内含其一 → 视为"引用他文"，不作为本文文号
_DOCNO_REF_CTX = ("根据", "依据", "按照", "参照", "同步废止", "废止", "参见",
                  "规定", "符合", "适用于", "规范性文件", "法律法规")
# 红头机关名（发文机关标志，如"阳光人寿保险股份有限公司文件"）——须剥除后方得文号
_REDHEAD_RE = re.compile(
    r"^[\u4e00-\u9fa5]{0,30}?(?:公司|集团|事业部|分公司|银行|委员会|部|厅|局)文件"
    r"(?=[\u4e00-\u9fa5]{2,14}(?:〔|【|\[|\(|（)\s*\d{4})")
# 归一后的严格文号形态：机关代字〔四位年〕序号号
_DOCNO_FULL_RE = re.compile(r"^[\u4e00-\u9fa5]{2,20}〔\d{4}〕\d{1,4}号$")
# 红头机关名（名称清洗用）：`…(股份)有限公司[文件]` 且其后紧跟**文号**（防误伤正常标题）。
# 2026-09-13 扩面：允许前置修饰词（实测 `普通阳光人寿保险股份有限公司阳光人寿发[2014]42号…`）
# 且 `文件` 可缺省（旧规则强制 `文件` + `(?=汉字)`，导致该红头剥不掉、文号进不了标题区）。
_REDHEAD_NAME_RE = re.compile(
    r"^[\u4e00-\u9fa5]{0,10}?(?:股份有限公司|有限公司|集团|事业部|分公司)(?:文件)?"
    r"(?=[\u4e00-\u9fa5]{2,14}(?:〔|【|\[|\(|（)\s*\d{4})")
# 文号锚点（归一用）：以白名单前缀起、到 `…号` 止的**最后一个**锚点即真实文号起点。
# 用于剥"白名单前缀 + 红头 + 再次白名单前缀"的重复（`…股份有限公司阳光人寿发[2014]42号`）。
_DOCNO_START_RE = re.compile(
    r"(?:阳光人寿|阳光保险)[\u4e00-\u9fa5]{0,12}"
    r"(?:〔|【|\[|\(|（)\s*\d{4}\s*(?:〕|】|\]|\)|）)\s*第?\s*\d{1,4}\s*号")
# 主送机关后缀（正文标题与主送机关常连写无换行："…工作指引各分公司："）
_MAIN_TO_RE = re.compile(
    r"(?:各分公司|总公司各部门|各事业部|各中心|各部门|各处室|各单位|各机构)[，,：:].*$", re.S)


def normalize_docno(raw: str) -> str:
    """文号归一：去空白 → 剥红头机关名 → 从白名单前缀处截断（去"特此通知"等前置噪声）
    → 各类括号统一为 〔〕 → 去"第"。

    实测反例（2026-09-13）：`阳光人寿保险股份有限公司文件阳光人寿发（2020）404号`
    → `阳光人寿发〔2020〕404号`；`特此通知阳光保险发【2022】90号` → `阳光保险发〔2022〕90号`。
    """
    t = re.sub(r"\s+", "", raw or "")
    t = _REDHEAD_RE.sub("", t)
    # 白名单锚点对齐（2026-09-13）：取**最后一个**"前缀＋括号年＋号"锚点为起点，剥掉
    # "前缀＋机关全称＋再次前缀"的重复（实测 `普通阳光人寿保险股份有限公司阳光人寿发[2014]42号`）。
    anchors = list(_DOCNO_START_RE.finditer(t))
    if anchors:
        t = t[anchors[-1].start():]
    idx = [p for p in (t.find("阳光人寿"), t.find("阳光保险")) if p >= 0]
    if idx and min(idx) > 0:
        t = t[min(idx):]
    t = (t.replace("【", "〔").replace("】", "〕")
          .replace("[", "〔").replace("]", "〕")
          .replace("（", "〔").replace("）", "〕")
          .replace("(", "〔").replace(")", "〕"))
    t = re.sub(r"第(?=\d+号)", "", t)
    return t


def is_internal_docno(docno: str, prefixes: tuple = DOCNO_PREFIX_WHITELIST) -> bool:
    """内部制度文号校验：归一后形态合规（`机关代字〔年〕序号号`）且发文机关在前缀白名单内。

    prefixes 传空元组可关闭白名单（非本语料场景）；默认按用户规则只认"阳光人寿/阳光保险"。
    """
    d = normalize_docno(docno)
    if not d:
        return False
    if prefixes and not d.startswith(tuple(prefixes)):
        return False
    return bool(_DOCNO_FULL_RE.match(d))


def extract_docno_title_area(text: str, *, area_chars: int = 260,
                             prefixes: tuple = DOCNO_PREFIX_WHITELIST) -> tuple:
    """仅在**标题区域**提取本文件文号 → (docno, end_pos)；未命中返回 ("", 0)。

    与旧实现（全文 head_chars 内首个命中）的差异在于**三重排除**：
      ① 区域：只看文档最前 area_chars 字；
      ② 引用语境：文号前 14 字含 根据/依据/按照/同步废止/废止/规定… → 判为他文引用；
      ③ 未闭合括号：文号落在 `（…〔年〕号）` 式括号内（典型引用格式）→ 判为引用。
    再加红头剥离与白名单校验。实测拦掉：`（保监发〔2013〕40号）`、
    `同步废止阳光人寿发【2021】673号文件`、`依据文件银保监办发【2020】41号`。
    """
    area = (text or "")[:area_chars]
    for m in _CONTENT_DOCNO_RE.finditer(area):
        pre = area[:m.start()]
        tail = pre[-14:]
        if any(k in tail for k in _DOCNO_REF_CTX):
            continue
        if pre.count("（") + pre.count("(") > pre.count("）") + pre.count(")"):
            continue          # 处于未闭合左括号内 → 引用
        d = normalize_docno(m.group(0))
        if is_internal_docno(d, prefixes):
            return d, m.end()
    return "", 0
# （2026-09-12 实测：会顶替真实文号；宁缺毋滥——识别不到则回退文件名解构）。
# 文件名遗留噪声（正文权威提取失败时的 fallback 清洗）：
_TITLE_NOISE_RE = re.compile(
    r"[-_—\s]*((清洁版|盖章版|含水印|无水印|扫描件|复印件|定稿版|终版|最终版)\s*V?\d*|盖章|扫描版)\s*$")
_BOOK_TITLE_RE = re.compile(r"《([^》]{4,90})》")
_GW_TITLE_RE = re.compile(r"^(关于.{2,70}?(?:的通知|的函|的决定|的通报|的公告|的批复|的意见))")
_TITLE_HEAD_CUT = re.compile(r"(一、|第一条|第1条|第一章|第[一二三四五六七八九十]+章|目的|总则)")
# 标题须以制度类关键词**结尾**（可带版本括注）；前缀命中为误（如页眉词"标准化管理体系文件"）
# 2026-09-13 扩面：补 协议/清单/问卷/表单/说明书 等常见制度名结尾（实测 `…入住协议`、
# `…归档清单` 因不在词表而"继续向后吞正文直到碰到 办法/规定"）。
_TITLE_KW = (r"管理办法|实施细则|工作指引|作业指导书|操作规程|管理规范|管理细则|管理规定"
             r"|暂行办法|暂行规定|试行办法|决定|办法|规定|通知|细则|指引|规范|制度|方案|预案"
             r"|手册|规程|准则|标准|流程|说明|通告|公告|条例|政策|协议|意向书|承诺书|确认书"
             r"|说明书|申请表|登记表|清单|问卷|要点|措施|规划|计划|指南|表|单|书")
# 弱关键词（单字，极易误判为标题结尾）：`劳动合同书领用及用印管理办法` 的 `书`、
# `公司政策制度出台依据说明表` 的 `表`。弱词须**后继很短**才算结尾，否则继续向后找。
_TITLE_KW_WEAK = ("表", "单", "书")
_WEAK_TAIL_TOL = 8   # 弱关键词后允许的剩余长度（超过即判"标题未完"）
_TITLE_KW_END_RE = re.compile(rf"({_TITLE_KW})(?:[（(][^）)]{{1,20}}[）)]|版)?$")
# 紧跟关键词的括注是否属标题：① 版本/变体类（`（2025版）/（试行）/（2020修订）`）；
# ② 短括注且无冒号（`（B类）/（暂定）`）。**带冒号的长括注判为正文**——
# 实测 `…差旅费适用标准（金额单位：人民币元）` 属表格文字，不得并入标题。
_TITLE_VER_PAREN = re.compile(
    r"[（(](?:[^）)]{0,20}(?:版|修订|修订版|年修订|试行|暂行|征求意见稿|草案|类)"
    r"|[^）)：:]{1,6})[）)]")
# "的××"标题延长标记：`关于印发《X办法》的通知` 中的 `办法》的` 不得作为标题结尾
_TITLE_TAIL_EXT = re.compile(r"^[》〉]?\s*的(?:通知|函|决定|通报|公告|批复|意见|规定|报告)")
# 标题候选黑名单（页眉/体系词等）
_TITLE_DENY_RE = re.compile(r"^(标准化管理体系文件|管理文件|红头文件|文件)")
# 制度类关键词（不锚定），供"最早结尾"截断使用
_TITLE_KW_ANY = re.compile(_TITLE_KW)
# 句读标点（标题含之即判为正文混入；含半角 :,; ——OCR/打印页眉常带 `17:05`）
_TITLE_PUNCT_RE = re.compile(r"[。，、；：！？,;:]")


def _title_end_candidates(cand: str, *, lo: int = 6, hi: int = 80) -> list[int]:
    """列出 `cand` 中所有"可行的标题结尾下标"（升序）。

    可行性 = 制度类关键词结尾（含可吸收的版本括注）∧ 长度落在 [lo, hi] ∧ 其后不是 `的××`
    接续 ∧ （若是弱关键词 `表/单/书`）其后残余很短。
    """
    ends: list[int] = []
    for m in _TITLE_KW_ANY.finditer(cand):
        end = m.end()
        mv = _TITLE_VER_PAREN.match(cand[end:]) or re.match(r"版", cand[end:])
        if mv:
            end += mv.end()
        if not (lo <= end <= hi):
            continue
        if _TITLE_TAIL_EXT.match(cand[end:]):
            continue
        if m.group(0) in _TITLE_KW_WEAK and len(cand) - end > _WEAK_TAIL_TOL:
            continue
        ends.append(end)
    return ends


def _pick_title_candidate(picks: list[str], fallback: str) -> str:
    """在多个候选标题中择优。

    择优信号 = **候选是否为文件名词干（fallback）的子串**：文件名是本语料的强先验，而
    "以文件名开头的那一段"正是正文标题该有的边界。判据刻意用**包含关系**而非字符相似度——
    实测相似度（difflib）**偏向长串**，会把页眉/目录噪声串进标题（如
    `首页 事项管理 …关于《…管理办法》`、`阳光人寿融客事业部培训制度阳光人寿保险股份…`）。
    规则：
      · 存在"⊂ 词干"的候选 → 取**最长**者（最贴近词干前缀的边界）；
      · 否则退回**最短候选**（标题天然在开头结束）。
    该规则同时覆盖两类相反错误：`员工周转房入住协议我同意…《周转房管理办法》`（防跑进正文）
    与 `公司政策制度出台依据说明表`（防中途截断）。
    """
    if not picks:
        return ""
    if not fallback:
        return picks[0]
    nf = re.sub(r"\s+", "", fallback)
    subs = [p for p in picks if (np := re.sub(r"\s+", "", p)) and np in nf]
    return max(subs, key=len) if subs else picks[0]


def _earliest_kw_end(cand: str, *, lo: int = 6, hi: int = 80) -> int:
    """返回 `cand` 中**标题结束位置**（制度类关键词结尾，含紧跟的括注），无则 -1。

    2026-09-13（用户报告案例）：原实现对"前 80/120 字"只校验**末尾**是否为关键词，
    于是"标题与正文连写"的文档会把正文一并吞进标题——实测
    `员工周转房入住协议我同意阳光人寿保险股份有限公司《周转房管理办法`、
    `保全档案归档清单阳光人寿个险保全扫描清单统计日期：…客户号`。

    判据（**首个可行结尾即采用**）：标题天然在开头结束，第一个"关键词（＋紧跟括注）"且
    其后不再是 `的××` 接续的位置即标题结尾。实测反例：
      · `员工周转房入住协议我同意…《周转房管理办法` → 在 `协议` 处截断（其后为正文，长度 > tail_tol）；
      · `…管理办法（2022年修订版）制定本办法` → 在**版本括注**后截断（首获即止，不再向后吞到"本办法"）；
      · `关于印发《X办法》的通知` → `办法》的` 命中 `的××` 延长标记 → 跳过，落到 `通知`。
    """
    for m in _TITLE_KW_ANY.finditer(cand):
        end = m.end()
        mv = _TITLE_VER_PAREN.match(cand[end:]) or re.match(r"版", cand[end:])
        if mv:
            end += mv.end()
        if not (lo <= end <= hi):
            continue
        if _TITLE_TAIL_EXT.match(cand[end:]):
            continue                      # "的通知/的函" → 标题未完，继续
        # 弱关键词（表/单/书）须后继很短才算结尾——`管理类劳动合同书领用及用印管理办法》的通知`
        # 若在 `书` 处截断会丢掉半截标题（实测用户报告第 3 例）。
        if m.group(0) in _TITLE_KW_WEAK and len(cand) - end > _WEAK_TAIL_TOL:
            continue
        return end
    return -1


def _trim_self_repeat(s: str) -> str:
    """消解"公司名+标题+公司名 发布/编制…"自重复（如"XX公司内部控制指引XX公司 发布"）：
    找首段（8-30 字）的复现位置，截到复现起点（保留 [公司名+标题]）。"""
    n = len(s)
    for L in range(min(30, n // 2), 7, -1):
        pos = s.find(s[:L], L)
        if pos > 0:
            return s[:pos]
    return s


def _norm_cmp(s: str) -> str:
    """比较用归一：去空白与常见标点/括号（标题重叠校验用）。"""
    return re.sub(r"[\s\-_—、：:（）()〔〕【】《》]", "", (s or ""))


def _overlaps(a: str, b: str, min_lcs: int = 8) -> bool:
    """a/b 归一后互相包含，或最长公共子串 ≥ min_lcs（正文候选与文件名 title 交叉校验，
    防"正文引用他文"被误当标题——如首部引《中华人民共和国保险法》，2026-09-12 实证）。"""
    a2, b2 = _norm_cmp(a), _norm_cmp(b)
    if not a2 or not b2:
        return False
    if a2 in b2 or b2 in a2:
        return True
    best = 0
    for i in range(len(a2)):
        for L in range(best + 1, len(a2) - i + 1):
            if a2[i:i + L] in b2:
                best = L
            else:
                break
    return best >= min_lcs


def parse_content_identity(text: str, *, head_chars: int = 1500,
                           fallback_title: str = "",
                           title_area_chars: int = 260,
                           docno_prefixes: tuple = DOCNO_PREFIX_WHITELIST) -> dict:
    """从正文首部识别 (文号, 标题)——内容权威（保守：识别不了返回空串，调用方回退文件名解构）。

    规则：
      ① 文号：**仅在标题区域**（前 `title_area_chars` 字）提取，且过引用语境排除、
            括号归一〔〕、发文机关前缀白名单（见 `extract_docno_title_area`）。
      ② 标题：a) 文号后随的公文式标题（"关于…的通知/函/决定…"）优先；
            b) 否则首部标题段（截到"一、/第一条/第N章/目的/总则"前，6-80 字、
               以制度类关键词结尾、过黑名单）；
      校验：候选标题须与 `fallback_title`（文件名 title）有实质重叠（防引用他文误提）。
      （说明：原"首个书名号"规则已删除——正文首部引用《他法》极常见，误提风险高。）

    2026-09-13 修正（用户规则「文号只会在文档标题处出现」）：文号提取由"全文 1500 字内首个命中"
    改为"标题区域 + 引用语境排除"。旧实现把正文引用/废止声明里的他文文号当本文文号，实测反例：
    `（保监发〔2013〕40号）`（引用他文）、`同步废止阳光人寿发【2021】673号文件`（文末废止声明）。
    """
    t = (text or "").strip()
    if not t:
        return {"docno": "", "title": ""}
    head = t[:head_chars]
    docno, end = extract_docno_title_area(head, area_chars=title_area_chars,
                                          prefixes=docno_prefixes)
    seg = head[end:] if docno else head
    title = ""
    # a) 公文式标题（校验重叠）
    gm = _GW_TITLE_RE.match(seg.lstrip(" ：:，,、"))
    if gm:
        t1 = gm.group(1).strip()
        if not fallback_title or _overlaps(t1, fallback_title):
            title = t1
    # b) 首部标题段（清编号前缀 + 重叠校验 + 关键词结尾 + 黑名单）
    if not title:
        cut = _TITLE_HEAD_CUT.search(seg)
        cand = (seg[:cut.start()] if cut else seg[:120])
        cand = re.sub(r"\s+", "", cand).strip(" 　：:—-、")
        cand = re.sub(r"^[0-9]{1,3}[、.．：:]\s*", "", cand)   # 清"4."/"23、"等编号前缀
        cand = re.split(r"(编制|审核|批准|发布|编写|版本号|生效日期|文件编号)", cand)[0].strip(" 　：:—-、")
        cand = _MAIN_TO_RE.sub("", cand).strip(" 　：:—-、")   # 剥主送机关（"…工作指引各分公司："）
        cand = _trim_self_repeat(cand)
        # 候选择优（2026-09-13）：列出全部可行结尾，取与**文件名词干**最相似者
        # （兼顾"标题跑进正文"与"中途截断"两类相反错误，见 _pick_title_candidate）。
        _ends = _title_end_candidates(cand)
        cand = _pick_title_candidate([cand[:e] for e in _ends], fallback_title) or cand
        if (6 <= len(cand) <= 80 and _TITLE_KW_END_RE.search(cand)
                and not _TITLE_DENY_RE.match(cand)
                and not _TITLE_PUNCT_RE.search(cand)   # 含句读即拒（正文混入，2026-09-13 由"仅查。"扩面）
                and cand[:1] not in ("）", ")", "。")   # 句读残留即拒
                and (not fallback_title or _overlaps(cand, fallback_title))):
            title = cand
            title = cand
    return {"docno": docno, "title": title}


def clean_title_noise(title: str) -> str:
    """清洗文件名/正文遗留噪声——正文提取失败时的 fallback 用：
    ① 前缀编号（"23."/"4："/"(1)"/"编号4."）；② 红头机关名（"…股份有限公司文件"）与
    "红头文件-"；③ 尾部版本/盖章括注（-清洁版V3/_盖章/（含水印））；④ 主送机关后缀
    （"各分公司，总公司各部门："）；⑤ 去外层书名号（《…》→ …）。

    2026-09-13（用户规则）：补 ②④ 与"同步废止""编号N."——实测反例
    `阳光人寿保险股份有限公司文件…`（红头被并入名称）、`编号4.经代-销售服务人员执业证管理工作指引`。
    """
    s = (title or "").strip()
    s = re.sub(r"^[0-9]{1,3}\s*[、.．：:]\s*", "", s)
    s = re.sub(r"^[（(][0-9]{1,3}[）)]\s*", "", s)
    s = re.sub(r"^编号\s*[0-9]{1,3}\s*[、.．：:]?\s*", "", s)
    s = re.sub(r"^红头文件\s*[-—_:：]?\s*", "", s)
    s = re.sub(r"^同步废止\s*", "", s)
    s = re.sub(r"^[A-Za-z]{1,3}[0-9]{2,4}\s*", "", s)   # 档案编号前缀（如 L042）
    s = _REDHEAD_NAME_RE.sub("", s)
    s = _TITLE_NOISE_RE.sub("", s).strip(" -_—")
    s = _MAIN_TO_RE.sub("", s).strip(" -_—，,：:")
    return s.strip("《》").strip()


def inherit_docno_by_containment(items: list, *, min_snippet: int = 40,
                                 snippet_chars: int = 80) -> dict:
    """附件型文档**继承正文文号**（用户规则 2026-09-13）。

    规则原文：「部分文档作为正文附件存在，在正文文档内容的结尾处，此类文档与正文的文号相同」。
    实现：把"已有文号文档"的正文（去空白）顺序拼成检索串并记录归属区间；对"无文号"文档，
    取其正文前 `snippet_chars` 字（去空白，需 ≥ `min_snippet`）在该串中检索，命中即继承该文号。

    items：`[{"text": str, "docno": str}, ...]`；返回 `{下标: 继承到的文号}`（仅含无文号且命中者）。
    复杂度 O(总字数)（`str.find` + 二分定位归属），878 份文档实测 < 1s。
    """
    import bisect  # noqa: PLC0415
    blob, starts, owners, pos = [], [], [], 0
    for it in items:
        d = normalize_docno(it.get("docno") or "")
        if not d or not is_internal_docno(d):
            continue
        s = re.sub(r"[\s\u3000]+", "", it.get("text") or "")
        if not s:
            continue
        starts.append(pos)
        owners.append(d)
        blob.append(s)
        pos += len(s)
    if not starts:
        return {}
    full = "".join(blob)
    out = {}
    for i, it in enumerate(items):
        if (it.get("docno") or "").strip():
            continue
        sn = re.sub(r"[\s\u3000]+", "", it.get("text") or "")[:snippet_chars]
        if len(sn) < min_snippet:
            continue
        j = full.find(sn)
        if j < 0:
            continue
        k = bisect.bisect_right(starts, j) - 1
        if 0 <= k < len(owners):
            out[i] = owners[k]
    return out


def scan_directory(root: str, *, supported: set[str] | None = None,
                   exclude_ledgers: bool = True) -> list[dict]:
    """递归扫描目录下受支持文件 → 元数据清单（按文件名排序，确定性）。

    exclude_ledgers（默认 True，2026-09-13 · P2）：跳过台账/清单类 xls/xlsx
    （制度检视自查表、制度清单等），它们不是制度正文，纳入后会成为"内容已不在原件库"的
    失效索引记录。置 False 可恢复旧行为（把表格全部纳入）。
    """
    supported = supported or _SUPPORTED
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.isdir(root):
        raise FileNotFoundError(f"制度源目录不存在: {root}")
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in supported:
                continue
            if exclude_ledgers and is_non_policy_ledger(fn):
                continue
            p = os.path.join(dirpath, fn)
            sha = hashlib.sha256(open(p, "rb").read()).hexdigest()
            parsed = parse_filename(fn)
            out.append({
                "ipn": ipn_of(parsed["docno"], parsed["title"], extension=ext.lstrip(".")),
                "file_name": fn,
                "relative_path": os.path.relpath(p, root),
                "extension": ext.lstrip("."),
                "docno": parsed["docno"],
                "title": parsed["title"],
                "source_path": p,
                "size_bytes": os.path.getsize(p),
                "sha256": sha,
                "scanned_at": now,
            })
    return out


if __name__ == "__main__":  # 自检
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    samples = [
        "阳光人寿办发〔2023〕58号_个险客经渠道团队套利处置管理办法.pdf",
        "_个险中心城市渠道营销员考勤管理办法（2023版）.pdf",
        "阳光人寿客户敏感信息查询权限授权管理办法.doc",
    ]
    for s in samples:
        print(json.dumps(parse_filename(s), ensure_ascii=False))
