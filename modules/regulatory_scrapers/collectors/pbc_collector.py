#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国人民银行条法司 — 法律法规 / 政策规章 / 规范性文件 自动抓取脚本
==================================================================================
功能：
  1. 对四个栏目（国家法律 / 行政法规 / 部门规章 / 规范性文件）自动遍历静态分页；
  2. 解析列表页提取条目名称、详情/附件链接；
  3. 对每条目请求详情页（HTML 正文页）或直接下载附件（.doc/.docx 等），
     提取 标题、发布日期、发文字号、发文机关、生效日期、正文全文、内容摘要；
  4. 以结构化 JSON + CSV 输出，并生成抓取统计报告。

设计要点（对齐数据治理严谨性要求）：
  - 依赖：标准库 + python-docx / pdfplumber / openpyxl（附件解析）；LibreOffice(headless)
    可选，启用后可将 .doc/.wps/.rtf/.ceb 转 docx 提取正文，否则该格式降级为保真下载；
  - 分页机制自适应：从列表页隐藏字段 `article_paging_list_hidden` 动态提取
    moduleid 与 totalpage，按 `{栏目目录}/{moduleid}-{n}.html` 规律生成各分页 URL，
    不硬编码任何栏目专属参数；
  - 两种条目类型全覆盖：HTML 详情页解析 + 附件（.doc/.docx/.pdf 等）下载，
    不跳过任一类型（满足代码解析完整性约束）；
  - 反爬与稳定性：UA 随机轮换、Referer 携带、 polite 随机延时、指数退避重试、
    超时控制、单条失败不影响整体；
  - 可断点续跑：已存在输出文件时，跳过已成功抓取的详情链接。

用法：
  python pbc_collector.py                 # 抓取全部四个栏目
  python pbc_collector.py --category 国家法律   # 仅抓取指定栏目
  python pbc_collector.py --max-items 50       # 限制条目数（调试用）
  python pbc_collector.py --delay 1.2 --out ./out
  python pbc_collector.py --no-attachments     # 不下载附件正文（仅记录链接）
"""


# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# pbc 列表/详情页均为静态 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（status==200）落盘；
# 二进制附件（binary=True）已落盘 ATTACHMENTS_DIR，不经文本缓存。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache

    def structured_table_fields(data, name="", *, kind=None):  # pragma: no cover
        return {}

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("pbc")，单物理根）；path 显式可覆盖。"""
    global _RESP_TEXT
    _RESP_TEXT = bind_source_cache("pbc", "text", root=path)
    if offline and _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(flag)

def _cache_ep(url: str) -> str:
    """缓存键：以「完整 URL 的 sha1」作为**裸端点名**（契约允许 endpoint 为裸端点名）。

    ⚠️ **不可直接把 URL 交给 TextResponseCache**：其命名算法取 URL **末段**作 ep
    （与 nfra 原 `_cache_path` 一致，保证历史缓存可复用），而 pbc 的栏目页 /
    分页页 / 详情页 URL **均以 `index.html` 结尾**且 `params` 为空 → 不同页面会
    撞进**同一个**缓存文件，导致详情页读到栏目页缓存正文（P0 数据错乱）。
    故此处改用 URL 级唯一键（sha1 前 16 位），彻底消除末段同名碰撞。
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

# ----------------------------------------------------------------------------------
# 栏目配置：仅需提供各栏目的列表首页 URL，分页参数运行时自动探测
# ----------------------------------------------------------------------------------
BASE = "https://www.pbc.gov.cn"

# 产物目录统一（Plan B 阶段 2b + 2026-09-08 docs_root 统一根）
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
from std_lib.scraper_std.cache_store import docs_root  # noqa: E402

ATTACHMENTS_DIR = docs_root("pbc", "attachments")
CATEGORIES = [
    {"name": "国家法律", "index_url": BASE + "/tiaofasi/144941/144951/index.html"},
    {"name": "行政法规", "index_url": BASE + "/tiaofasi/144941/144953/index.html"},
    {"name": "部门规章", "index_url": BASE + "/tiaofasi/144941/144957/index.html"},
    {"name": "规范性文件", "index_url": BASE + "/tiaofasi/144941/3581332/index.html"},
]

# 常见发文机关关键词（用于从正文启发式识别，按出现优先级排列）
ISSUING_AUTHORITIES = [
    "中国人民银行", "国务院", "全国人民代表大会常务委员会", "全国人民代表大会",
    "中国银行保险监督管理委员会", "中国银行监督管理委员会", "中国保险监督管理委员会",
    "国家外汇管理局", "财政部", "国家金融监督管理总局", "中国证券监督管理委员会",
    "最高人民法院", "最高人民检察院",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
]

# 正则表达式预编译
RE_PAGING = re.compile(
    r'name="article_paging_list_hidden"\s+moduleid="([^"]+)"\s+modulekey="[^"]*"\s+totalpage="(\d+)"'
)
RE_ENTRY = re.compile(
    r'<a\s+href="(/tiaofasi/[^"]+)"[^>]*?\btitle="([^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
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
RE_TAG = re.compile(r'<[^>]+>')
RE_WS = re.compile(r'\s+')

ATTACH_EXT = (".doc", ".docx", ".pdf", ".xls", ".xlsx", ".wps", ".ceb", ".rtf")

# ----------------------------------------------------------------------------------
# 附件解析：多格式路由 + LibreOffice 自动探测 + 优雅降级
# ----------------------------------------------------------------------------------
def safe_filename(title, ext, url):
    """生成本地安全文件名：hash 前缀防重名 + 可读标题 + 小写扩展名。

    （共享库对齐审计 阶段 1）核心逻辑（去非法字符 / 限长 / URL 哈希前缀）委托
    ``crawler_common.safe_filename``，本函数仅补扩展名；**保留 pbc 原有**限长 60**，
    签名与返回结构不变。与 ``backfill_pdfs.safe_filename`` 同口径，二者现共用共享库实现。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.crawler_common import safe_filename as _cc_safe_filename
    base = _cc_safe_filename(title, url, 60)
    ext = (ext or "bin").lower()
    return f"{base}.{ext}"

