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
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
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
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: E402
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


# ---- 拆分（2026-09-13，P3）：下列符号迁 gov_parse，re-export 保持外部调用兼容 ----
from gov_parse import (  # noqa: F401  拆分 re-export（显式；规避 F405）
    SOURCES,  # 子源登记表唯一事实源在 gov_parse（2026-09-15 收敛，勿在别处重复定义）
    ScrapeConfig,
    _is_https_scheme_upgrade,
    _longest_text_block,
    build_config,
    clean_text,
    decode_html,
    extract_date,
    extract_doc_number,
    extract_issue_organ,
    load_resume,
    make_summary,
    merge_with_master,
    write_outputs,
)


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("gov")，单物理根）；path 显式可覆盖。"""
    global _RESP_TEXT
    _RESP_TEXT = bind_source_cache("gov", "text", root=path)
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
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
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
                        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
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




# 发文机关关键词（用于从『X令第N号』中定位机关名）
_ORGAN_KW = r"(?:国务院|部|委员会|局|人民政府|政府|厅|署|中央军委)"

# 详情页需剔除的噪声元素（下载条 / 历史沿革 / 按钮等）
_NOISE_CLASS_HINTS = ["download", "fold", "historical", "tip", "btn", "share", "qr", "vconsole"]





# http 站点正文页仅返回「JS 协议升级」脚本（window.location.href="https:"+...），
# requests 不执行 JS → 拿不到正文。检测到该模式后改用 https 重抓。
_SCHEME_UPGRADE_RE = re.compile(r'targetProtocol\s*=\s*["\']https:', re.I)



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
            # 表格结构化聚合到条目顶层（附件 rec 表字段 → 顶层表键，map_gov 透传 cleaned）
            _tbls = [t for a in atts for t in (a.get("table_structured") or [])]
            if _tbls:
                d["table_structured"] = _tbls
                d["table_recovery_method"] = "structured"
                _raws = [a.get("table_raw_text") for a in atts if a.get("table_raw_text")]
                if _raws:
                    d["table_raw_text"] = "\n\n".join(_raws)
            # 富内容聚合到条目顶层（rich_object 轨，pipeline 透传 cleaned JSONL）
            _richo = [o for a in atts for o in (a.get("rich_structured") or [])]
            if _richo:
                d["rich_structured"] = _richo
                d["rich_count"] = len(_richo)
                _rtext = [a.get("rich_text") for a in atts if a.get("rich_text")]
                if _rtext:
                    d["rich_text"] = "\n".join(_rtext)
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


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

# 子源登记表 SOURCES 由 gov_parse 导入（唯一事实源，见上方 import 块）。


# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
import atexit  # noqa: E402

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from config.exitcodes import ExitCode  # noqa: E402
from std_lib.common_lib import fs_lock


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="抓取 gov.cn 行政法规库（xzfg.moj.gov.cn）行政法规条目（含正文全文）")
    parser.add_argument("--source", choices=["xzfgk", "zhengceku", "all"], default="all",
                        help="数据源：xzfgk=行政法规库；zhengceku=国务院政策文件库·部门文件；"
                             "all=两者依次抓取（默认，gov 源常规采集范围）")
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
                             "避免重复并支持中断后恢复（默认即增量，见 --full）")
    parser.add_argument("--full", action="store_true",
                        help="全量重抓：忽略旧主库、重抓全部列表条目详情（默认增量：跳过已抓条目并合并历史主库）")
    parser.add_argument("--delay-min", type=float, default=1.5,
                        help="请求最小延时（秒）")
    parser.add_argument("--delay-max", type=float, default=3.5,
                        help="请求最大延时（秒）")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时（秒）")
    parser.add_argument("--retries", type=int, default=4, help="重试次数")
    parser.add_argument("--summary-len", type=int, default=200,
                        help="内容摘要字数")
    parser.add_argument("--cache-dir", default="",
                        help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(gov) 统一解析"
                             "→ modules/regulatory_scrapers/cache/gov（单物理根）")
    parser.add_argument("--offline", action="store_true",
                        help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    parser.add_argument("--csv", action="store_true",
                        help="额外输出 CSV 表格视图（默认仅写 JSON 主库，2026-09-09 规范）")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="多子源抓取时任一子源失败即中止（默认跳过失败子源、继续其余并汇总）")
    parser.add_argument("--checkpoint-every", type=int, default=200,
                        help="详情阶段断点落盘间隔（条；0=不落暂存）。zhengceku 全量约 30 小时，"
                             "主库仅在全部结束后写一次，中断必须靠暂存文件续跑（默认 200）")
    parser.add_argument("--log-file", default="",
                        help="日志文件路径（默认输出到控制台与 out-dir/scraper.log）")
    args = parser.parse_args(argv)
    # 增量默认（2026-09-08 周调度增量改造）：resume = 非 --full。显式 --full 才全量重抓。
    args.resume = not args.full

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

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
        return ExitCode.OK
    atexit.register(_lock.release)

    # —— 多子源分发（2026-09-15 纳入 zhengceku）——
    # 原实现硬编码 XzfgkScraper；现按 cfg.source 分发，支持 --source all 依次抓取
    # 两个子源并**合并进同一 gov 主库** gov_laws.json（按 detail_url 去重，新优先）。
    from gov_zhengceku import ZhengcekuScraper  # noqa: E402  （同目录子采集器）

    if args.source == "all":
        want = list(SOURCES)
    else:
        want = [args.source]

    # P0 修复（2026-09-22 周调度发现）：`SOURCES` 中并无 "all" 键，原先在子源分发前
    # 直接 `build_config(args)` 会 KeyError('all') → `--source all`（**即默认值**）与
    # 无参调用必然崩溃（2026-09-15 纳入 zhengceku 时引入的回归）。
    # 现按**首个子源**构建公共配置：RobustSession 的超时/重试/延时以及 resume/out_dir
    # 均与子源无关；子源专属 cfg 仍在下方循环内按 `scfg = build_config(sub)` 重建。
    _cfg_args = argparse.Namespace(**vars(args))
    _cfg_args.source = want[0]
    cfg = build_config(_cfg_args)
    client = RobustSession(cfg)

    # resume：只跳过「已有正文」的 detail_url（无正文的历史记录仍需重抓补全）
    seen_urls = set()
    if cfg.resume:
        _seen, detailed = load_resume(cfg.out_dir, "gov")
        seen_urls = set(detailed)

    records, failed = [], []
    for s in want:
        sub = argparse.Namespace(**vars(args))
        sub.source = s
        scfg = build_config(sub)
        LOG.info("开始抓取子源：%s（%s）", scfg.source, scfg.category)
        try:
            if s == "xzfgk":
                recs = XzfgkScraper(scfg, client).run()
            else:
                recs = ZhengcekuScraper(scfg, client, seen_urls).run()
        except Exception as e:  # noqa: BLE001
            LOG.exception("子源 %s 抓取过程发生致命错误：%s", s, e)
            failed.append(s)
            continue
        LOG.info("子源 %s 抓取到 %d 条", s, len(recs))
        records.extend(recs)
        for r in recs:
            if r.get("full_text"):
                seen_urls.add(r.get("detail_url", ""))

    if failed and args.stop_on_error:
        return ExitCode.FAIL
    if not records:
        LOG.warning("未抓取到任何条目（失败子源：%s）。", failed or "无")
        return ExitCode.OK

    if cfg.resume:
        # 增量续抓：本次 records 仅含新发现条目 → 与现主库合并后覆盖写，防丢历史
        records = merge_with_master(records, cfg.out_dir)

    # 信封 source/category：单子源沿用其自身标识；all 时标为 gov 汇总
    env_source = cfg.source if len(want) == 1 else "gov"
    env_cat = cfg.category if len(want) == 1 else "行政法规+部门文件"
    cfg.source, cfg.category = env_source, env_cat
    paths = write_outputs(records, cfg, source_label=env_source, write_csv=args.csv)
    LOG.info("成功抓取 %d 条（子源：%s；失败：%s）。文件：%s",
             len(records), ",".join(want), ",".join(failed) or "无", paths)
    return ExitCode.OK

if __name__ == "__main__":
    sys.exit(main())
