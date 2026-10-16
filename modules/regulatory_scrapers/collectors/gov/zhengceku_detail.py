# -*- coding: utf-8 -*-
"""gov.zhengceku_detail — **详情层**：详情页字段抽取（标题 / 文号 / 发布日期 / 正文 HTML / 清洗后正文）。纯函数层，可被 `tests/cassettes/zhengceku_detail_page.json` 直接驱动。

依赖方向：zhengceku_detail → zhengceku_list（仅详情层）
本文件为**纯搬运**（E3 拆分：函数体逐字保留，含原注释），仅改依赖来源。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.dirname(_HERE),
           _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：各层常量（`_SRC_ROOT` / `_REPO_ROOT`）用它计算路径
del _p, _os, _sys

import re

# 层间依赖（详情层 → 列表层的共享纯函数；双形态）
try:  # 包形态
    from .zhengceku_list import _TAG, _iso, clean_html_text
except ImportError:  # 脚本形态
    from zhengceku_list import _TAG, _iso, clean_html_text  # type: ignore[no-redef]


# ----------------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------------
_META = re.compile(r"(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]", re.U)
CONTENT_SELECTORS = [
    ".pages_content", ".article-content", "#UCAP-CONTENT", ".TRS_Editor",
    ".content_box", ".article", "#content_body", ".pages-content", "#zoom",
]
_DOCNO_RE = re.compile(
    r"[\u4e00-\u9fff]{2,12}(?:发|办|令|公告|函|字)?\s*[〔\[【]\s*(?:19|20)\d{2}\s*[〕\]】]\s*第?\s*\d+\s*号"
    r"|[\u4e00-\u9fff]{2,12}令\s*第?\s*\d+\s*号")
_LABEL_LIKE = re.compile(r"^\s*(发文字号|发文机关|成文日期|主题分类|公文种类|来\s*源|标\s*题)\s*[：:]")


def _extract_docno(text: str) -> str:
    """从标题/正文首部提取发文字号（`_meta_field` 取不到时的兜底）。"""
    m = _DOCNO_RE.search(text or "")
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def _meta_field(html: str, label: str, maxlen: int = 120) -> str:
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") != label.replace(" ", ""):
            continue
        frag = clean_html_text(html[m.end():m.end() + 400]).replace("|", " ").strip()
        if not frag:
            continue
        val = frag.split("\n")[0].strip()
        # ⚠️ 该页此字段为空时，窗口会滑到相邻标签 → 取到「来 源：」之类标签文本，
        # 会污染 document_number/issue_organ 等字段（实测暂存中曾出现 document_number='来 源：'）。
        if not val or _LABEL_LIKE.match(val) or len(val) > 160:
            continue
        return val[:maxlen]
    return ""


def pick_content_html(html: str) -> str:
    for sel in CONTENT_SELECTORS:
        cls, idv = sel[1:], sel[1:]
        if sel.startswith("."):
            m = re.search(r'<div[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        else:
            m = re.search(r'<div[^>]*id="' + re.escape(idv) + r'"[^>]*>(.*?)</div>\s*(?:</div>|<div)',
                          html, re.I | re.S)
        if m and len(clean_html_text(m.group(1))) > 120:
            return m.group(1)
    best, blen = "", 0
    for m in re.finditer(r"<div[^>]*>(.*?)</div>", html, re.S):
        t = clean_html_text(m.group(1))
        if len(t) > blen:
            best, blen = m.group(1), len(t)
    return best


def parse_detail(html: str, url: str) -> dict:
    """详情页 → gov 主库契约的部分字段（不含附件）。"""
    title = ""
    m = re.search(r"(?is)<title>(.*?)</title>", html or "")
    if m:
        title = re.split(r"[_\-|]", _TAG.sub("", m.group(1)).strip())[0].strip()
    for m in _META.finditer(html):
        if m.group(1).replace(" ", "").replace("\u3000", "") == "标题":
            t2 = clean_html_text(html[m.end():m.end() + 300]).split("\n")[0].strip()
            if len(t2) > len(title):
                title = t2
            break
    body = clean_html_text(pick_content_html(html))
    pub = _iso(_meta_field(html, "成文日期", 40))
    docno = _meta_field(html, "发文字号", 60)
    if not docno:
        # 兜底：部分部门文件页无「发文字号」标签（或标签后为空），但**标题/正文首部**载明文号。
        docno = _extract_docno(title) or _extract_docno(body[:600])
    return {
        "title": title,
        "detail_url": url,
        "document_number": docno,
        "issue_organ": _meta_field(html, "发文机关", 60),
        "publish_date": pub,
        "pub_date_original": _meta_field(html, "成文日期", 40),
        "full_text": body,
    }
