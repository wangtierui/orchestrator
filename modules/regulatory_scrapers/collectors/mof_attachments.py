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
import html
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

# 共享加固工具层（UA 池、文档文本抽取、魔数纠正命名、表格结构化）
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS as CC_USER_AGENTS
    from std_lib.scraper_std.crawler_common import extract_document_text
    from std_lib.scraper_std.rich_object import rich_object_fields
    from std_lib.scraper_std.table_recovery import structured_table_fields
except ImportError:  # pragma: no cover
    CC_USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]
    def extract_document_text(data, name="", **kw):
        return {"text": "", "kind": "unknown", "extracted": False,
                "extract_status": "library_missing", "sha256": "",
                "needs_ocr": False, "garble_ratio": 0.0, "size_bytes": len(data)}
    def structured_table_fields(data, name="", *, kind=None):
        return {}
    def rich_object_fields(data, name="", *, image_dir=None, rec_key=""):
        return {}

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
# 通用缓存模块（五源统一抽象层：std_lib/scraper_std/cache_store，2026-09-05）
# mof 接口均返回 JSON → 委托 ResponseCache；命中读盘跳过网络、离线缺失抛 OfflineMiss、
# 仅成功响应（code==200）落盘，不缓存错误/拦截页。
try:
    from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache  # noqa: F401
except ImportError:  # pragma: no cover
    from std_lib.scraper_std.cache_store import OfflineMiss

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss  # 兼容别名



HOST = "http://fgk.mof.gov.cn"

BASE = "/dev"  # 接口基础路径：/dev/lawFile/list 等

SRC_DIR = os.path.dirname(os.path.abspath(__file__))

REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）

from std_lib.scraper_std.cache_store import docs_root  # noqa: E402

ATTACHMENTS_DIR = docs_root("mof", "attachments")

DEFAULT_CATEGORIES = {
    "1000000000000300000": "财政法律法规（财政部规章）",
    "1000000000000400000": "财政部规范性文件",
}

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

_TAG_RE = re.compile(r"<[^>]+>")

_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

_WS_RE = re.compile(r"\s+")

ATTACH_PATH = "/file/f/get"  # GET ?infoId=<id>&fileType=0(相关附件)/90(文字版)

_ATTACH_KIND = {"0": "attachment", "90": "text_version"}

_ATT_502_STREAK = 0

_ATT_CIRCUIT_OPEN = False

_MAX_ATT_502_STREAK = 8

_SCRAPERS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, _SCRAPERS_ROOT)


_LOCKS = {}   # lock_path -> fs_lock.ProcessLock（release_lock 需释放同一实例）

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


def _finalize_attachment(att, data, fname, law_id, dest):
    """附件字节 → 落盘 + 文本/表格/富内容抽取，回填 att（下载与本地复用共用）。"""
    with open(dest, "wb") as f:
        f.write(data)
    att["local_path"] = os.path.relpath(dest, REPO_ROOT).replace("\\", "/")
    att["size_bytes"] = len(data)
    try:
        ext = extract_document_text(data, fname)
        att["text"] = ext.get("text", "")
        att["extracted"] = ext.get("extracted", False)
        att["extract_status"] = ext.get("extract_status", "unsupported")
        att["attachment_kind"] = ext.get("kind", "unknown")
        att["sha256"] = ext.get("sha256", "")
        att["needs_ocr"] = ext.get("needs_ocr", False)
        att["garble_ratio"] = ext.get("garble_ratio", 0.0)
        try:
            att.update(structured_table_fields(data, fname))
        except Exception:
            pass
        try:
            att.update(rich_object_fields(data, fname,
                                          image_dir=docs_root("mof", "diagrams"),
                                          rec_key=str(law_id)))
        except Exception:
            pass
    except Exception as e:
        logger.warning("附件文本抽取异常 %s：%s", att.get("file_url"), e)
        att["extracted"] = False
        att["extract_status"] = "extract_error"
    return att


def download_attachment(att, law_id, outdir, rate, timeout=60, max_retries=6):
    """下载单个附件到 data/docs/mof_regulations_scraper/attachments/<law_id>/，返回带本地相对路径与大小的元数据；失败返回 None。

    增量策略（2026-09-10，附件主机 502 环境）：
      - **幂等复用**：本地已存在非空副本 → 直接本地重抽（不再请求，规避重复下载/502 空转）；
      - **502 熔断**：502/503 仅重试 2 次即跳过，连续 8 次 502 熔断，本次运行后续附件全部跳过；
      - 429/500 保留指数退避重试；404 等不可恢复直接放弃。
    """
    global _ATT_502_STREAK, _ATT_CIRCUIT_OPEN
    if _ATT_CIRCUIT_OPEN:
        return None
    attach_dir = os.path.join(ATTACHMENTS_DIR, str(law_id))
    os.makedirs(attach_dir, exist_ok=True)
    fname = safe_filename(att.get("file_name"), att.get("extension"),
                          f"{law_id}_{att.get('file_type')}")
    dest = os.path.join(attach_dir, fname)
    # 幂等复用：本地已有非空副本 → 不再下载，仅本地重抽（增量续跑核心）
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        try:
            with open(dest, "rb") as f:
                return _finalize_attachment(att, f.read(), fname, law_id, dest)
        except OSError:
            pass
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
            return _finalize_attachment(att, data, fname, law_id, dest)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (502, 503):
                # 附件主机限流/不可用：仅重试 2 次，连续达阈值熔断（防数十小时空转）
                _ATT_502_STREAK += 1
                if _ATT_502_STREAK >= _MAX_ATT_502_STREAK:
                    _ATT_CIRCUIT_OPEN = True
                    logger.error("附件主机连续 %d 次 502/503，熔断：本次运行剩余附件跳过。",
                                 _ATT_502_STREAK)
                    return None
                if attempt >= 2:
                    logger.warning("附件下载 HTTP %s @ %s：跳过（熔断计数 %d）",
                                   e.code, att.get("file_url"), _ATT_502_STREAK)
                    return None
                backoff = min(2 ** attempt * 2.0, 20) + random.uniform(0, 1)
                if rate is not None:
                    rate.penalize(backoff)
                time.sleep(backoff)
                continue
            if e.code in (429, 500):
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
    附件主机熔断（连续 502）后不再尝试下载。
    """
    if _ATT_CIRCUIT_OPEN:
        return []
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
        "attachment_content": "\n\n".join(
            a.get("text") for a in (attachments or []) if a.get("text")
        ),
        "table_structured": [t for a in (attachments or [])
                             for t in (a.get("table_structured") or [])] or [],
        "table_raw_text": "\n\n".join(
            a.get("table_raw_text") for a in (attachments or []) if a.get("table_raw_text")
        ),
        "table_recovery_method": "structured" if any(
            a.get("table_structured") for a in (attachments or [])) else "",
        "rich_structured": [o for a in (attachments or [])
                            for o in (a.get("rich_structured") or [])] or [],
        "rich_text": "\n".join(
            a.get("rich_text") for a in (attachments or []) if a.get("rich_text")),
        "rich_count": sum((a.get("rich_count") or 0) for a in (attachments or [])),
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


__all__ = ["_finalize_attachment", "_mof_cache_key", "_request", "build_entry", "collect_attachments", "download_attachment", "fetch_attachments", "fetch_category_records", "fetch_list_page", "html_to_text", "normalize_date", "safe_filename"]
