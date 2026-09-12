#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gov_regulations_scraper.py
===========================

自动抓取行政法规库（xzfg.moj.gov.cn）的行政法规条目，并结构化输出（含正文全文）：
  - xzfgk —— 行政法规库（司法部 xzfg.moj.gov.cn）
             列表与详情页均为服务端渲染（HTML），可用 requests 直接抓取；
             详情页含正文全文/发文字号/生效日期/发文机关。

2026-09-04：仅保留 xzfgk 数据源。flk（国家法律法规数据库，flk.npc.gov.cn）
正文原文存储于站内 OBS 外部不可达，仅元数据无正文，已停止抓取并清理相关代码。

通用能力：
  - 会话复用 + 连接池；请求头 / User-Agent 轮换；
  - 随机延时 + 指数退避重试，规避反爬、控制请求频率；
  - 自动遍历分页（?PageIndex= 或 点击"下一页"）；
  - 对每条目二次请求详情页，抽取正文全文、发文字号、生效日期、发文机关；
  - 输出结构化 JSON（含全文）与 CSV 表格（标题/类别/发布日期/正文链接/摘要）；
  - 完善的异常捕获与运行日志，单条失败不影响整体。

用法示例：
  # 完整抓取行政法规库（含全部详情正文），输出 gov_laws.json/.csv（覆盖更新）
  python scraper.py

  # 快速验证（前 2 页、5 条详情）
  python scraper.py --max-pages 2 --max-items 5

依赖：
  pip install requests beautifulsoup4 lxml
"""

from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import csv
import json
import logging
import os
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

# 附件下载 + 文本抽取（修复：gov 此前完全不抓附件）
try:
    from gov_fetch_attachments import fetch_gov_attachments
except ImportError:  # pragma: no cover
    def fetch_gov_attachments(*a, **k):
        return [], ""

LOG = logging.getLogger("gov_scraper")

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

# 常用桌面浏览器 UA 池（轮换以降低被识别为脚本的概率）
# （共享库对齐审计 阶段 4）实测：本文件原有 UA 池与 ``crawler_common.USER_AGENTS``
# **逐字相同**（md5 一致、n=5），故改为复用共享库，消除重复副本；
# 沿用 nfra 既有「try 导入 + 缺模块回退单 UA」降级样板，避免缺依赖时启动失败。
_STD_LIB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STD_LIB_ROOT not in sys.path:
    sys.path.insert(0, _STD_LIB_ROOT)
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS
except ImportError:  # pragma: no cover
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]

# 统一文号提取器（2026-09-05 五源共享：doc_number 模块，15+ 优先级正则 + 规范化）
try:
    from std_lib.scraper_std.doc_number import extract_doc_number as _unified_doc_number
except ImportError:  # pragma: no cover
    _unified_doc_number = None

# 默认请求头（模拟真实浏览器）
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# gov xzfgk 列表/详情页均为服务端渲染 HTML → 委托 TextResponseCache（存储 HTML 文本）；
# 命中读盘跳过网络、离线缺失抛 OfflineMiss、仅成功响应（非空）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: E402, F401
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名



DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意：不要包含 br（brotli）。本机 urllib3 未安装 brotli 解码器，
    # 一旦服务器回 brotli 压缩，resp.content 会是未解压乱码字节，
    # decode_html 解出乱码 → 选择器全部落空 → 详情正文=0（海量 gov 子站因此被误判为空）。
    # 去掉 br 后服务器回退 gzip，urllib3 可正常解压。
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
}


_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)


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


def load_resume(out_dir: str, source: str):
    """读取 out_dir 下最新的同源输出 JSON，返回 (已抓条目key集合, 已有正文的detail_url集合)。
    用于 --resume 增量续抓：跳过已存在的列表条目，且对已含 full_text 的条目不再抓详情。"""
    files = [os.path.join(out_dir, "gov_laws.json")] if os.path.exists(
        os.path.join(out_dir, "gov_laws.json")) else []
    seen, detailed = set(), set()
    if not files:
        return seen, detailed
    try:
        data = json.load(open(files[-1], encoding="utf-8"))
        for r in data.get("records", []):
            seen.add((r.get("title", ""), r.get("detail_url", "")))
            if r.get("full_text"):
                detailed.add(r.get("detail_url", ""))
        LOG.info("【resume】已从 %s 加载 %d 条已抓条目（其中 %d 条已有正文）",
                 os.path.basename(files[-1]), len(seen), len(detailed))
    except Exception as e:
        LOG.warning("【resume】读取历史输出失败，将全新抓取：%s", e)
    return seen, detailed


def write_outputs(records: list[dict[str, Any]], cfg: ScrapeConfig,
                  source_label: str | None = None,
                  write_csv: bool = False) -> dict[str, str]:
    os.makedirs(cfg.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(cfg.out_dir, "gov_laws.json")
    csv_path = os.path.join(cfg.out_dir, "gov_laws.csv")

    # JSON：保留全文与原始字段
    _payload = {
        "source": cfg.source,
        "category": cfg.category,
        "captured_at": stamp,
        "count": len(records),
        "records": records,
    }
    _tmp = json_path + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        json.dump(_payload, f, ensure_ascii=False, indent=2)
    os.replace(_tmp, json_path)

    # CSV：仅 --csv 显式开启时输出表格视图（默认仅 JSON 主库，2026-09-09 规范）
    if write_csv:
        _tmpc = csv_path + ".tmp"
        with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
        os.replace(_tmpc, csv_path)

    LOG.info("输出完成：\n  JSON: %s%s", json_path,
             ("\n  CSV : %s" % csv_path) if write_csv else "")
    return {"json": json_path, "csv": csv_path if write_csv else ""}


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
    )


def merge_with_master(new_records: list[dict[str, Any]], out_dir: str) -> list[dict[str, Any]]:
    """增量续抓合并：现主库旧记录 ∪ 本次新条目（detail_url 去重，新优先）。

    2026-09-05：定长覆盖更新 + ``--resume`` 使本次仅产出新增条目，
    若直接覆盖写会丢失历史条目；合并后覆盖保证主库 = 历史全量 + 本周新增/更新。
    """
    master_path = os.path.join(out_dir, "gov_laws.json")
    if not os.path.exists(master_path):
        return new_records
    try:
        with open(master_path, encoding="utf-8") as fh:
            data = json.load(fh)
        old = data.get("records") or []
    except Exception as e:
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


__all__ = ["ScrapeConfig", "_is_https_scheme_upgrade", "_longest_text_block", "build_config", "clean_text", "decode_html", "extract_date", "extract_doc_number", "extract_issue_organ", "load_resume", "make_summary", "merge_with_master", "write_outputs"]
