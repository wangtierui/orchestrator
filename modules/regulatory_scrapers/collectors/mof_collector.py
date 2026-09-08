# -*- coding: utf-8 -*-
"""
财政部法规数据库（fgk.mof.gov.cn）自动抓取脚本
================================================

目标站点：http://fgk.mof.gov.cn/ui/start/#/outerHomePage/categoryGuide/lfgccId=...
该站点为 layui 单页应用（SPA），列表与详情数据均由后端 REST 接口返回。
本脚本直接调用其数据接口，避免渲染 SPA，稳定且高效。

接口（经源码反编译确认）：
  列表：POST {HOST}/dev/lawFile/list
         body = {"queryMap":{"lfgcc":<类别ID>,"linvalid":"0"},"size":<每页条数>,"current":<页码>}
         resp = {"code":200,"data":{"records":[...],"total":N,"pages":P,"current":C,"size":S}}
  详情：GET  {HOST}/dev/lawFile/get/{id}
  前端详情页（可点击）：{HOST}/ui/src/views/law_html/{id}.html
  附件（前端 JS 异步加载，不在 lcontent 内）：GET {HOST}/file/f/get?infoId={id}&fileType=0(相关附件)/90(下载文字版)
        返回 data 列表，每项含 fileName/fileUrl/serverIp/extension；文件下载地址 = serverIp + fileUrl。

条目关键字段：
  id            法规ID（详情页/接口主键）
  title         法规名称
  lbbdw         颁布单位（发文机关）
  lwh           发文字号
  lBuDate       公布日期
  lSsDate       施行日期（生效日期）
  lcaption      题注/内容摘要
  lcontent      正文全文（HTML）
  lfgcc         法规层次/类别ID
  ltflbName     条法类别名称
  lstate        状态（4=有效等）

依赖：仅 Python 标准库（urllib / json / csv / re / html / argparse / logging）。

使用：
  # 增量 diff 更新（默认）：载入历史存储，仅对新增/变更条目抓详情，
  # 消失条目标记 removed 而非删除；mof_laws.json 本身即持久化存储
  python mof_collector.py

  # 只抓列表前若干条做验证（不抓详情，快速）
  python mof_collector.py --max-items 10 --no-detail

  # 自定义类别与输出目录
  python mof_collector.py --categories 1000000000000300000 1000000000000400000 \
                            --outdir ./data/raw --delay-min 0.4 --delay-max 0.9
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
import html
import io
import json
import logging
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

# 共享加固工具层（UA 池、文档文本抽取、魔数纠正命名等）
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS as CC_USER_AGENTS
    from std_lib.scraper_std.crawler_common import extract_document_text
except ImportError:  # pragma: no cover
    CC_USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]
    def extract_document_text(data, name="", **kw):
        return {"text": "", "kind": "unknown", "extracted": False,
                "extract_status": "library_missing", "sha256": "",
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data)}

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# mof 接口均返回 JSON → 委托 ResponseCache；命中读盘跳过网络、离线缺失抛 OfflineMiss、
# 仅成功响应（code==200）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("mof")，单物理根）；path 显式可覆盖。"""
    global _RESP
    _RESP = bind_source_cache("mof", "json", root=path)
    if offline and _RESP is not None:
        _RESP.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP is not None:
        _RESP.set_offline(flag)

HOST = "http://fgk.mof.gov.cn"
BASE = "/dev"  # 接口基础路径：/dev/lawFile/list 等

# 产物目录统一（Plan B 阶段 2b + 5b 收敛）：文件系统根与附件根（避免与 API BASE 冲突）
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
# 5b（2026-09-04）：附件写入点收敛至统一 data/docs/（此前指向 per-source data/docs 会与实物分裂）
ATTACHMENTS_DIR = os.path.join(REPO_ROOT, "data", "docs", "mof_regulations_scraper", "attachments")

# 两个目标 URL 对应的类别（lfgccId -> 展示名称）
DEFAULT_CATEGORIES = {
    "1000000000000300000": "财政法律法规（财政部规章）",
    "1000000000000400000": "财政部规范性文件",
}

# 浏览器化请求头，降低被反爬拦截概率
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": HOST,
    "Referer": HOST + "/ui/start/",
}