def detect_magic(bdata):
    """按文件头 magic bytes 判断实际格式，处理扩展名与内容不符的'格式不匹配'异常。"""
    if not bdata:
        return None
    if bdata[:4] == b"%PDF":
        return "pdf"
    if bdata[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"          # docx / xlsx 均为 zip 容器
    if bdata[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"          # .doc / 旧 .xls 为 OLE 复合文档
    return None

from std_lib.scraper_std.doc_convert import (  # noqa: E402 五源共享 doc→docx（2026-09-08）
    find_libreoffice,
)

LO_PATH = find_libreoffice()
LO_AVAILABLE = LO_PATH is not None


def detect_libreoffice():
    """兼容别名：委托共享 find_libreoffice（五源统一探测）。"""
    return find_libreoffice()


def convert_with_libreoffice(doc_path):
    """兼容别名：委托共享 doc_to_docx（.doc/.wps/.rtf/.ceb → .docx，headless）。不可用返回 None。"""
    from std_lib.scraper_std.doc_convert import doc_to_docx  # noqa: PLC0415
    return doc_to_docx(doc_path)

def extract_pdf_text(data):
    """用统一 OCR 模块抽取 PDF 文本（文本层优先，扫描件走 OCR 引擎链）。

    修复：原实现仅用 pdfplumber 抽文本层、无 OCR 兜底；现经 std_lib/scraper_std/
    ocr_engine，文本层充分即直接采用（避免对正常文本做 OCR），否则自动走 PaddleOCR
    3.7.0（默认）+ Tesseract v5 降级，并规避 Paddle#77340 oneDNN/PIR 崩溃
    （disable_mkldnn 默认开启）。返回抽取文本（str）或 None（无文本层且 OCR 失败）。
    """
    import tempfile
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.ocr_engine import get_ocr
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="pbc_main_ocr_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        res = get_ocr().extract_pdf(tmp, force_ocr=False)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if res.success and res.text.strip():
        return res.text
    return None

def extract_xls_text(data):
    try:
        import io

        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    out.append(" | ".join(cells))
        return "\n".join(out).strip()
    except Exception:
        return None

# ----------------------------------------------------------------------------------
# 网络请求层：带重试退避、UA 轮换、超时、频率控制
# ----------------------------------------------------------------------------------
class Fetcher:
    def __init__(self, min_delay=0.8, max_delay=1.6, timeout=30, retries=3):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.retries = retries
        self._last_req = 0.0

    def _throttle(self):
        """礼貌限速：请求间隔随机化，避免固定频率触发风控。"""
        elapsed = time.time() - self._last_req
        gap = random.uniform(self.min_delay, self.max_delay)
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_req = time.time()

    def get(self, url, referer=None, binary=False, timeout=None):
        """
        带指数退避重试的 GET 请求，返回 (status, content)。
        status 为 HTTP 状态码；失败时 status=None，content 为错误说明字符串。

        文本页（binary=False）委托通用缓存层 TextResponseCache：命中读盘跳过网络、
        离线缺失抛 _OfflineMiss、仅成功响应（status==200）落盘；二进制附件
        （binary=True）已落盘 ATTACHMENTS_DIR，不经文本缓存。
        """
        last_err = None
        url = safe_url(url)
        # 1) 缓存命中（仅文本页）：续跑 / 离线
        if not binary and _RESP_TEXT is not None:
            cp = _RESP_TEXT.path(_cache_ep(url), {})
            if os.path.exists(cp):
                try:
                    with open(cp, encoding="utf-8") as fh:
                        return 200, fh.read()
                except Exception:
                    pass  # 缓存损坏则重新请求
            if _RESP_TEXT.offline:
                raise _OfflineMiss(url)
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", random.choice(USER_AGENTS))
                req.add_header("Accept", "*/*")
                req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
                if referer:
                    req.add_header("Referer", referer)
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    status = resp.status
                    data = resp.read()
                    if binary:
                        return status, data
                    # 解码：优先按 HTTP 头，回退 utf-8
                    enc = resp.headers.get_content_charset() or "utf-8"
                    try:
                        text = data.decode(enc, errors="replace")
                    except LookupError:
                        text = data.decode("utf-8", errors="replace")
                    # 写入文本缓存（仅成功响应且非空）
                    if _RESP_TEXT is not None and status == 200 and text:
                        try:
                            _RESP_TEXT.put(_cache_ep(url), {}, text)
                        except Exception:
                            pass
                    return status, text
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                # 确定性错误不重试；限流/服务暂不可用则退避后重试
                if e.code in (403, 404, 410):
                    return e.code, None
                if e.code in (429, 503):
                    retry_after = e.headers.get("Retry-After")
                    wait = int(retry_after) if (retry_after and retry_after.isdigit()) else min(2 ** attempt * 2, 16)
                    if attempt < self.retries:
                        time.sleep(wait)
                    continue
            except (urllib.error.URLError, ConnectionError, TimeoutError, Exception) as e:
                last_err = f"{type(e).__name__}: {e}"
            # 通用退避：2^(attempt) 秒
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 8))
        return None, last_err

