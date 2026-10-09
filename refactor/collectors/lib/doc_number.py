# -*- coding: utf-8 -*-
"""本地法规文号提取与规范化（移植自 std_lib.scraper_std.doc_number，纯正则、零外部依赖）。"""
from __future__ import annotations

import re

# 年份/序号统一
_FULL = re.compile(r"[\[(（]\s*(\d{4})\s*[\])）]")
_HALF = re.compile(r"\[(\d{4})\]\s*(\d+)")

_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_FW_PAREN = str.maketrans("（）", "()")

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn_to_arabic(s: str) -> str:
    s = s.strip()
    if s.isdigit():
        return s
    total, section = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            section = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            total += (section or 1) * unit
            section = 0
        else:
            return s
    return str(total + section)


_ORG = [
    "中国银行业监督管理委员会", "中国证券监督管理委员会", "中国保险监督管理委员会",
    "中国人民银行", "财政部", "国家税务总局", "住房和城乡建设部",
    "金融监管总局", "国家金融监督管理总局", "中国银行保险监督管理委员会",
]
_DAIZI_END = [
    "国家金融监督管理总局公告", "中国银行保险监督管理委员会公告",
    "中国证券监督管理委员会公告", "中国保险监督管理委员会公告",
    "中国人民银行公告", "财政部公告", "国家税务总局公告", "金融监管总局公告",
    "银保监会公告", "证监会公告", "公告",
    "金监规", "金监总局令", "金规", "金发", "金办发", "金办便函",
    "银保监发", "银保监办发", "银保监办便函", "银保监规",
    "银监发", "银监办发", "银监办便函", "银监规",
    "保监发", "保监厅发", "保监厅函", "保监办发", "保监办函", "保监中介",
    "保监财会", "保监统信", "保监稽查", "保监国际", "保监消保", "人身险部函",
    "银发", "银办发", "银函", "财发", "财税", "财库", "财会", "财金",
    "财监", "财综", "财办", "财建", "国发", "国办发", "建房", "发改", "工信部",
    "中国人民银行令", "国家金融监督管理总局令", "中国银行保险监督管理委员会令",
    "中国保险监督管理委员会令", "国务院令", "主席令", "银监会令", "保监会令",
    "证监会令", "财政部令",
]
_DAIZI_RE = re.compile(
    r"(?:" + "|".join(re.escape(o) for o in _ORG) + r")*"
    r"(?:" + "|".join(re.escape(d) for d in _DAIZI_END) + r")"
    r"\s*[（(]?\s*(?:[〔\[]\s*\d{4}\s*[〕\]]|\d{4}\s*年)\s*第?\s*\d+\s*号"
)
_SPAN_LING = re.compile(
    r"[\u4e00-\u9fa5]{1,18}?令\s*[（(]?\s*第?\s*[一二三四五六七八九十百千两零\d]+\s*号"
)
_ORG_LING = [
    "国务院", "主席", "中国人民银行", "财政部", "国家金融监督管理总局",
    "中国银行保险监督管理委员会", "中国保险监督管理委员会",
    "中国证券监督管理委员会", "中国银行业监督管理委员会", "金融监管总局",
    "银监会", "保监会", "证监会", "银保监会",
]
_LING_RE = re.compile(
    r"(?:" + "|".join(re.escape(o) for o in _ORG_LING) + r")"
    r"\s*令\s*[（(]?\s*第?\s*[一二三四五六七八九十百千两零\d]+\s*号"
)


def _locate_doc_span(s: str) -> str:
    m = _DAIZI_RE.search(s)
    if m:
        return m.group(0)
    m = _LING_RE.search(s)
    if m:
        return m.group(0)
    m = _SPAN_LING.search(s)
    if m:
        return m.group(0)
    best, best_start = "", -1
    for pat in _PATTERNS:
        mm = pat.search(s)
        if mm and mm.start() > best_start:
            best, best_start = mm.group(0), mm.start()
    return best