logger = logging.getLogger("mof_scraper")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# --------------------------------------------------------------------------- #
# 网络层（带重试 / 退避 / 限速）
# --------------------------------------------------------------------------- #
class RateLimiter:
    """请求频率限制器，支持限流触发后的自适应降温（penalize）。

    - 基础间隔 min_delay~max_delay 之间随机，避免固定节奏被识别为脚本。
    - 一旦命中 429/503 等限流，penalize(extra) 追加冷却时长，后续请求整体减速，
      给服务器端限流窗口留出恢复时间；每次 wait 后冷却按半衰减，避免长期过慢。
    """

    def __init__(self, min_delay=0.3, max_delay=0.7, max_cooldown=180.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_cooldown = max_cooldown
        self.cooldown = 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            target = random.uniform(self.min_delay, self.max_delay) + self.cooldown
            elapsed = now - self._last
            if elapsed < target:
                time.sleep(target - elapsed)
            self._last = time.time()
            # 降温随每次请求指数衰减一半，避免长期过慢
            self.cooldown = max(0.0, self.cooldown * 0.5)

    def penalize(self, extra):
        """触发限流后追加冷却时长（秒），上限 max_cooldown。"""
        if extra <= 0:
            return
        with self._lock:
            self.cooldown = min(self.max_cooldown, self.cooldown + extra)

def _mof_cache_key(method, path, payload):
    """为 _request 生成确定性缓存键 (endpoint, params)。

    - POST：endpoint = HOST+path，params = payload（嵌套 dict/list 值用 JSON 序列化，
      保证 urlencode 安全且确定性；mof 列表 payload 含 queryMap 嵌套，必须此处理）。
    - GET：若 path 含查询串则拆出 query 作为 params；否则 params={}。
    键交给 ResponseCache.path()（与 nfra 命名算法逐字一致，仅扩展名 .json）。
    """
    full_url = HOST + path
    if method == "GET" and "?" in path:
        ep, q = path.split("?", 1)
        params = dict(urllib.parse.parse_qsl(q))
        endpoint = HOST + ep
    elif method == "POST":
        endpoint = full_url
        params = dict(payload or {})
    else:
        endpoint = full_url
        params = {}
    # 嵌套值（dict/list）JSON 序列化，标量保持原样（urlencode 安全 + 确定性）
    flat = {}
    for k, v in params.items():
        flat[k] = json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return endpoint, flat

def _request(method, path, payload=None, *, rate=None, timeout=30,
             max_retries=4):
    """发起一次 HTTP 请求，返回解析后的 JSON dict。含重试与指数退避。

    2026-09-05：委托通用缓存层 std_lib/scraper_std.cache_store.ResponseCache——
    命中读盘跳过网络；offline 下缺失抛 OfflineMiss；仅成功响应（code==200）落盘。
    """
    endpoint, params = _mof_cache_key(method, path, payload)

    # 1) 缓存命中：续跑 / 离线
    if _RESP is not None:
        cp = _RESP.path(endpoint, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass  # 缓存损坏则重新请求
        if _RESP.offline:
            raise _OfflineMiss("%s" % endpoint)

    # 2) 实时请求
    url = HOST + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = dict(HEADERS)
    if method == "POST":
        headers["Content-Type"] = "application/json;charset=UTF-8"
    # 反爬加固：每次请求轮换 UA（避免单一 UA 被风控聚类）
    headers["User-Agent"] = random.choice(CC_USER_AGENTS)

    if rate is not None:
        rate.wait()

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=body, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            # 写入缓存（仅成功响应：mof 三接口均以 code==200 标识成功）
            if _RESP is not None and isinstance(data, dict) and data.get("code") == 200:
                try:
                    _RESP.put(endpoint, params, data)
                except Exception:
                    pass
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            # 429/503 等限流类错误：长退避 + 全站降温
            if e.code in (429, 503, 502, 500):
                backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning(
                    "HTTP %s @ %s (尝试 %d/%d)，%s 秒后重试",
                    e.code, path, attempt, max_retries, round(backoff, 1),
                )
                time.sleep(backoff)
                continue
            # 其它 HTTP 错误直接抛出（如 404 数据不存在）
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            backoff = min(2 ** attempt, 15) + random.uniform(0, 1)
            logger.warning(
                "网络异常 @ %s (尝试 %d/%d)：%s，%s 秒后重试",
                path, attempt, max_retries, e, round(backoff, 1),
            )
            time.sleep(backoff)
            continue
    raise RuntimeError(f"请求失败（已达最大重试）：{path} -> {last_err}")

# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")

def html_to_text(s):
    """将正文 HTML 转为纯文本。"""
    if not s or not isinstance(s, str):
        return ""
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()

def normalize_date(v):
    """规范化日期字段（兼容 '2024-11-03 00:00:00' / [2024,11,3,0,0] / 'None'）。"""
    if v is None:
        return None
    if isinstance(v, list) and len(v) >= 3:
        try:
            return f"{int(v[0]):04d}-{int(v[1]):02d}-{int(v[2]):02d}"
        except Exception:
            return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "None", "null", "[]"):
            return None
        # 截取日期部分（去时间）
        m = re.match(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", v)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return v.split(" ")[0] if " " in v else v
    return str(v)

# --------------------------------------------------------------------------- #
# 抓取逻辑
# --------------------------------------------------------------------------- #
def fetch_list_page(lfgcc, page, size, rate):
    """抓取某一页列表，返回 (records, total, pages)。"""
    payload = {
        "queryMap": {"lfgcc": lfgcc, "linvalid": "0"},
        "size": size,
        "current": page,
    }
    resp = _request("POST", f"{BASE}/lawFile/list", payload, rate=rate)
    if resp.get("code") != 200 or "data" not in resp:
        raise RuntimeError(f"列表接口返回异常：{resp.get('message')} | {str(resp)[:200]}")
    d = resp["data"]
    # 接口返回的分页字段可能为字符串，统一转 int
    def _to_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    return d.get("records", []), _to_int(d.get("total")), _to_int(d.get("pages"))

def fetch_detail(law_id, rate):
    """抓取单条详情，返回 dict（失败返回 None）。"""
    try:
        resp = _request("GET", f"{BASE}/lawFile/get/{law_id}", rate=rate)
        if resp.get("code") == 200 and resp.get("data"):
            return resp["data"]
    except Exception as e:  # 单条失败不影响整体
        logger.warning("详情获取失败 id=%s：%s", law_id, e)
    return None

# --------------------------------------------------------------------------- #
# 附件抓取（前端 JS 异步加载的"相关附件"与"下载文字版"，不在 lcontent 中）
# --------------------------------------------------------------------------- #
ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)
_ATTACH_KIND = {"0": "attachment", "90": "text_version"}

def safe_filename(name, ext, fallback):
    """生成安全的本地文件名（去除非法字符、补扩展名、限长）。

    （共享库对齐审计 阶段 1）核心逻辑（去非法字符 / 限长）委托
    ``crawler_common.safe_filename``；本函数保留 mof 原有语义：
    **无 URL 参与哈希**、第三参 ``fallback`` 兜底、末补扩展名（共享库版不补扩展名）。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from std_lib.scraper_std.crawler_common import safe_filename as _cc_safe_filename
    name = _cc_safe_filename(name or fallback, "", 120)
    if ext and not name.lower().endswith("." + ext.lower()):
        name = f"{name}.{ext}"
    return name

def fetch_attachments(law_id, rate, file_types=("0", "90"), timeout=20):
    """获取某条法规的附件清单（JSON 元数据）。返回 list[dict]。

    接口：GET /file/f/get?infoId=<id>&fileType=0|90
    返回 data 为列表，每项含 id/fileName/fileUrl/serverIp/extension/fileType。
    """
    items = []
    for ft in file_types:
        try:
            url = f"{ATTACH_PATH}?infoId={urllib.parse.quote(str(law_id))}&fileType={ft}"
            resp = _request("GET", url, rate=rate, timeout=timeout)
            if resp.get("code") != 200 or not resp.get("data"):
                continue
            for it in resp["data"]:
                if not isinstance(it, dict):
                    continue
                server = (it.get("serverIp") or "").strip()
                furl = (it.get("fileUrl") or "").strip()
                if not server or not furl:
                    continue
                if not server.lower().startswith("http"):
                    server = "http://" + server
                full = server.rstrip("/") + (furl if furl.startswith("/") else "/" + furl)
                items.append({
                    "file_id": it.get("id"),
                    "file_name": it.get("fileName") or f"{law_id}_{ft}",
                    "file_type": ft,
                    "kind": _ATTACH_KIND.get(str(ft), "other"),
                    "extension": (it.get("extension") or "").lower(),
                    "file_url": full,
                })
        except Exception as e:
            logger.warning("附件清单获取失败 id=%s fileType=%s：%s", law_id, ft, e)
    return items

def download_attachment(att, law_id, outdir, rate, timeout=60, max_retries=6):
    """下载单个附件到 data/docs/mof_regulations_scraper/attachments/<law_id>/，返回带本地相对路径与大小的元数据；失败返回 None。

    针对附件服务器限流优化（vB）：
      - 默认重试 6 次（原 3 次），给限流更多恢复窗口；
      - 429/503/502/500 走指数退避（上限 60s）并触发全站降温 penalize；
      - 其它网络异常（超时/连接重置/解析失败）走指数退避（上限 30s）；
      - 404 等不可恢复错误直接放弃，不浪费重试；
      - 每次重试前重新 rate.wait()，避免连续冲击附件服务器。
    """
    attach_dir = os.path.join(ATTACHMENTS_DIR, str(law_id))
    os.makedirs(attach_dir, exist_ok=True)
    fname = safe_filename(att.get("file_name"), att.get("extension"),
                          f"{law_id}_{att.get('file_type')}")
    dest = os.path.join(attach_dir, fname)
    last_err = None
    for attempt in range(1, max_retries + 1):
        if rate is not None:
            rate.wait()
        try:
            req = urllib.request.Request(att["file_url"], headers=HEADERS)
            # 反爬加固：每次下载轮换 UA
            req.add_header("User-Agent", random.choice(CC_USER_AGENTS))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            att["local_path"] = os.path.relpath(dest, REPO_ROOT).replace("\\", "/")
            att["size_bytes"] = len(data)
            # —— 附件文本抽取（修复：此前仅落盘二进制，未抽内部文本）——
            # 透明标记：依赖缺失/扫描件/损坏均返回结构化状态，源文件已留存不丢弃。
            try:
                ext = extract_document_text(data, fname)
                att["text"] = ext.get("text", "")
                att["extracted"] = ext.get("extracted", False)
                att["extract_status"] = ext.get("extract_status", "unsupported")
                att["attachment_kind"] = ext.get("kind", "unknown")
                att["sha256"] = ext.get("sha256", "")
                att["needs_ocr"] = ext.get("needs_ocr", False)
                att["garble_ratio"] = ext.get("garble_ratio", 0.0)
            except Exception as e:
                logger.warning("附件文本抽取异常 %s：%s", att.get("file_url"), e)
                att["extracted"] = False
                att["extract_status"] = "extract_error"
            return att
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 502, 500):
                backoff = min(2 ** attempt * 2.0, 60) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                logger.warning(
                    "附件下载 HTTP %s @ %s (尝试 %d/%d)，%ss 后重试",
                    e.code, att.get("file_url"), attempt, max_retries,
                    round(backoff, 1),
                )
                time.sleep(backoff)
                continue
            # 404 等不可恢复错误：直接放弃该附件
            logger.warning("附件下载不可恢复 HTTP %s @ %s：%s",
                           e.code, att.get("file_url"), e)
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            backoff = min(2 ** attempt * 1.5, 30) + random.uniform(0, 1)
            logger.warning(
                "附件下载网络异常 (尝试 %d/%d)：%s，%ss 后重试",
                attempt, max_retries, e, round(backoff, 1),
            )
            time.sleep(backoff)
            continue
    logger.warning("附件下载失败 %s：%s", att.get("file_url"), last_err)
    return None

def collect_attachments(law_id, rate, timeout=60):
    """抓取并下载某条法规的全部附件，返回带本地路径的元数据列表。

    timeout 透传至 download_attachment，便于调用方在慢速/限流场景下收紧超时。
    """
    meta = fetch_attachments(law_id, rate, timeout=timeout)
    if not meta:
        return []
    result = []
    for att in meta:
        d = download_attachment(att, law_id, ATTACHMENTS_DIR, rate, timeout=timeout)
        if d:
            result.append(d)
    return result

def build_entry(rec, detail, category_id, category_name, fetch_detail_enabled, attachments=None):
    """将列表记录 + 详情合并为结构化条目。"""
    # 正文优先用详情接口（列表里通常也已包含），兜底用列表字段
    content_html = ""
    if fetch_detail_enabled and detail:
        content_html = detail.get("lcontent") or ""
    if not content_html:
        content_html = rec.get("lcontent") or ""
    content_text = html_to_text(content_html)

    # 摘要：题注优先，否则取正文前若干字符
    summary = (detail or rec).get("lcaption") if fetch_detail_enabled else rec.get("lcaption")
    if not summary:
        summary = content_text[:200]

    law_id = rec.get("id") or (detail or {}).get("id")

    return {
        "id": law_id,
        "title": rec.get("title"),
        "category_id": category_id,
        "category_name": category_name or rec.get("ltflbName"),
        "publish_date": normalize_date((detail or rec).get("lBuDate") if fetch_detail_enabled else rec.get("lBuDate")),
        "effective_date": normalize_date((detail or rec).get("lSsDate") if fetch_detail_enabled else rec.get("lSsDate")),
        "issue_org": (detail or rec).get("lbbdw") if fetch_detail_enabled else rec.get("lbbdw"),
        "doc_no": (detail or rec).get("lwh") if fetch_detail_enabled else rec.get("lwh"),
        "summary": summary,
        "content_text": content_text,
        "content_length": len(content_text),
        "status": (detail or rec).get("lstate") if fetch_detail_enabled else rec.get("lstate"),
        "create_dept": rec.get("createDeptName"),
        "file_count": rec.get("fileCount"),
        "expire_date": normalize_date((detail or rec).get("expireDate") if fetch_detail_enabled else rec.get("expireDate")),
        "abolish_date": normalize_date((detail or rec).get("abolishDate") if fetch_detail_enabled else rec.get("abolishDate")),
        "detail_link": f"{HOST}/ui/src/views/law_html/{law_id}.html" if law_id else None,
        "api_link": f"{BASE}/lawFile/get/{law_id}" if law_id else None,
        "source": "fgk.mof.gov.cn",
        "fetch_time": datetime.now(UTC).isoformat(),
        "attachments": attachments or [],
        "attachment_count": len(attachments or []),
        "attachment_names": "; ".join(
            (a.get("file_name") or "") for a in (attachments or [])
        ),
    }

def fetch_category_records(lfgcc, category_name, *, size=50, rate, max_items=None):
    """分页拉取某类别的列表原始记录（含 lcontent），不抓详情、不做条目级构建。

    返回 (raw_records, expected_total)。增量 diff 与详情抓取在 main 中按 id 进行。
    """
    logger.info("拉取类别 [%s] %s 列表", lfgcc, category_name)
    records = []
    expected_total = 0
    page = 1
    seen_ids = set()
    while True:
        recs, total, pages = fetch_list_page(lfgcc, page, size, rate)
        if page == 1:
            expected_total = total  # 首页总量作为"预期条数"，用于漏抓自检
        logger.info("  第 %d/%d 页，本页 %d 条（全量共 %d 条）",
                    page, max(pages, 1), len(recs), total)
        for rec in recs:
            rid = rec.get("id")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            records.append(rec)
            if max_items and len(records) >= max_items:
                logger.info("  已达到 --max-items 上限 %d，停止。", max_items)
                return records, expected_total
        if page >= max(pages, 1) or not recs:
            break
        page += 1
    logger.info("类别 [%s] 列表拉取完成，共 %d 条。", lfgcc, len(records))
    return records, expected_total

def _sig_current(rec):
    """当前列表记录的变更指纹（用于增量 diff：标题/正文/日期/文号/机关/题注）。"""
    return (
        rec.get("title"),
        html_to_text(rec.get("lcontent") or ""),
        normalize_date(rec.get("lBuDate")),
        normalize_date(rec.get("lSsDate")),
        rec.get("lwh"),
        rec.get("lbbdw"),
        rec.get("lcaption"),
    )

def _sig_prev(entry):
    """历史存储条目的变更指纹（与 _sig_current 口径对齐）。"""
    return (
        entry.get("title"),
        entry.get("content_text"),
        entry.get("publish_date"),
        entry.get("effective_date"),
        entry.get("doc_no"),
        entry.get("issue_org"),
        entry.get("summary"),
    )

# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 可靠性增强：原子写、运行锁、状态记录
# --------------------------------------------------------------------------- #
def _atomic_write(path, text):
    """先写临时文件再原子替换，避免写入中途崩溃损坏已有结果文件。

    2026-09-04 修复：目标目录不存在时自动创建。此前 data/reports、data/state
    目录缺失会导致首跑在写报告/状态阶段抛 FileNotFoundError（主库已写入但
    状态未落盘，周报失真）。
    """
    _d = os.path.dirname(path)
    if _d:
        os.makedirs(_d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

# —— 运行锁统一实现（N-8）：判定逻辑收敛到 regulatory_scrapers/fs_lock.py，四源共用 ——
_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)
from std_lib.common_lib import fs_lock

_LOCKS = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）

def _read_lock_pid(lock_path):
    """读取锁文件首段（PID）。格式：PID|ISO|token。失败返回 None。"""
    try:
        with open(lock_path, encoding="utf-8") as f:
            first = f.readline().strip()
    except OSError:
        return None
    try:
        return int(first.split("|")[0])
    except (ValueError, IndexError):
        return None

def _pid_alive(pid):
    """跨平台进程存活检测（Windows/Linux 均可用 os.kill(pid, 0)）。"""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False

def acquire_lock(lock_path, max_age_sec=3 * 3600):
    """基于 PID 存活校验的运行锁（实现统一委托 fs_lock.ProcessLock，N-8）。

    - 判定逻辑（PID 存活 + max_age 陈旧兜底）仅在 fs_lock 公共库一份，四源共用；
    - 锁存在且其中 PID 仍在运行 -> 返回 False（并发重复，调用方跳过本次）；
    - PID 已死 / 锁损坏 / 超龄 -> 覆盖抢占。

    注：保留本函数签名仅为兼容既有调用方与测试；新代码请直接用 fs_lock.ProcessLock。
    """
    lk = fs_lock.ProcessLock(lock_path, max_age_sec=max_age_sec)
    if lk.acquire():
        _LOCKS[lock_path] = lk
        return True
    return False

def release_lock(lock_path):
    """释放由 acquire_lock 获取的锁（幂等；未持有则无操作）。"""
    lk = _LOCKS.pop(lock_path, None)
    if lk is not None:
        lk.release()

def write_status(outdir, status):
    """记录抓取状态（成功 / 异常），供调度与监控读取，保留异常状态处理能力。"""
    _atomic_write(os.path.join(os.path.dirname(outdir), "state", "status.json"),
                  json.dumps(status, ensure_ascii=False, indent=2))

def save_json(entries, path):
    _atomic_write(path, json.dumps({
        "source": HOST,
        "captured_at": datetime.now(UTC).isoformat(),
        "count": len(entries),
        "items": entries,
    }, ensure_ascii=False, indent=2))

def save_csv(entries, path):
    cols = [
        "id", "title", "category_name", "category_id", "publish_date",
        "effective_date", "issue_org", "doc_no", "summary", "content_text",
        "content_length", "status", "create_dept", "file_count",
        "attachment_count", "attachment_names", "attachments",
        "active", "change", "removed_at",
        "detail_link", "api_link", "source", "fetch_time",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writeheader()
    for e in entries:
        w.writerow({c: e.get(c, "") for c in cols})
    # 前置 BOM，保证 Excel 正确识别 UTF-8
    _atomic_write(path, "\ufeff" + buf.getvalue())

def save_html_report(entries, path):
    """生成可读的离线 HTML 报告（自包含，无外部依赖）。"""
    items_html = []
    for i, e in enumerate(entries, 1):
        att_cell = ""
        for a in (e.get("attachments") or []):
            lp = a.get("local_path")
            fn = html.escape(str(a.get("file_name", "")))
            att_cell += (f'<a href="{html.escape(lp)}" target="_blank">{fn}</a><br>'
                         if lp else fn + "<br>")
        if not att_cell:
            att_cell = "—"
        items_html.append(f"""
        <tr>
          <td>{i}</td>
          <td><a href="{e.get('detail_link','')}" target="_blank">{html.escape(str(e.get('title','')))}</a></td>
          <td>{html.escape(str(e.get('category_name','')))}</td>
          <td>{html.escape(str(e.get('publish_date','')))}</td>
          <td>{html.escape(str(e.get('effective_date','')))}</td>
          <td>{html.escape(str(e.get('issue_org','')))}</td>
          <td>{html.escape(str(e.get('doc_no','')))}</td>
          <td>{html.escape(str(e.get('summary',''))[:160])}</td>
          <td>{html.escape(str(e.get('content_length','')))}</td>
          <td>{att_cell}</td>
          <td>{'有效' if e.get('active') else '已移除'}</td>
          <td>{html.escape(str(e.get('change','')))}</td>
        </tr>""")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>财政部法规数据库抓取结果</title>
<style>
 body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px;color:#222}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f5f7fa}} tr:nth-child(even){{background:#fafafa}}
 a{{color:#1E6FD9;text-decoration:none}}
 .meta{{color:#888;font-size:12px;margin-bottom:12px}}
</style></head><body>
<h1>财政部法规数据库抓取结果</h1>
<div class="meta">来源：{HOST} ｜ 条目数：{len(entries)} ｜ 生成时间：{datetime.now().isoformat(timespec='seconds')}</div>
<table>
<thead><tr><th>#</th><th>标题</th><th>类别</th><th>公布日期</th><th>生效日期</th>
<th>发文机关</th><th>发文字号</th><th>内容摘要</th><th>正文字数</th><th>附件</th><th>状态</th><th>变更</th></tr></thead>
<tbody>{''.join(items_html)}</tbody>
</table></body></html>"""
    _atomic_write(path, doc)

# --------------------------------------------------------------------------- #
# 主程序
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="财政部法规数据库自动抓取脚本（增量 diff 更新）")
    ap.add_argument("--categories", nargs="*", default=list(DEFAULT_CATEGORIES.keys()),
                    help="要抓取的类别 lfgccId 列表（默认两个目标 URL 对应的类别）")
    ap.add_argument("--names", nargs="*", default=None,
                    help="与 --categories 对应的展示名称（可选）")
    ap.add_argument("--outdir", default=os.path.join(REPO_ROOT, "data", "raw"),
                    help="输出目录（默认统一 data/raw/：regulatory_scrapers/data/raw）")
    ap.add_argument("--size", type=int, default=50, help="列表每页条数（默认50）")
    ap.add_argument("--delay-min", type=float, default=0.3, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=0.7, help="请求最大间隔(秒)")
    ap.add_argument("--no-detail", action="store_true", help="仅抓列表，不逐条请求详情页（同时跳过附件下载）")
    ap.add_argument("--no-attachments", action="store_true",
                    help="跳过附件下载（仅主数据全量）。附件主机 10.1.60.36:8888 实测 100%% HTTP 502 "
                         "（195 URL/成功 0），默认重试 6 次退避约 180s/附件，会让全量任务空转数十小时。"
                         "默认 False＝保持原行为（抓取附件）。")
    ap.add_argument("--refetch-attachments", action="store_true",
                    help="强制重新抓取并下载全部条目的附件（用于补抓历史条目附件，默认仅新增/变更条目抓附件）")
    ap.add_argument("--max-items", type=int, default=None, help="每类别最多抓取条目数（验证用）")
    ap.add_argument("--timeout", type=int, default=30, help="单请求超时(秒)")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）：缺省由 cache_store.source_cache_root(mof) 统一解析"
                         "→ modules/regulatory_scrapers/cache/mof（单物理根）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()

    # 通用缓存（五源统一抽象层）：统一根绑定 + 离线开关
    _init_cache(args.cache_dir or None, args.offline)

    # 名称映射
    if args.names and len(args.names) == len(args.categories):
        cat_map = dict(zip(args.categories, args.names, strict=False))
    else:
        cat_map = {k: DEFAULT_CATEGORIES.get(k, k) for k in args.categories}

    os.makedirs(args.outdir, exist_ok=True)

    # —— 调度可靠性：运行锁防并发重复 ——
    lock_path = os.path.join(args.outdir, "scrape.lock")
    if not acquire_lock(lock_path):
        logger.error("已有抓取任务在运行（锁文件存在且未超龄），本次跳过以避免重复抓取。")
        sys.exit(0)

    status = {
        "last_run_time": datetime.now(UTC).isoformat(),
        "mode": "incremental",
        "last_success_time": None,
        "last_success_count": None,
        "active_count": None,
        "expected_count": None,
        "added": 0, "updated": 0, "removed": 0, "unchanged": 0,
        "warning": None,
        "last_error": None,
        "last_error_time": None,
    }
    try:
        rate = RateLimiter(args.delay_min, args.delay_max)
        fetch_detail_enabled = not args.no_detail

        # —— 载入历史存储（mof_laws.json 本身即为持久化存储）——
        store_path = os.path.join(args.outdir, "mof_laws.json")
        prev_items = []
        if os.path.exists(store_path):
            try:
                pd = json.load(open(store_path, encoding="utf-8"))
                if isinstance(pd, dict):
                    prev_items = pd.get("items", []) or []
                elif isinstance(pd, list):
                    prev_items = pd
            except Exception as e:
                logger.warning("历史存储读取失败，本次将作为全量新增处理：%s", e)
                prev_items = []
        prev_by_id = {it.get("id"): it for it in prev_items if it.get("id")}

        new_store = []
        current_raw_by_id = {}
        expected_total = 0
        added = updated = unchanged = 0

        for lfgcc in args.categories:
            raw_records, exp = fetch_category_records(
                lfgcc, cat_map.get(lfgcc, lfgcc),
                size=args.size, rate=rate, max_items=args.max_items,
            )
            expected_total += exp
            refetch_att = args.refetch_attachments and fetch_detail_enabled
            # 附件主机不可用时可整体跳过附件（--no-attachments），保障主数据全量不被拖垮
            fetch_att = fetch_detail_enabled and not args.no_attachments
            for rec in raw_records:
                rid = rec.get("id")
                current_raw_by_id[rid] = rec
                prev = prev_by_id.get(rid)
                sig_changed = prev is not None and _sig_current(rec) != _sig_prev(prev)
                if prev is None:
                    # 新增：抓详情以补全字段 + 抓附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = collect_attachments(rid, rate) if fetch_att else []
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    entry["active"] = True
                    entry["change"] = "added"
                    added += 1
                elif sig_changed:
                    # 变更：重新抓详情与附件
                    detail = fetch_detail(rid, rate) if fetch_detail_enabled else None
                    atts = collect_attachments(rid, rate) if fetch_att else []
                    entry = build_entry(rec, detail, lfgcc, cat_map.get(lfgcc, lfgcc),
                                       fetch_detail_enabled, attachments=atts)
                    entry["active"] = True
                    entry["change"] = "updated"
                    updated += 1
                else:
                    # 未变：保留历史记录（含历史 attachments），跳过详情与附件抓取
                    entry = dict(prev)
                    if refetch_att and fetch_att:
                        atts = collect_attachments(rid, rate)
                        entry["attachments"] = atts
                        entry["attachment_count"] = len(atts)
                        entry["attachment_names"] = "; ".join(
                            a.get("file_name", "") for a in atts)
                    entry["active"] = True
                    entry["change"] = "unchanged"
                    unchanged += 1
                new_store.append(entry)

        # —— 历史中存在但本次列表已无 -> 视为已移除（保留记录，标记 inactive）——
        removed = 0
        for rid, prev in prev_by_id.items():
            if rid not in current_raw_by_id:
                e = dict(prev)
                e["active"] = False
                e["change"] = "removed"
                e["removed_at"] = datetime.now(UTC).isoformat()
                new_store.append(e)
                removed += 1

        active_count = sum(1 for e in new_store if e.get("active"))

        # —— 漏抓自检：活跃条数应与当前列表预期总量一致（验证模式除外）——
        if expected_total and not args.max_items and active_count != expected_total:
            status["warning"] = (
                f"活跃条目 {active_count} 与预期 {expected_total} 不一致，"
                "可能抓取期间数据发生变更，建议复核。"
            )
            logger.warning(status["warning"])

        # —— 原子写输出（失败时不覆盖上一次成功结果）——
        json_path = store_path
        csv_path = os.path.join(args.outdir, "mof_laws.csv")
        html_path = os.path.join(os.path.dirname(args.outdir), "reports", "mof_laws_report.html")
        save_json(new_store, json_path)
        save_csv(new_store, csv_path)
        save_html_report(new_store, html_path)

        status.update({
            "last_success_time": datetime.now(UTC).isoformat(),
            "last_success_count": len(new_store),
            "active_count": active_count,
            "expected_count": expected_total,
            "added": added, "updated": updated, "removed": removed, "unchanged": unchanged,
        })
        logger.info("✅ 增量更新完成：新增 %d / 更新 %d / 移除 %d / 未变 %d；活跃 %d 条。",
                    added, updated, removed, unchanged, active_count)
        logger.info("   JSON : %s", json_path)
        logger.info("   CSV  : %s", csv_path)
        logger.info("   HTML : %s", html_path)
    except Exception as e:
        # —— 异常状态处理：记录错误，保留上一次成功结果（不覆盖）——
        status["last_error"] = f"{type(e).__name__}: {e}"
        status["last_error_time"] = datetime.now(UTC).isoformat()
        logger.exception("抓取失败：%s", e)
    finally:
        write_status(args.outdir, status)
        release_lock(lock_path)

    # 异常时以非 0 退出，便于调度系统感知失败
    if status["last_error"]:
        sys.exit(1)

if __name__ == "__main__":
    main()