# ----------------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------------
def strip_tags(s):
    if not s:
        return ""
    s = RE_TAG.sub("", s)
    s = html.unescape(s)
    return RE_WS.sub(" ", s).strip()

def clean_text(s):
    return RE_WS.sub(" ", html.unescape(s or "")).strip()

def normalize_digits(s):
    """将全角数字 ０-９ 归一为半角 0-9，统一日期口径。"""
    if not s:
        return s
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    return s.translate(trans)

def is_attachment(url):
    low = url.lower().split("?")[0]
    return any(low.endswith(ext) for ext in ATTACH_EXT)

def safe_url(url):
    """
    对非 ASCII 字符（如中文文件名）的 URL 路径/查询做百分号编码，
    避免 urllib 抛出 'unknown url type' / 'ascii codec' 错误。
    仅编码 path 与 query，保留 scheme/netloc 及 '/'、'=&' 等分隔符。
    """
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/")
    query = urllib.parse.quote(parts.query, safe="=&")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))

# ----------------------------------------------------------------------------------
# 列表页解析
# ----------------------------------------------------------------------------------
def parse_listing(html_text, column_dir):
    """
    返回 (moduleid, totalpage, [(title, abs_url), ...])
    column_dir: 栏目目录的绝对 URL（不含 /index.html）
    """
    paging = RE_PAGING.search(html_text)
    moduleid, totalpage = (paging.group(1), int(paging.group(2))) if paging else (None, 1)

    entries = []
    seen = set()
    for m in RE_ENTRY.finditer(html_text):
        href = m.group(1)
        title = clean_text(m.group(2)) or clean_text(strip_tags(m.group(3)))
        if not title:
            continue
        abs_url = urllib.parse.urljoin(BASE + "/", href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        entries.append((title, abs_url))
    return moduleid, totalpage, entries

def listing_page_urls(index_url, moduleid, totalpage):
    """
    生成列表所有分页 URL（第 1 页为 index.html，后续为 {short}-{n}.html）。
    关键修正：easysite 分页实际使用的是 moduleid 的前 8 位字符，而非隐藏字段里的
    完整 moduleid。数字栏目（如 21885）前 8 位即自身，故原规律碰巧成立；
    hex 栏目（如 3b3662a6db7145c0a025d2d410570ae1）必须用前 8 位 '3b3662a6' 拼接，
    否则全部 404。统一截断前 8 位可同时覆盖两类栏目。
    """
    column_dir = index_url.rsplit("/index.html", 1)[0]
    urls = [index_url]
    if moduleid and totalpage > 1:
        short = moduleid[:8]
        for n in range(2, totalpage + 1):
            urls.append(f"{column_dir}/{short}-{n}.html")
    return urls

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
    except Exception:  # pragma: no cover
        _u = _n = None
    if _u is not None:
        dn = _u(blob)
        if dn:
            return dn
    m = RE_DOC_NUMBER.search(blob)
    if m:
        return _n(m.group(0)) if _n else clean_text(m.group(0))
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

def extract_docx_text(data):
    """尝试用 python-docx 提取 .docx 文本；不可用则返回 None。"""
    try:
        from docx import Document
    except Exception:
        return None
    import io
    try:
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
    except Exception:
        return None

# ----------------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------------
def scrape_category(cat, fetcher, args, done_urls, existing_map=None):
    name = cat["name"]
    index_url = cat["index_url"]
    records = []

    # 1) 抓取首页，探测分页参数
    try:
        status, txt = fetcher.get(index_url, referer=BASE + "/tiaofasi/144941/index.html")
    except _OfflineMiss:
        print(f"  [offline] 栏目《{name}》首页缓存缺失，跳过", file=sys.stderr)
        return records
    if status != 200 or not txt:
        print(f"  [!] 栏目《{name}》首页获取失败 status={status}", file=sys.stderr)
        return records

    moduleid, totalpage, entries = parse_listing(txt, index_url.rsplit("/index.html", 1)[0])
    print(f"  [+] 栏目《{name}》探测到 moduleid={moduleid} totalpage={totalpage} 首页条目={len(entries)}")

    # 2) 遍历所有分页，收集条目
    page_urls = listing_page_urls(index_url, moduleid, totalpage)
    for pu in page_urls[1:]:  # 第 1 页已处理
        try:
            st, ptxt = fetcher.get(pu, referer=index_url)
        except _OfflineMiss:
            print(f"  [offline] 分页缓存缺失 {pu}，停止翻页", file=sys.stderr)
            break
        if st == 200 and ptxt:
            _, _, more = parse_listing(ptxt, index_url.rsplit("/index.html", 1)[0])
            entries.extend(more)
        else:
            print(f"  [!] 分页获取失败 {pu} status={st}", file=sys.stderr)

    # 去重（跨页可能重复）
    uniq = {}
    for t, u in entries:
        uniq[u] = t
    print(f"  [+] 合并后去重条目数：{len(uniq)}")

    # 3) 逐条抓取详情 / 附件
    for i, (url, title) in enumerate(uniq.items(), 1):
        if args.max_items and len(records) >= args.max_items:
            break
        rec = {
            "category": name,
            "title": title,
            "detail_url": url,
            "link_type": "attachment" if is_attachment(url) else "html",
            "file_type": (url.rsplit(".", 1)[-1].lower() if is_attachment(url) else None),
            "local_path": None,
            "publish_date": None,
            "document_number": None,
            "issuing_authority": None,
            "effective_date": None,
            "content": "",
            "summary": "",
            "fetch_status": "pending",
            "error": None,
        }

        if url in done_urls:
            # 断点续跑：复用已成功抓取的完整旧记录（含正文/字段），避免重跑时丢失内容
            old = (existing_map or {}).get(url)
            if old:
                rec = dict(old)
                rec["fetch_status"] = "skipped_existing"
            else:
                rec["fetch_status"] = "skipped_existing"
            records.append(rec)
            continue

        if rec["link_type"] == "html":
            try:
                st, dtxt = fetcher.get(url, referer=index_url)
            except _OfflineMiss:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = "offline: 详情缓存缺失"
                records.append(rec)
                continue
            if st == 200 and dtxt:
                try:
                    d = parse_detail(dtxt)
                    rec.update({k: d[k] for k in (
                        "title", "publish_date", "document_number",
                        "issuing_authority", "effective_date", "content")})
                    rec["fetch_status"] = "ok"
                except Exception as e:
                    rec["fetch_status"] = "parse_error"
                    rec["error"] = f"{type(e).__name__}: {e}"
            else:
                rec["fetch_status"] = "fetch_failed"
                rec["error"] = f"status={st}; {dtxt}"
        else:
            # 附件：下载原始文件到本地（供前端显示/下载），并按格式路由解析正文。
            # 覆盖全部附件类型，绝不跳过；解析失败/格式不支持时降级为保真下载并标注。
            if args.no_attachments:
                rec["fetch_status"] = "attachment_skipped"
            else:
                try:
                    st, bdata = fetcher.get(url, referer=index_url, binary=True, timeout=60)
                except _OfflineMiss:
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = "offline: 附件缓存缺失"
                    records.append(rec)
                    continue
                if not (st == 200 and bdata):
                    rec["fetch_status"] = "fetch_failed"
                    rec["error"] = f"附件下载失败 status={st}"
                else:
                    # 落盘原始文件（中文名安全编码）
                    fname = safe_filename(rec["title"], rec["file_type"], url)
                    adir = os.path.join(ATTACHMENTS_DIR, name)
                    os.makedirs(adir, exist_ok=True)
                    fpath = os.path.join(adir, fname)
                    with open(fpath, "wb") as fh:
                        fh.write(bdata)
                    rec["local_path"] = os.path.relpath(fpath, REPO_ROOT).replace("\\", "/")
                    # 格式路由解析（异常隔离：单条附件解析异常不中断整体运行）
                    ft = rec["file_type"]
                    parsed = None
                    try:
                        if ft == "docx":
                            parsed = extract_docx_text(bdata)
                        elif ft == "pdf":
                            parsed = extract_pdf_text(bdata)
                        elif ft in ("xls", "xlsx"):
                            parsed = extract_xls_text(bdata)
                        elif ft in ("doc", "wps", "rtf", "ceb"):
                            if LO_AVAILABLE:
                                conv = convert_with_libreoffice(fpath)
                                if conv and os.path.exists(conv):
                                    with open(conv, "rb") as cf:
                                        parsed = extract_docx_text(cf.read())
                        # 表格结构化（2026-09-08 仿 supp 打通）：xlsx/docx 附件表 → rec 表键；
                        # .doc/wps/rtf/ceb 由 helper 内 doc→docx（共享 doc_convert）后取表
                        if ft in ("docx", "xls", "xlsx", "doc", "wps", "rtf", "ceb"):
                            rec.update(structured_table_fields(bdata, fname))
                    except Exception as e:
                        rec["error"] = ("附件正文解析异常：%s: %s；已保存原始文件供下载"
                                        % (type(e).__name__, e))
                        parsed = None
                    # 结果判定
                    if parsed:
                        rec["content"] = parsed
                        rec["fetch_status"] = "ok"
                    else:
                        rec["fetch_status"] = "attachment_saved"
                        if ft in ("doc", "wps", "rtf", "ceb"):
                            rec["error"] = "正文解析需 LibreOffice 环境（当前不可用），已保存原始文件供下载"
                        elif ft in ("xls", "xlsx"):
                            rec["error"] = "表格正文提取失败，已保存原始文件供下载"
                        elif ft == "pdf":
                            rec["error"] = "PDF 文本提取失败，已保存原始文件供下载"
                        else:
                            rec["error"] = f".{ft} 暂不支持正文解析，已保存原始文件供下载"

        # 内容摘要（前 200 字）
        if rec["content"]:
            rec["summary"] = rec["content"][:200]
        records.append(rec)
        if i % 20 == 0 or i == len(uniq):
            print(f"  [.] 《{name}》进度 {i}/{len(uniq)} 成功={sum(1 for r in records if r['fetch_status']=='ok')}")
    return records

def save_outputs(records, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "pbc_laws.json")
    csv_path = os.path.join(out_dir, "pbc_laws.csv")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    fields = ["category", "title", "detail_url", "link_type", "file_type", "local_path",
              "publish_date", "document_number", "issuing_authority",
              "effective_date", "content", "summary", "fetch_status", "error"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow(r)
    return json_path, csv_path

def build_report(records):
    by_cat = {}
    for r in records:
        c = r["category"]
        by_cat.setdefault(c, {"total": 0, "ok": 0, "attach": 0, "fail": 0})
        by_cat[c]["total"] += 1
        if r["fetch_status"] == "ok":
            by_cat[c]["ok"] += 1
        elif r["fetch_status"] in ("attachment_link_only", "attachment_no_text", "attachment_skipped", "attachment_saved"):
            by_cat[c]["attach"] += 1
        elif r["fetch_status"] in ("fetch_failed", "parse_error"):
            by_cat[c]["fail"] += 1
    lines = ["# 抓取统计报告", f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"总条目数：{len(records)}", ""]
    for c, s in by_cat.items():
        lines.append(f"- {c}：共 {s['total']} 条，正文提取成功 {s['ok']}，附件保真 {s['attach']}，失败 {s['fail']}")
    fail_list = [r for r in records if r["fetch_status"] in ("fetch_failed", "parse_error")]
    if fail_list:
        lines.append("")
        lines.append("## 失败条目")
        for r in fail_list:
            lines.append(f"  - [{r['category']}] {r['title']} -> {r['detail_url']} ({r['error']})")
    return "\n".join(lines)

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
import atexit  # noqa: E402

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock


def main():
    ap = argparse.ArgumentParser(description="中国人民银行条法司法规抓取脚本")
    ap.add_argument("--category", help="仅抓取指定栏目名称（如 国家法律）")
    ap.add_argument("--max-items", type=int, default=0, help="限制总条目数（调试用）")
    ap.add_argument("--delay", type=float, default=1.0, help="平均请求间隔（秒）")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw"), help="输出目录（默认统一 data/raw/：regulatory_scrapers/data/raw）")
    ap.add_argument("--no-attachments", action="store_true", help="不下载附件正文，仅记录链接")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(pbc) 统一解析"
                         "→ modules/regulatory_scrapers/cache/pbc（单物理根）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

    # —— 调度可靠性：跨进程单实例锁防并发重复（统一 fs_lock 公共库，N-8）——
    _lock = fs_lock.ProcessLock(os.path.join(args.out, "scrape.lock"))
    if not _lock.acquire():
        print("[!] 已有抓取任务在运行（锁存在且 PID 存活），本次跳过以避免重复抓取。",
              file=sys.stderr)
        sys.exit(0)
    atexit.register(_lock.release)

    min_d = max(0.4, args.delay * 0.8)
    max_d = args.delay * 1.6
    fetcher = Fetcher(min_delay=min_d, max_delay=max_d, timeout=30, retries=3)

    # 断点续跑：读取已有 JSON
    done_urls = set()
    existing_map = {}
    out_json = os.path.join(args.out, "pbc_laws.json")
    if os.path.exists(out_json):
        try:
            with open(out_json, encoding="utf-8") as f:
                existing = json.load(f)
            for r in existing:
                if r.get("fetch_status") == "ok":
                    done_urls.add(r["detail_url"])
                    existing_map[r["detail_url"]] = r
            print(f"[*] 检测到已有结果，跳过 {len(done_urls)} 条已成功条目（断点续跑）")
        except Exception:
            pass

    cats = [c for c in CATEGORIES if (not args.category or c["name"] == args.category)]
    if not cats:
        print(f"[!] 未找到栏目：{args.category}", file=sys.stderr)
        sys.exit(1)

    all_records = []
    for cat in cats:
        print(f"\n=== 开始抓取栏目：《{cat['name']}》 ===")
        recs = scrape_category(cat, fetcher, args, done_urls, existing_map)
        all_records.extend(recs)
        # 逐栏目增量落盘：即使进程被中断，已完成栏目数据不丢失，下次运行可断点续跑
        save_outputs(all_records, args.out)
        print(f"  [✓] 《{cat['name']}》已落盘，累计 {len(all_records)} 条")

    if args.max_items:
        all_records = all_records[:args.max_items]

    json_path, csv_path = save_outputs(all_records, args.out)
    report = build_report(all_records)
    report_path = os.path.join(os.path.dirname(args.out), "reports", "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print("\n" + report)
    print(f"\n[✓] 完成。JSON: {json_path}\n    CSV : {csv_path}\n    报告: {report_path}")

if __name__ == "__main__":
    main()
