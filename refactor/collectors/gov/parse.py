# -*- coding: utf-8 -*-
"""gov 解析/配置/IO 工具（原 gov_parse.py 迁出，逻辑逐函数等价）。

含：子源登记表 SOURCES、ScrapeConfig、build_config、文本/日期/文号解析、
merge_with_master（旧主库 ∪ 新条目增量合并）。
仅 import 路径改为相对导入，仓库引导与 pbc 同口径。
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import requests  # noqa: F401
    from bs4 import BeautifulSoup  # noqa: F401
    from requests.adapters import HTTPAdapter  # noqa: F401
    from urllib3.util.retry import Retry  # noqa: F401
except ImportError as e:  # pragma: no cover
    sys.stderr.write(
        "缺少依赖，请先执行：\n"
        "  pip install requests beautifulsoup4 lxml\n"
        f"原始错误：{e}\n"
    )
    sys.exit(2)

# 附件下载 + 文本抽取
try:
    from .attachments import fetch_gov_attachments
except ImportError:  # pragma: no cover
    def fetch_gov_attachments(*a, **k):
        return [], ""

LOG = logging.getLogger("gov_scraper")

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
from ..lib.http import USER_AGENTS

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块）
from ..lib.doc_number import extract_doc_number as _unified_doc_number

from ..storage import get_storage, StorageSettings, open_storage


DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

CONTENT_SELECTORS = [
    ".law-chapter",        # 司法部行政法规详情正文
    "#content",
    "#UCAP-CONTENT",
    ".content-text",
    ".pages_content",
    ".article",
    "#zoom",
    "#article-content",
    ".TRS_Editor",
    "div.content",
]

TITLE_SELECTORS = ["h1", ".article-title", "#title", ".title", "h2"]

_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)

CSV_COLUMNS = [
    "title", "category", "publish_date", "pub_date_original", "effective_date",
    "issue_organ", "document_number", "detail_url", "summary", "source",
]

SOURCES = {
    # 2026-09-04：仅保留 xzfgk（行政法规库）。flk（国家法律法规数据库）正文存站内
    # OBS 外部不可达、仅元数据无正文，已停止抓取并清理（见 docs/gov正文缺失排查报告）。
    "xzfgk": ("https://www.gov.cn/zhengce/xzfgk/", "行政法规"),
    # 2026-09-15：纳入「国务院政策文件库·国务院部门文件」（zhengceku/bmwj，629 页）
    "zhengceku": ("https://www.gov.cn/zhengce/zhengceku/bmwj/home.htm", "部门文件"),
}
# ⚠️ 本 dict 为 gov 源子源登记表的**唯一事实源**；gov_collector.py 从此处导入。


@dataclass
class ScrapeConfig:
    source: str
    base_url: str
    category: str
    out_dir: str = "data/raw"
    max_pages: int = 0          # 0 = 不限制
    max_items: int = 0          # 0 = 不限制（仅列表条目数）
    fetch_details: bool = True
    details_limit: int = 0      # 仅对前 N 条抓取详情正文（0=全部，需配合 fetch_details）
    resume: bool = False        # 增量续抓：基于 out_dir 最新同源输出跳过已抓条目
    delay_min: float = 1.5      # 列表/详情请求间最小延时（秒）
    delay_max: float = 3.5      # 最大延时（秒）
    timeout: int = 30
    retries: int = 4
    summary_len: int = 200
    # 详情阶段断点落盘间隔（条；0=不落暂存）。
    checkpoint_every: int = 200
    # ---- N-190（2026-10-08）**增量治理**参数（两子源共用）----
    stop_after_empty_pages: int = 1
    #: 历史存量补全模式：**忽略早停**、扫完全部列表页（月度作业用；不进周度增量）。
    backfill: bool = False
    #: 详情阶段**软预算**（秒；0=不限）——到达即停止取新详情并**正常落盘退出**。
    max_seconds: float = 0.0


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

    2026-09-05：优先委托五源统一模块 ``std_lib.scraper_std.doc_number``；
    模块缺失或未命中时回退原本地正则，保证抓取行为不劣化。
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


def make_summary(full_text: str, length: int = 200) -> str:
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
    且非导航（链接文本占比不过高）的内容块。"""
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


def build_config(args) -> ScrapeConfig:
    base, category = SOURCES[args.source]
    return ScrapeConfig(
        source=args.source,
        base_url=base,
        category=category,
        out_dir=args.out_dir,
        max_pages=args.max_pages,
        max_items=args.max_items,
        fetch_details=not args.no_details,
        details_limit=args.details_limit,
        resume=args.resume,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        timeout=args.timeout,
        retries=args.retries,
        summary_len=args.summary_len,
        checkpoint_every=getattr(args, "checkpoint_every", 200),
        # N-190：增量治理参数（老调用方无这些属性时退回默认，保持向后兼容）
        stop_after_empty_pages=int(getattr(args, "stop_after_empty_pages", 1) or 1),
        backfill=bool(getattr(args, "backfill", False)),
        max_seconds=float(getattr(args, "max_seconds", 0.0) or 0.0),
    )


# ── 治理步骤（采集边界，非采集逻辑）：源内增量合并 ──
def merge_with_master(new_records: list[dict[str, Any]], out_dir: str, storage=None) -> list[dict[str, Any]]:
    """增量续抓合并：现主库旧记录 ∪ 本次新条目（detail_url 去重，新优先）。

    读回走 storage（file/postgres 统一）；不写文件（由 write_outputs 落盘）。
    """
    if storage is None:
        storage = open_storage("gov", out_dir)
    try:
        old = storage.load_master().items
    except Exception as e:  # noqa: BLE001
        LOG.warning("【merge】读取现主库失败，仅写本次抓取结果：%s", e)
        return new_records
    merged: dict[str, dict[str, Any]] = {}
    for r in old:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    for r in new_records:
        merged[str(r.get("detail_url") or r.get("title"))] = r
    LOG.info("【merge】现主库 %d 条 + 本次 %d 条 → 合并 %d 条",
             len(old), len(new_records), len(merged))
    return list(merged.values())


__all__ = ["ScrapeConfig", "_is_https_scheme_upgrade", "_longest_text_block", "build_config", "clean_text",
           "decode_html", "extract_date", "extract_doc_number",
           "extract_issue_organ", "make_summary", "merge_with_master"]
