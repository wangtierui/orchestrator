# -*- coding: utf-8 -*-
"""gov.extract — gov 采集器**纯抽取层**（E2 拆分，2026-10-16）。

全部是**纯函数**：文本/HTML 进、字符串出，**无网络、无落盘、无全局状态**——
因此可被 `tests/cassettes/` 夹具直接驱动锁定（见 `tests/test_collector_replay_zhengceku.py`
与 gov 抽取相关用例），也是后续按层继续拆分的天然边界。

搬移纪律：函数体**逐字搬运**（含原注释），仅改依赖来源；行为变更须另开批次并附夹具证据。
"""

import os as _os
import sys as _sys
from typing import Any

_HERE = _os.path.dirname(_os.path.abspath(__file__))
# 三条路径都要：仓根（`std_lib`/`config` 等包）、上层 `collectors`、**本目录**
# （兄弟模块裸名回退所需——被 `importlib` 按路径单独加载时本目录不在 sys.path）。
for _p in (_HERE,
           _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", "..")),
           _os.path.dirname(_HERE)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _p, _os, _sys, _HERE

import re

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块，15+ 优先级正则 + 规范化）
try:
    from std_lib.scraper_std.doc_number import extract_doc_number as _unified_doc_number
except ImportError:  # pragma: no cover
    _unified_doc_number = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 抽取用常量（仅本层使用）
# --------------------------------------------------------------------------- #
_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)


# --------------------------------------------------------------------------- #
# 抽取函数
# --------------------------------------------------------------------------- #
def clean_text(s: Any) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def decode_html(content: bytes) -> str:
    """
    鲁棒解码中文网页字节：先尝试 UTF-8，再尝试 GB18030/GBK。
    以「汉字占比最高、替换符最少」为准，避免把 GBK 页面误当 UTF-8 解码成乱码。
    """
    if not content:
        return ""
    cands: list[str] = []
    try:
        cands.append(content.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "gbk"):
        try:
            cands.append(content.decode(enc))
        except UnicodeDecodeError:
            pass
    if not cands:
        return content.decode("utf-8", errors="replace")

    def _score(s: str) -> int:
        cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
        return cjk - s.count("�") * 10

    return max(cands, key=_score)


def extract_date(text: str) -> str:
    """从文本中抽取第一个 YYYY年MM月DD日 / YYYY-MM-DD 日期。"""
    if not isinstance(text, str) or not text:
        return ""
    m = re.search(r"\d{4}[-年]\d{1,2}[-月]\d{1,2}日?", text)
    if not m:
        m = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)
    return m.group(0) if m else ""


def extract_doc_number(text: str) -> str:
    """抽取发文字号，如『国务院令第300号』→ 国务院令第300号。

    2026-09-05：优先委托五源统一模块 ``std_lib.scraper_std.doc_number``
    （15+ 优先级正则 + 半角〔〕规范化 + 标题内嵌文号）；模块缺失或未命中时
    回退原本地正则，保证抓取行为不劣化。
    """
    if not text:
        return ""
    if _unified_doc_number is not None:
        dn = _unified_doc_number(text)
        if dn:
            return dn
    m = re.search(r"([\u4e00-\u9fa5]{2,14}?" + _ORGAN_KW + r"令第\s*\d+\s*号)", text)
    if not m:
        m = re.search(r"[\u4e00-\u9fa5（(][\u4e00-\u9fa5\d（）()]+?〔\d{4}〕\d+\s*号", text)
    if not m:
        return ""
    # 第二条正则（〔YYYY〕N号 格式）无捕获组，统一用 group(0) 避免 IndexError
    s = re.sub(r"^[年月日（）()\s]+", "", m.group(0))  # 去掉前缀多余的日期字
    return clean_text(s)


def extract_issue_organ(text: str) -> str:
    """从发文字号上下文抽取发文机关，如『中华人民共和国国务院令第300号』。"""
    if not text:
        return ""
    m = re.search(r"([\u4e00-\u9fa5]{2,14}?" + _ORGAN_KW + r")令第", text)
    if not m:
        return ""
    s = re.sub(r"^[年月日（）()\s]+", "", m.group(1))
    return clean_text(s)


def make_summary(full_text: str, length: int) -> str:
    s = clean_text(full_text)
    # 跳过开头的『（…公布…修订）』前言括号，直接取正文摘要
    if s.startswith("（") or s.startswith("("):
        end = s.find("）")
        if end == -1:
            end = s.find(")")
        if end != -1:
            s = s[end + 1:].strip()
    return s[:length]


def _longest_text_block(soup: Any) -> Any | None:
    """通用正文兜底：当 CONTENT_SELECTORS 均未命中时，返回页面中纯文本最长、
    且非导航（链接文本占比不过高）的内容块。用于覆盖各市政府子站异构结构。

    第四轮增强：
    - 候选标签扩展到 table/td/main（覆盖正文塞在表格里的政府详情页）；
    - 链接占比阈值放宽到 0.5（避免误杀正文含脚注/相关链接的页面，如 jinan）；
    - 若无任何块达标，兜底取 <body>（剔除 script/style/噪声），覆盖正文容器
      既非 div 也非 table 的情形（如 jinan 的 swiper 结构，正文确在 HTML 中
      但因被整体当作导航而误杀）。仅当 body 文本足够长才采用，极薄/拦截页
      仍保持为空，符合预期。
    """
    best, best_len = None, 200  # 阈值：过滤极短块
    for tag in soup.find_all(["div", "article", "section", "table", "main"]):
        links = tag.find_all("a")
        text = tag.get_text(" ", strip=True)
        if len(text) <= best_len:
            continue
        # 链接文本占比 >50% 视为导航/侧栏，跳过
        if links and len(text):
            link_chars = sum(len(a.get_text(strip=True)) for a in links)
            if link_chars / len(text) > 0.5:
                continue
        best, best_len = tag, len(text)
    if best is None:
        body = soup.body
        if body is not None:
            for el in body.select("script, style"):
                el.decompose()
            for hint in _NOISE_CLASS_HINTS:
                for el in body.select(f'[class*="{hint}"]'):
                    el.decompose()
            bt = body.get_text(" ", strip=True)
            if len(bt) > best_len:
                return body
    return best


def _is_https_scheme_upgrade(html: str) -> bool:
    return bool(_SCHEME_UPGRADE_RE.search(html or ""))
