# -*- coding: utf-8 -*-
"""pbc.detail — **详情层**：详情页字段抽取（标题 / 发布日期 / 正文 / 文号 / 发文机关 /
生效日期）。

纯函数层：输入 HTML 文本 ⇒ 输出结构化字段；**不触网、不落盘** ⇒ 可被夹具直接驱动。
抽取策略沿用原实现（多路兜底 + 正文优先启发式），本次为**逐字搬运**，不改判定逻辑。

与 gov 源的口径差异（现状披露，勿误当 bug）：本层 `extract_doc_number` 优先委托
`std_lib.scraper_std.doc_number`（五源统一）；`extract_issuing` 只在**正文**启发式识别；
`extract_effective` 只认「自…起施行」句式。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_os.path.abspath(_os.path.join(_HERE, "..", "..", "..", "..")),
           _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _p, _os, _sys, _HERE

import re

# 依赖导入**双形态**（W4 L1 重组纪律）。
try:  # 包形态
    from .constants import ISSUING_AUTHORITIES
    from .parse import clean_text, normalize_digits, strip_tags
except ImportError:  # 脚本形态
    from constants import ISSUING_AUTHORITIES  # type: ignore[no-redef]
    from parse import clean_text, normalize_digits, strip_tags  # type: ignore[no-redef]

# ----------------------------------------------------------------------------------
# 详情页正则
# ----------------------------------------------------------------------------------
RE_ARTICLE_TITLE_META = re.compile(r'<meta\s+name="ArticleTitle"\s+content="([^"]*)"', re.IGNORECASE)
RE_TITLE_H2 = re.compile(r'<h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
RE_SHIJIAN = re.compile(r'<span\s+id="shijian"[^>]*>([^<]*)</span>', re.IGNORECASE)
RE_ZOOM = re.compile(r'<div\s+id="zoom"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
RE_P = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
RE_DOC_NUMBER = re.compile(
    r'[〔【]\s*\d{4}\s*[〕】]\s*第?\s*\d+\s*号'                     # 〔2024〕1号 / 〔2024〕第1号
    r'|(中国人民银行令|公告|银发|银监发|保监发|证监发|法释|法发|国发|国办发|'
    r'财政部令|银保监会令|金融监管总局令|中国银行业监督管理委员会令|'
    r'中国保险监督管理委员会令|中国证券监督管理委员会令)[^<〉】\s]{0,10}?第?\s*\d+\s*号'
)
RE_EFFECTIVE = re.compile(r'自\s*([\d]{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*起')


# ----------------------------------------------------------------------------------
# 详情页解析
# ----------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------
# 详情页解析
# ----------------------------------------------------------------------------------
def parse_detail(html_text):
    """从 HTML 详情页提取结构化字段。"""
    # 标题
    title = ""
    mt = RE_ARTICLE_TITLE_META.search(html_text)
    if mt:
        title = clean_text(mt.group(1))
    if not title:
        mh = RE_TITLE_H2.search(html_text)
        if mh:
            title = clean_text(strip_tags(mh.group(1)))

    # 发布日期
    publish_date = None
    ms = RE_SHIJIAN.search(html_text)
    if ms:
        raw = ms.group(1).strip()
        dm = re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", raw)
        if dm:
            publish_date = normalize_digits(dm.group(0)).replace("年", "-").replace("月", "-").replace("/", "-")

    # 正文：优先 #zoom，回退到 <td class="content">
    body_text = ""
    zm = RE_ZOOM.search(html_text)
    if zm:
        paras = [strip_tags(p) for p in RE_P.findall(zm.group(1))]
        body_text = "\n".join(p for p in paras if p).strip()
    if not body_text:
        # 兜底：尝试常见 gov CMS 正文容器（仅当上述选择器均落空时启用，
        # 取含多个 <p> 段落的最长候选，避免抓取导航/页眉等短文本）。
        for token in ("TRS_Editor", "content", "pages_content", "article",
                      "fontarea", "zoom", "newscontent"):
            m = re.search(
                r'<(div|font|td|article|span)[^>]*\bclass="[^"]*%s[^"]*"[^>]*>(.*?)</\1>'
                % re.escape(token), html_text, re.IGNORECASE | re.DOTALL)
            if not m:
                m = re.search(
                    r'<(div|font|td|article|span)[^>]*\bid="%s"[^>]*>(.*?)</\1>'
                    % re.escape(token), html_text, re.IGNORECASE | re.DOTALL)
            if m:
                paras = [strip_tags(p) for p in RE_P.findall(m.group(2))]
                cand = "\n".join(p for p in paras if p).strip()
                if len(cand) > len(body_text):
                    body_text = cand

    # 启发式提取：发文字号 / 发文机关 / 生效日期
    # 注意：仅基于正文 body_text 抽取，避免页面页眉/导航中的"中国人民银行"等干扰。
    doc_number = extract_doc_number(body_text)
    issuing = extract_issuing(body_text)
    effective = extract_effective(body_text)

    return {
        "title": title,
        "publish_date": publish_date,
        "document_number": doc_number,
        "issuing_authority": issuing,
        "effective_date": effective,
        "content": body_text,
    }

def extract_doc_number(*texts):
    """
    启发式提取发文字号。仅匹配高置信模式（含年份六角括号 〔YYYY〕 或机关关键词+号），
    刻意排除纯数字串（如页面追踪 ID '05073439号'），避免产生虚假发文字号。
    注意：国家法律类等由人大通过的文本通常无"发文字号"，此时返回 None 属正常。
    """
    blob = "\n".join(t for t in texts if t)
    # 2026-09-05：优先委托五源统一模块 doc_number（15+ 优先级正则 + 规范化）；
    # 模块不可用/未命中时回退原 RE_DOC_NUMBER，并同样做规范化输出。
    try:
        try:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
        except ImportError:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
    except Exception:  # pragma: no cover  # noqa: BLE001
        _u = _n = None  # type: ignore[assignment]
    if _u is not None:
        dn = _u(blob)
        if dn:
            return dn
    m = RE_DOC_NUMBER.search(blob)
    if m:
        return _n(m.group(0)) if _n is not None else clean_text(m.group(0))   # N-67：显式非 None
    return None

def extract_issuing(*texts):
    """
    启发式识别发文机关。优先在前言/标题区（前 350 字）匹配，
    并优先识别"X令"形式（部门规章/规范性文件典型）；法律类前言含
    "全国人民代表大会常务委员会"，不会被正文中出现的"中国人民银行"误判。
    """
    blob = "\n".join(t for t in texts if t)
    head = blob[:350]
    m = re.search(
        r'(中国人民银行|国务院|国家外汇管理局|财政部|国家金融监督管理总局|'
        r'中国银行保险监督管理委员会|中国保险监督管理委员会|'
        r'中国证券监督管理委员会|全国人民代表大会常务委员会|全国人民代表大会)\s*令', head)
    if m:
        return m.group(1)
    for auth in ISSUING_AUTHORITIES:
        if auth in head:
            return auth
    for auth in ISSUING_AUTHORITIES:
        if auth in blob:
            return auth
    return None

def extract_effective(body_text):
    m = RE_EFFECTIVE.search(body_text)
    if m:
        return normalize_digits(clean_text(m.group(1))).replace(" ", "")
    return None