_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(中华人民共和国国务院令\s*第\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"((?<![\u4e00-\u9fa5])国务院令\s*第\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"(中华人民共和国主席令\s*[（(]?\s*第?\s*[一二三四五六七八九十百\d]+\s*号[）)]?)"),
    re.compile(r"(国(?:办)?发\s*[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号)"),
    re.compile(r"(国家金融监督管理总局令\s*[〔\[]\s*\d{4}\s*[〕\]]\s*第?\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(
        r"((?:金规|金发|金办发|金办便函|金监规|金监总局令|"
        r"银保监发|银保监办发|银保监办便函|银保监规|"
        r"保监发|保监厅发|保监厅函|保监办发|保监办函|保监中介|保监财会|保监统信|"
        r"保监稽查|保监国际|保监消保|人身险部函|"
        r"银发|银办发|银函|人民银行令)"
        r"\s*[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号)"
    ),
    re.compile(r"([\u4e00-\u9fa5]{2,}?令\s*\d{4}\s*年\s*第\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"(中国人民银行令\s*[〔\[]\s*\d{4}\s*[〕\]]\s*第?\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"(公开市场业务公告\s*[〔\[]\s*\d{4}\s*[〕\]]\s*第?\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"([\u4e00-\u9fa5\s]{2,40}?公告\s*\d{4}\s*年\s*第?\s*[一二三四五六七八九十百\d]+\s*号)"),
    re.compile(r"(财政部(?:、[\u4e00-\u9fa5]{2,20}?)*?令\s*[（(]?第?\s*[一二三四五六七八九十百\d]+\s*号[）)]?)"),
    re.compile(r"(财(?:发|税|库|会|金|监|综|办)\s*[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号)"),
    re.compile(
        r"([\u4e00-\u9fa5]{2,14}?(?:部|委|局|署|总局|委员会)"
        r"令\s*[〔\[]\s*\d{4}\s*[〕\]]\s*第?\s*[一二三四五六七八九十百\d]+\s*号)"
    ),
    re.compile(
        r"([\u4e00-\u9fa5]{2,10}?(?:发|函|规|令|便函)\s*"
        r"[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号)"
    ),
]


def normalize_doc_number(v: object) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    s = s.translate(_FW_DIGITS).translate(_FW_PAREN)
    s = re.sub(r"\s+", " ", s)
    s = _HALF.sub(r"〔\1〕\2", s)
    s = s.replace("[", "〔").replace("]", "〕")
    s = re.sub(r"^[^\u4e00-\u9fa5A-Za-z0-9]+", "", s)
    span = _locate_doc_span(s)
    if span:
        s = span
    s = re.sub(
        r"令\s*[（(]?\s*(第?)\s*([一二三四五六七八九十百千两零\d]+)\s*号[）)]?",
        lambda m: "令" + ("第" if m.group(1) else "") + cn_to_arabic(m.group(2)) + "号",
        s,
    )
    s = re.sub(r"(?<!中华人民共和国)国务院令", "中华人民共和国国务院令", s)
    s = re.sub(r"(?<!中华人民共和国)主席令", "中华人民共和国主席令", s)
    s = re.sub(r"(\d{4})\s*年\s*第?\s*(\d+)号", r"〔\1〕\2号", s)
    s = re.sub(r"〔(\d{4})〕\s*第?\s*(\d+)号", r"〔\1〕\2号", s)
    return clean_doc_number(s)


def clean_doc_number(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "", s)
    idx = s.find("号")
    if idx > 0 and idx < len(s) - 1:
        tail = s[idx + 1:]
        if tail and not tail[0].isdigit() and tail[0] not in "）)]":
            s = s[: idx + 1]
    return s


def extract_doc_number(text: str) -> str:
    if not text:
        return ""
    s = str(text).strip()
    for pat in _PATTERNS:
        m = pat.search(s)
        if m:
            return normalize_doc_number(m.group(0))
    return ""


def extract_from_title(title: str) -> str:
    if not title:
        return ""
    t = str(title).strip()
    m = re.search(
        r"[（(]\s*([\u4e00-\u9fa5A-Za-z0-9〔〕\[\]第号、，,和与及\s]{4,60}?号)\s*[）)]", t
    )
    if m:
        dn = extract_doc_number(m.group(1))
        if dn:
            return dn
    return extract_doc_number(t)


_BODY_PUB_RE = re.compile(
    r"[（(][^（）()]{0,30}?(?:国务院令|主席令|公告|令)\s*第?\s*\d+\s*号"
    r"[^（）()]{0,8}?(?:发布|公布|施行)[）)]"
)


def extract_from_body(text: str) -> str:
    if not text:
        return ""
    m = _BODY_PUB_RE.search(text)
    if m:
        return normalize_doc_number(m.group(0))
    return ""


_ABOLISH_WORDS = ("废止", "不再执行", "停止执行", "同时作废", "自行失效")


def in_abolish_context(text: str, doc_no: str) -> bool:
    if not text or not doc_no:
        return False
    pos = text.find(doc_no)
    if pos < 0:
        alt = str(doc_no).replace("〔", "[").replace("〕", "]")
        pos = text.find(alt)
        if pos < 0:
            return False
    if text[:pos].rfind("《") > text[:pos].rfind("》"):
        return True
    start = max(text.rfind("。", 0, pos), text.rfind("；", 0, pos), text.rfind("\n", 0, pos)) + 1
    end = len(text)
    for p in (text.find("。", pos), text.find("；", pos), text.find("\n", pos)):
        if p >= 0:
            end = min(end, p)
    sent = text[start:end]
    if not any(w in sent for w in _ABOLISH_WORDS):
        return False
    head = text[max(0, pos - 60):pos]
    return "》" in head


_ORG_ALIAS: dict[str, str] = {
    "国家税务总局": "国家税务总局", "财政部": "财政部", "中国人民银行": "中国人民银行",
    "国家外汇管理局": "国家外汇管理局",
    "中国银行业监督管理委员会": "中国银行业监督管理委员会",
    "中国证券监督管理委员会": "中国证券监督管理委员会",
    "中国保险监督管理委员会": "中国保险监督管理委员会",
    "中国银行保险监督管理委员会": "中国银行保险监督管理委员会",
    "国家金融监督管理总局": "国家金融监督管理总局",
    "国家发展和改革委员会": "国家发展和改革委员会",
    "工业和信息化部": "工业和信息化部",
    "住房和城乡建设部": "住房和城乡建设部",
    "应急管理部": "应急管理部", "商务部": "商务部", "公安部": "公安部",
    "司法部": "司法部", "交通运输部": "交通运输部", "农业农村部": "农业农村部",
    "国家市场监督管理总局": "国家市场监督管理总局", "海关总署": "海关总署",
    "国务院办公厅": "国务院办公厅", "国务院": "国务院",
    "银保监会": "中国银行保险监督管理委员会", "银监会": "中国银行业监督管理委员会",
    "证监会": "中国证券监督管理委员会", "保监会": "中国保险监督管理委员会",
    "人民银行": "中国人民银行", "外汇局": "国家外汇管理局",
    "金融监管总局": "国家金融监督管理总局", "发改委": "国家发展和改革委员会",
    "工信部": "工业和信息化部", "住建部": "住房和城乡建设部",
}
_ORG_KEYS = sorted(_ORG_ALIAS, key=len, reverse=True)


def extract_orgs(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    i, n = 0, len(text)
    while i < n:
        hit = ""
        for k in _ORG_KEYS:
            if text.startswith(k, i):
                hit = k
                break
        if hit:
            canon = _ORG_ALIAS[hit]
            if canon not in seen:
                seen.add(canon)
                out.append(canon)
            i += len(hit)
        else:
            i += 1
    return out


_REBUILD_TAIL = re.compile(r"(公告\s*\d{4}\s*年\s*第?\s*\d+\s*号)\s*$")


def rebuild_multi_org_docno(doc_no: str, title: str) -> str:
    if not doc_no or not title:
        return doc_no
    m = _REBUILD_TAIL.search(doc_no)
    if not m:
        return doc_no
    tail = m.group(1)
    head = doc_no[: m.start()]
    t = str(title).split("关于")[0]
    orgs_title = extract_orgs(t)
    if not orgs_title:
        return doc_no
    orgs_now = extract_orgs(head)
    if len(orgs_title) <= len(orgs_now):
        return doc_no
    return "".join(orgs_title) + tail


def looks_like_doc_number(v: object) -> bool:
    if not v:
        return False
    s = str(v).strip()
    if not s or s.upper() in ("N/A", "NA", "NULL"):
        return False
    return bool(re.search(
        r"(令\s*第?\s*[一二三四五六七八九十百\d]+\s*号"
        r"|令\s*\d{4}\s*年\s*第?\s*[一二三四五六七八九十百\d]+\s*号"
        r"|〔\d{4}〕\s*第?\s*\d+\s*号|\[\d{4}\]\s*第?\s*\d+\s*号"
        r"|公告\s*\d{4}\s*年\s*第?\s*[一二三四五六七八九十百\d]+\s*号)",
        s,
    ))


# 兼容外部引用名
extract = extract_doc_number
normalize = normalize_doc_number
