# -*- coding: utf-8 -*-
"""pbc 中国人民银行爬虫：网络层 + 站点常量（重构版）。

原 modules/regulatory_scrapers/collectors/pbc_collector.py 的网络/常量部分迁出，逻辑等价；
仅调整 import 路径（相对导入 + 仓库引导）。HTTP 响应磁盘缓存已于重构中移除
（透明重放不再需要），仅保留反爬风控检测 _is_cacheable 与实时请求。
"""
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..lib.paths import docs_root
from ..lib.rich_object import rich_object_fields
from ..lib.table_recovery import structured_table_fields
from ..lib.doc_convert import find_libreoffice
from ..lib.http import USER_AGENTS, robust_get

LO_PATH = find_libreoffice()
LO_AVAILABLE = LO_PATH is not None

ATTACHMENTS_DIR = docs_root("pbc", "attachments")

BASE = "https://www.pbc.gov.cn"

# ----------------------------------------------------------------------------------
# 栏目配置：仅需提供各栏目的列表首页 URL，分页参数运行时自动探测
# ----------------------------------------------------------------------------------
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

# 风控检测：判断一页是否"像真实页"而非反爬拦截页（访问频率/验证码等）。
# 仅用于抓取时的风控识别与退避重试，与响应缓存无关。
_BLOCKED_HINTS = (
    "访问频率", "访问过于频繁", "验证码", "请输入验证码",
    "403 Forbidden", "Access Denied", "请求过于频繁", "captcha",
)
def _is_cacheable(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    if any(h.lower() in low for h in _BLOCKED_HINTS):
        return False
    # 列表页或详情页应含关键结构标记
    return bool(
        RE_PAGING.search(text) or RE_ENTRY.search(text)
        or RE_ARTICLE_TITLE_META.search(text) or RE_ZOOM.search(text)
        or RE_TITLE_H2.search(text)
    )

ATTACH_EXT = (".doc", ".docx", ".pdf", ".xls", ".xlsx", ".wps", ".ceb", ".rtf")


# ----------------------------------------------------------------------------------
# 网络请求层：带重试退避、UA 轮换、超时、频率控制（纯实时，无磁盘缓存）
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
        """带指数退避重试的 GET 请求，返回 (status, content)。

        统一走 lib/http（requests 底座），自动接入容错编排层（熔断/死信/指标）。
        status 为 HTTP 状态码；失败时 status=None，content 为错误说明字符串。
        """
        from .parse import safe_url  # 延迟导入，避免与 parse.py 的循环引用

        url = safe_url(url)
        return robust_get(
            url, binary=binary, timeout=timeout or self.timeout,
            retries=self.retries, min_delay=self.min_delay,
            max_delay=self.max_delay, referer=referer,
        )
