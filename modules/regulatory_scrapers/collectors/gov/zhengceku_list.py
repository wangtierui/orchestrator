# -*- coding: utf-8 -*-
"""gov.zhengceku_list — **列表层**：分页 URL 构造、停翻页判据、总页数解析、列表页抓取与条目抽取、HTML 清洗、摘要截取。**纯函数 + 一次 HTTP 抓取**，不触详情、不落盘。

依赖方向：zhengceku_list → （无层间依赖）
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

import os
import re
import sys
from urllib.parse import urljoin

# ----------------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------------
LIST_BASE = "https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# `_SRC_ROOT` 是**路径常量**（各层重复定义，值相同）：list 层自身派生
# `_REPO_ROOT` 需要它，而 list 是最底层——不应为共享一个路径常量而依赖 attach 层。
_SRC_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_SRC_ROOT))              # 仓库根
_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_WS = re.compile(r"[ \t\u3000]{2,}")
_BLANK = re.compile(r"\n{3,}")
_A = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


def list_page_url(index: int) -> str:
    """第 index 页（0-based）的 URL。"""
    return LIST_BASE if index == 0 else LIST_BASE.replace("home.htm", f"home_{index}.htm")


def should_stop_paging(empty_streak: int, stop_after_empty_pages: int, backfill: bool) -> bool:
    """是否停止翻页（**纯函数**，便于单测；N-190）。

    判据与依据：
      · 列表按**发布日期倒序**（第 0 页最新）⇒ 一旦某页**无任何新条目**，其后各页只会更旧，
        不可能再出现新条目 ⇒ 继续翻页是**纯空转**。
      · 原实现 `for i in range(n_pages)` **恒扫全部 629 页**（每页 1 次请求 + `delay` 1.5s），
        实测单轮 **~20+ 分钟**都花在"已知页"上；本函数让增量轮次在**首页无新**即结束。
      · `backfill=True`（--backfill，历史存量补全）**不早停**：该模式的目的正是走完全部页、
        把"在主库但尚无正文"的历史条目补齐（原设计的意图，现与"追新"分离、各自排程）。
    """
    if backfill:
        return False
    return empty_streak >= max(1, int(stop_after_empty_pages or 1))


def total_pages(html: str) -> int:
    """从列表页 JS 变量 `nPageCount` 取总页数（取不到返回 1）。"""
    m = re.search(r"nPageCount\s*=\s*(\d+)", html or "")
    return int(m.group(1)) if m else 1


def _to_text(maybe) -> str:
    """响应体 → 文本，兼容 ① 文本 ② 原始 bytes ③ **未解压的 gzip 体**。

    ⚠️ 踩坑：`crawler_common.robust_get(binary=False)` 以 `errors="replace"` 解码响应体，
    gzip magic 的 `0x8b` 会变成 U+FFFD，既无法按 magic 识别也无法解压 → HTML 解析全空
    （表现为「正文 0 字、附件 0 个」却不报错）。故一律 binary=True 取原始字节后自行处理。
    """
    import gzip
    import zlib
    if isinstance(maybe, bytes):
        b = maybe
    elif isinstance(maybe, str) and maybe[:2] == "\x1f\x8b":
        b = maybe.encode("latin-1", "ignore")
    else:
        return maybe if isinstance(maybe, str) else ""
    if b[:2] == b"\x1f\x8b":
        try:
            b = gzip.decompress(b)
        except Exception:  # noqa: BLE001
            try:
                b = zlib.decompress(b, 16 + zlib.MAX_WBITS)
            except Exception:  # noqa: BLE001
                pass
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return b.decode("utf-8", "replace")


def fetch(url: str, binary: bool = False, referer: str | None = None, timeout: int = 35):
    """HTTP GET。优先 crawler_common.robust_get，失败回退 urllib。"""
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        from std_lib.scraper_std.crawler_common import robust_get  # type: ignore
        st, data = robust_get(url, binary=True, referer=referer or LIST_BASE, timeout=timeout)
        if st == 200 and data:
            if binary:
                return data if isinstance(data, bytes) else data.encode("latin-1", "ignore")
            txt = _to_text(data)
            if txt and "<" in txt:
                return txt
    except Exception:  # noqa: BLE001
        pass
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer or LIST_BASE})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        b = f.read()
    return b if binary else _to_text(b)


def clean_html_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", s)
    s = _TAG.sub("", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = _WS.sub(" ", s)
    s = "\n".join(x.strip() for x in s.split("\n"))
    return _BLANK.sub("\n\n", s).strip()


def _iso(datestr: str) -> str:
    m = _DATE.search(datestr or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def make_summary(full_text: str, length: int = 200) -> str:
    """摘要：与 gov 主链 `parse.make_summary` 同口径（压缩空白后截断）。"""
    s = re.sub(r"\s+", " ", full_text or "").strip()
    return s if len(s) <= length else s[:length]


def scan_list(html: str, base_url: str = LIST_BASE):
    """列表页 → [(绝对URL, 标题, 发布日期ISO)]。按 <li> 块提取，回退按 <a> 提取。"""
    out, seen = [], set()
    for li in _LI.findall(html or ""):
        m = _A.search(li)
        if not m or not re.search(r"content_\d+\.htm", m.group(1)):
            continue
        u = urljoin(base_url, m.group(1).strip())
        if u in seen:
            continue
        seen.add(u)
        out.append((u, clean_html_text(m.group(2)).replace("\n", " ").strip()[:160],
                    _iso(clean_html_text(li))))
    if out:
        return out
    for m in _A.finditer(html or ""):
        href = m.group(1).strip()
        if not re.search(r"content_\d+\.htm", href):
            continue
        u = urljoin(base_url, href)
        if u in seen:
            continue
        seen.add(u)
        out.append((u, clean_html_text(m.group(2)).replace("\n", " ").strip()[:160], ""))
    return out
