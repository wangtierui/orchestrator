# -*- coding: utf-8 -*-
"""nfra.extract — **解析层**（E4 拆分，2026-10-16 / D-遗留第四批）。

职责：把 nfra 接口返回的 JSON / HTML 抽成结构化字段。

**本层是零外部依赖的纯函数层**（不触网、不落盘、不依赖全局缓存），因此：
  · 等价性可用「重构前后逐字段比对」锁死，不依赖网络夹具形态假设；
  · 可被未来 `step_triage` / 单元测试直接驱动。
函数体自 E4 起**逐字保留**（含原注释），仅改依赖来源。

依赖方向：extract ← http_api ← collector（本层不依赖任何层）
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：常量计算路径需要它
del _p, _os, _sys

import re

# ⚠️ `lxml.html` 是 `html_to_text` 的**主路径依赖**：缺失时函数内的 try 抛NameError
# ⇒ **静默落到正则兜底** ⇒ `<script>` 内容不被剥离、正文多出换行。
# 该依赖随函数一起迁入本层（拆分前它是 collector 的顶层导入）。
try:
    import lxml.html
except ImportError as e:  # pragma: no cover
    import sys as _sys

    _sys.exit("缺少依赖 lxml，请先安装: pip install lxml\n%s" % e)

_DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]+[〔\[]?\d{4}[〕\]]\d+号")
_SHIXING_RE = re.compile(r"自\s*(\d{4}[-年./]\d{1,2}[-月./]\d{1,2})\s*[日]?\s*起施行")
BASE = "https://www.nfra.gov.cn"


def html_to_text(html):
    """将正文 HTML（含 Word 命名空间）转为紧凑纯文本。"""
    if not html:
        return ""
    try:
        doc = lxml.html.fromstring(html)
        # 去除脚本/样式
        for bad in doc.xpath("//script | //style"):
            bad.getparent().remove(bad)
        text = doc.text_content()
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", "\n", html)
    # 折叠多余空行与首尾空白
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text


def extract_document_no(text):
    """从正文中抽取发文字号，如 '金监规〔2024〕1号'。

    2026-09-05：优先委托五源统一模块 doc_number（15+ 优先级正则 + 半角 [ ] → 全角〔〕
    规范化 + 脏串清理）；模块不可用/未命中时回退原 _DOC_NO_RE，并对回退值做规范化。
    2026-09-07（case 9）：抽取结果须经废止/失效引用语境校验——若该文号实为被本文件
    废止的他文文号，则返回 None（本文件无字号时留空），禁止错配。
    """
    if not text:
        return None
    try:
        try:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import in_abolish_context as _a
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
        except ImportError:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import in_abolish_context as _a
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
    except Exception:  # pragma: no cover  # noqa: BLE001
        _u = _n = _a = None  # type: ignore[assignment]
    dn = ""
    if _u is not None:
        dn = _u(text) or ""
    if not dn:
        m = _DOC_NO_RE.search(text)
        dn = (_n(m.group(0)) if _n is not None else m.group(0)) if m else ""   # N-67：显式非 None（原 `if _n` 恒真）
    if not dn:
        return None
    if _a is not None and _a(text, dn):
        return None
    return dn


def extract_effective_date(text, builddate=None):
    """优先从正文 '自 X 年 X 月 X 日起施行' 抽取施行日期；否则回退成文日期。"""
    if text:
        m = _SHIXING_RE.search(text)
        if m:
            nums = re.findall(r"\d+", m.group(1))
            if len(nums) >= 3:
                return "%s-%02d-%02d" % (nums[0], int(nums[1]), int(nums[2]))
            return m.group(1)
    if builddate:
        return str(builddate).strip()
    return None


def make_summary(text, max_len=200):
    """正文摘要：取开头若干有效句，截断到 max_len；首句超长时直接截断补齐。"""
    if not text:
        return ""
    parts = re.split(r"[。\n！？]", text)
    parts = [p.strip() for p in parts if p.strip()]
    summary = ""
    for p in parts:
        if len(summary) + len(p) + 1 > max_len:
            if not summary:           # 首句即超长：截断补齐
                summary = p[:max_len]
            break
        summary += p + "。"
        if len(summary) >= max_len:
            break
    return summary[:max_len]


def build_detail_url(doc_id, item_id):
    return "%s/cn/view/pages/ItemDetail.html?docId=%s&itemId=%s" % (BASE, doc_id, item_id)
