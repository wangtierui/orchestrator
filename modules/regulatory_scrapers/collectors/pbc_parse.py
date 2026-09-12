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
import html
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# pbc 列表/详情页均为静态 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（status==200）落盘；
# 二进制附件（binary=True）已落盘 ATTACHMENTS_DIR，不经文本缓存。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: F401
    from std_lib.scraper_std.rich_object import rich_object_fields
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

    def structured_table_fields(data, name="", *, kind=None):  # pragma: no cover
        return {}

    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):  # pragma: no cover
        return {}

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名



BASE = "https://www.pbc.gov.cn"

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

from std_lib.scraper_std.doc_convert import (  # noqa: E402 五源共享 doc→docx（2026-09-08）
    find_libreoffice,
)

LO_PATH = find_libreoffice()

LO_AVAILABLE = LO_PATH is not None


_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)


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


__all__ = ["clean_text", "convert_with_libreoffice", "detect_libreoffice", "detect_magic", "extract_docx_text", "extract_pdf_text", "extract_xls_text", "is_attachment", "normalize_digits", "safe_filename", "safe_url", "strip_tags"]
