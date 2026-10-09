# -*- coding: utf-8 -*-
"""pbc 中国人民银行爬虫：网络层 + 站点常量 + 共享缓存状态。

原 modules/regulatory_scrapers/collectors/pbc_collector.py 的网络/常量部分迁出，
逻辑逐函数等价；仅调整 import 路径（改为相对导入 + 仓库引导）。
"""
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 仓库引导：使 std_lib 可导入（orchestrator 根）----
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
del _ROOT

try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
    from std_lib.scraper_std.rich_object import rich_object_fields
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    def structured_table_fields(data, name="", *, kind=None):  # type: ignore[misc]  # pragma: no cover
        return {}

    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):  # type: ignore[misc]  # pragma: no cover
        return {}

_RESP_TEXT = None  # TextResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名

# N-167：`_cache_ep` 的唯一实现在 std_lib/scraper_std/cache_store.py；别名导入保持调用点不变。
from std_lib.scraper_std.cache_store import docs_root
from std_lib.scraper_std.cache_store import url_endpoint_key as _cache_ep
from std_lib.scraper_std.doc_convert import find_libreoffice

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
# 缓存绑定：统一根（缺省 cache_store.source_cache_root("pbc")）
# ----------------------------------------------------------------------------------
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
        from .parse import safe_url  # 延迟导入，避免与 parse.py 的循环引用

        last_err = None
        url = safe_url(url)
        # 1) 缓存命中（仅文本页）：续跑 / 离线
        if not binary and _RESP_TEXT is not None:
            cp = _RESP_TEXT.path(_cache_ep(url), {})
            if os.path.exists(cp):
                try:
                    with open(cp, encoding="utf-8") as fh:
                        return 200, fh.read()
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
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
                        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
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
            except (urllib.error.URLError, ConnectionError, TimeoutError, Exception) as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            # 通用退避：2^(attempt) 秒
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 8))
        return None, last_err
