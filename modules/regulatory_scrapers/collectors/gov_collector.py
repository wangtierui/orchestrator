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
import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import requests
    from bs4 import BeautifulSoup
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
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
    from std_lib.scraper_std.cache_store import OfflineMiss, TextResponseCache
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, TextResponseCache

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名

def set_cache_dir(path):
    """设置请求缓存根目录（启用/禁用缓存）。path=None 表示禁用缓存。"""
    global _RESP_TEXT
    _RESP_TEXT = TextResponseCache(path) if path else None

def set_offline(flag):
    if _RESP_TEXT is not None:
        _RESP_TEXT.set_offline(flag)

def _cache_ep(url: str) -> str:
    """缓存键：以「完整 URL 的 sha1」作为**裸端点名**（契约允许 endpoint 为裸端点名）。

    ⚠️ **不可直接把 URL 交给 TextResponseCache**：其命名算法取 URL **末段**作 ep
    （与 nfra 原 `_cache_path` 一致，保证历史缓存可复用）；当 `params` 为空且多个
    URL 末段同名时（典型如 pbc 栏目页/详情页均以 `index.html` 结尾）会撞进同一
    缓存文件 → 读到错误正文。此处改用 URL 级唯一键（sha1 前 16 位），
    `params` 仍参与命名，故同一 URL 的不同参数组合依旧各自独立。
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

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

# 详情页正文候选容器选择器（按优先级尝试）
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

# --------------------------------------------------------------------------- #
# 健壮的 HTTP 客户端
# --------------------------------------------------------------------------- #

class RobustSession:
    """带重试、退避、UA 轮换与随机延时的请求会话。"""

    def __init__(self, cfg: ScrapeConfig):
        self.cfg = cfg
        self.session = requests.Session()
        retry = Retry(
            total=cfg.retries,
            connect=cfg.retries,
            read=cfg.retries,
            status=cfg.retries,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            backoff_factor=1.5,          # 退避：{backoff_factor} * (2 **(n-1))
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self, referer: str | None = None) -> dict[str, str]:
        h = dict(DEFAULT_HEADERS)
        h["User-Agent"] = random.choice(USER_AGENTS)
        if referer:
            h["Referer"] = referer
        return h

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.cfg.delay_min, self.cfg.delay_max))

    def get(self, url: str, referer: str | None = None,
            params: dict[str, Any] | None = None,
            is_json: bool = False) -> Any | None:
        params = params or {}
        # 1) 缓存命中：续跑 / 离线
        if _RESP_TEXT is not None:
            cp = _RESP_TEXT.path(_cache_ep(url), params)
            if os.path.exists(cp):
                try:
                    with open(cp, encoding="utf-8") as fh:
                        cached = fh.read()
                    return json.loads(cached) if is_json else cached
                except Exception:
                    pass  # 缓存损坏则重新请求
            if _RESP_TEXT.offline:
                raise _OfflineMiss("%s ? %s" % (url, urllib.parse.urlencode(params)))
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.get(
                    url, params=params, headers=self._headers(referer),
                    timeout=self.cfg.timeout,
                )
                if resp.status_code == 200:
                    # 文本响应使用鲁棒解码（UTF-8→GBK/GB18030），避免中文乱码；
                    # JSON 响应交由 requests 自行解码。
                    result = resp.json() if is_json else decode_html(resp.content)
                    # 写入缓存（仅成功响应且非空，不缓存错误/拦截页）
                    if _RESP_TEXT is not None and result:
                        try:
                            _RESP_TEXT.put(
                                _cache_ep(url), params,
                                json.dumps(result, ensure_ascii=False) if is_json else result,
                            )
                        except Exception:
                            pass
                    return result
                LOG.warning("GET %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:  # 网络异常 / 超时
                LOG.warning("GET %s 异常：%s (尝试 %d)", url, e, attempt)
            # 指数退避
            time.sleep(min(2 ** attempt, 30))
        LOG.error("GET %s 多次重试失败，放弃", url)
        return None

    def get_text(self, url: str, referer: str | None = None,
                 params: dict[str, Any] | None = None) -> str | None:
        return self.get(url, referer=referer, params=params, is_json=False)

    def post_json(self, url: str, json_body: dict[str, Any],
                  referer: str | None = None) -> Any | None:
        """POST JSON 并解析响应（带重试/退避/UA 轮换）。失败返回 None。"""
        for attempt in range(1, self.cfg.retries + 2):
            try:
                self._sleep()
                resp = self.session.post(
                    url, json=json_body,
                    headers=self._headers(referer), timeout=self.cfg.timeout)
                if resp.status_code == 200:
                    return resp.json()
                LOG.warning("POST %s -> HTTP %s (尝试 %d)",
                            url, resp.status_code, attempt)
            except Exception as e:
                LOG.warning("POST %s 异常：%s (尝试 %d)", url, e, attempt)
            time.sleep(min(2 ** attempt, 30))
        LOG.error("POST %s 多次重试失败，放弃", url)
        return None

# --------------------------------------------------------------------------- #
# 工具函数
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

# 发文机关关键词（用于从『X令第N号』中定位机关名）
_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

# 详情页需剔除的噪声元素（下载条 / 历史沿革 / 按钮等）
_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]

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

# http 站点正文页仅返回「JS 协议升级」脚本（window.location.href="https:"+...），
# requests 不执行 JS → 拿不到正文。检测到该模式后改用 https 重抓。
_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)

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

# --------------------------------------------------------------------------- #
# 数据源 1：行政法规库（xzfgk，服务端渲染，requests 可抓）
# --------------------------------------------------------------------------- #

class XzfgkScraper:
    """司法部行政法规库：search2.html 列表 + /front/law/detail 详情。"""

    LIST_URL = "https://xzfg.moj.gov.cn/search2.html"
    DETAIL_URL = "https://xzfg.moj.gov.cn/front/law/detail"

    def __init__(self, cfg: ScrapeConfig, client: RobustSession):
        self.cfg = cfg
        self.client = client

    def _list_page(self, page_index: int) -> str | None:
        # 默认页即展示全部现行有效行政法规；分页参数 PageIndex 从 1 开始
        return self.client.get_text(
            self.LIST_URL, referer=self.LIST_URL,
            params={"PageIndex": page_index},
        )

    def _parse_list(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        items: list[dict[str, str]] = []
        for li in soup.select("li.list-item"):
            a = li.select_one(".title a")
            if not a:
                continue
            title = clean_text(a.get_text())
            href = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://xzfg.moj.gov.cn" + href
            pub = li.select_one(".publish-date")
            impl = li.select_one(".implemented-date")
            items.append({
                "title": title,
                "detail_url": href,
                "publish_date": extract_date(pub.get_text()) if pub else "",
                "effective_date": extract_date(impl.get_text()) if impl else "",
                "issue_organ": "",
                "document_number": "",
            })
        return items

    def _parse_detail(self, html: str) -> dict[str, str]:
        # gov.cn 内容页为 HTML 4.01，lxml 解析会严重丢结构导致正文为空；
        # 改用 html.parser（内置，无需额外依赖）以正确抽取正文。
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one(".text-title")
        title = clean_text(title_el.get_text()) if title_el else ""

        # 正文：按优先级尝试候选容器（.law-chapter 为行政法规详情正文）
        content_el = None
        for sel in CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break
        if content_el:
            # 剔除下载条 / 历史沿革 / 分享等噪声元素
            for hint in _NOISE_CLASS_HINTS:
                for el in content_el.select(f'[class*="{hint}"]'):
                    el.decompose()
            for el in content_el.select("script, style"):
                el.decompose()
        full_text = clean_text(content_el.get_text()) if content_el else ""

        # 发文字号 / 生效日期 / 发文机关 优先从正文前言抽取
        doc_no = extract_doc_number(full_text)
        # 生效日期：『自……起施行』
        eff = ""
        m = re.search(r"自(.{0,30}?)起施行", full_text)
        if m:
            eff = extract_date(m.group(1))
        if not eff:
            m = re.search(r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?)\s*施行", full_text)
            eff = m.group(1) if m else ""
        organ = extract_issue_organ(full_text)
        # 原始公布日期（前言『YYYY年M月D日……公布』）
        m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日).{0,40}公布", full_text)
        pub_orig = m.group(1) if m else ""
        return {
            "title": title,
            "full_text": full_text,
            "document_number": doc_no,
            "issue_organ": organ,
            "effective_date": eff,
            "pub_date_original": pub_orig,
        }

    def run(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        seen_urls = set()
        if self.cfg.resume:
            resumed, _ = load_resume(self.cfg.out_dir, self.cfg.source)
            seen_urls = set(k[1] for k in resumed if k[1])
            LOG.info("【xzfgk resume】将跳过 %d 条已抓条目", len(seen_urls))
        while True:
            if self.cfg.max_pages and page > self.cfg.max_pages:
                LOG.info("已达 max_pages=%d，停止翻页", self.cfg.max_pages)
                break
            LOG.info("【xzfgk】抓取列表第 %d 页", page)
            html = self._list_page(page)
            if not html:
                LOG.warning("第 %d 页获取失败，停止", page)
                break
            items = self._parse_list(html)
            if not items:
                LOG.info("第 %d 页无条目，视为末页，停止", page)
                break
            new_in_page = 0
            for it in items:
                if it["detail_url"] in seen_urls:
                    continue
                seen_urls.add(it["detail_url"])
                new_in_page += 1
                rec = dict(it)
                rec["category"] = self.cfg.category
                rec["source"] = "xzfgk"
                if (self.cfg.fetch_details and it["detail_url"]
                        and (self.cfg.details_limit == 0
                             or len(records) < self.cfg.details_limit)):
                    d = self._fetch_detail(it["detail_url"])
                    rec.update(d)
                rec["summary"] = make_summary(
                    rec.get("full_text", ""), self.cfg.summary_len)
                records.append(rec)
                if self.cfg.max_items and len(records) >= self.cfg.max_items:
                    LOG.info("已达 max_items=%d，停止", self.cfg.max_items)
                    return records
            if new_in_page == 0:
                # 2026-09-04 修复：xzfgk 列表接口分页失效时每页返回同批条目 →
                # 整页全为已见即终止（曾致无限翻页，60 分钟空跑 61+ 页）
                LOG.info("第 %d 页无新增条目（全部已抓或分页失效），停止翻页", page)
                break
            page += 1
        return records

    def _fetch_detail(self, url: str) -> dict[str, str]:
        detail_html = self.client.get_text(url, referer=self.LIST_URL)
        if not detail_html:
            LOG.warning("详情获取失败：%s", url)
            return {"full_text": "", "document_number": "",
                    "issue_organ": "", "effective_date": "",
                    "attachments": [], "attachment_text": "", "attachment_count": 0}
        try:
            d = self._parse_detail(detail_html)
        except Exception as e:
            LOG.warning("详情解析失败 %s：%s", url, e)
            d = {"full_text": "", "document_number": "",
                 "issue_organ": "", "effective_date": ""}
        # 修复：抓取详情页附件（Word/PDF/Excel），异常隔离不中断主流程
        try:
            atts, att_text = fetch_gov_attachments(
                detail_html, entry_id=url, entry_title=d.get("title", ""),
                out_dir=self.cfg.out_dir, base_url=url)
            d["attachments"] = atts
            d["attachment_text"] = att_text
            d["attachment_count"] = len(atts)
        except Exception as e:
            LOG.warning("附件抓取异常 %s：%s", url, e)
            d.setdefault("attachments", [])
            d["attachment_text"] = ""
            d["attachment_count"] = 0
        return d

# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# CSV 表格列（2026-09-04：随 FlkScraper 段删除时被误删，已从备份恢复）
CSV_COLUMNS = [
    "title", "category", "publish_date", "pub_date_original", "effective_date",
    "issue_organ", "document_number", "detail_url", "summary", "source",
]

def write_outputs(records: list[dict[str, Any]], cfg: ScrapeConfig,
                  source_label: str | None = None) -> dict[str, str]:
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

    # CSV：表格视图（含摘要，不含全文，便于 Excel 打开）
    _tmpc = csv_path + ".tmp"
    with open(_tmpc, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
    os.replace(_tmpc, csv_path)

    LOG.info("输出完成：\n  JSON: %s\n  CSV : %s", json_path, csv_path)
    return {"json": json_path, "csv": csv_path}

# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

SOURCES = {
    # 2026-09-04：仅保留 xzfgk（行政法规库）。flk（国家法律法规数据库）正文存站内
    # OBS 外部不可达、仅元数据无正文，已停止抓取并清理（见 docs/gov正文缺失排查报告）。
    "xzfgk": ("https://www.gov.cn/zhengce/xzfgk/", "行政法规"),
}

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

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
import atexit  # noqa: E402

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock


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

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="抓取 gov.cn 行政法规库（xzfg.moj.gov.cn）行政法规条目（含正文全文）")
    parser.add_argument("--source", choices=["xzfgk"], default="xzfgk",
                        help="数据源：xzfgk=行政法规库(xzfg.moj.gov.cn，唯一数据源)")
    parser.add_argument("--out-dir",
                        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw"),
                        help="输出目录（5b 收敛：默认统一 data/raw/，即 regulatory_scrapers/data/raw）")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="最大翻页数（0=不限制）")
    parser.add_argument("--max-items", type=int, default=0,
                        help="最大条目数（0=不限制，指列表条目）")
    parser.add_argument("--no-details", action="store_true",
                        help="不抓取详情页（仅列表字段，适合全量列表级抓取）")
    parser.add_argument("--details-limit", type=int, default=0,
                        help="仅对前 N 条抓取详情正文（0=全部；调试用采样）")
    parser.add_argument("--resume", action="store_true",
                        help="增量续抓：基于 out_dir 最新同源输出跳过已抓条目，"
                             "避免重复并支持中断后恢复")
    parser.add_argument("--delay-min", type=float, default=1.5,
                        help="请求最小延时（秒）")
    parser.add_argument("--delay-max", type=float, default=3.5,
                        help="请求最大延时（秒）")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时（秒）")
    parser.add_argument("--retries", type=int, default=4, help="重试次数")
    parser.add_argument("--summary-len", type=int, default=200,
                        help="内容摘要字数")
    parser.add_argument("--cache-dir",
                        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "gov"),
                        help="请求缓存目录：抓取时落盘、断网时读盘（支持断点续跑/离线复现）。"
                             "默认 regulatory_scrapers/cache/gov（五源统一缓存根）")
    parser.add_argument("--offline", action="store_true",
                        help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    parser.add_argument("--log-file", default="",
                        help="日志文件路径（默认输出到控制台与 out-dir/scraper.log）")
    args = parser.parse_args(argv)

    # 通用缓存（五源统一抽象层）：启用缓存目录 + 离线开关
    set_cache_dir(args.cache_dir)
    set_offline(args.offline)

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    log_file = args.log_file or os.path.join("logs", "scraper.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    # —— 调度可靠性：跨进程单实例锁防并发重复（统一 fs_lock 公共库，N-8）——
    _lock = fs_lock.ProcessLock(os.path.join(args.out_dir, "scrape.lock"))
    if not _lock.acquire():
        LOG.warning("已有抓取任务在运行（锁存在且 PID 存活），本次跳过以避免重复抓取。")
        return 0
    atexit.register(_lock.release)

    cfg = build_config(args)
    client = RobustSession(cfg)

    LOG.info("开始抓取数据源：%s（%s）", cfg.source, cfg.category)
    try:
        records = XzfgkScraper(cfg, client).run()
    except Exception as e:
        LOG.exception("抓取过程发生致命错误：%s", e)
        return 1

    if not records:
        LOG.warning("未抓取到任何条目。")
        return 0

    if cfg.resume:
        # 增量续抓：本次 records 仅含新发现条目 → 与现主库合并后覆盖写，防丢历史
        records = merge_with_master(records, cfg.out_dir)

    paths = write_outputs(records, cfg, source_label=cfg.source)
    LOG.info("成功抓取 %d 条。文件：%s", len(records), paths)
    return 0

if __name__ == "__main__":
    sys.exit(main())
