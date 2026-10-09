#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国家金融监督管理总局 —— 政策法规全量抓取脚本（refactor 迁入版）。

原 modules/regulatory_scrapers/collectors/nfra_collector.py 迁出，逻辑逐函数等价。
仅 import 路径改为相对/绝对导入；`download_original_doc` 的 local_path 基址仍固定为
regulatory_scrapers（字节一致）；缓存/附件产物继续走 docs_root / source_cache_root 绝对路径。
实现 SourceCollector：NfraCollector.collect(out_dir) 为被 COLLECT_CMD 调用的入口。
"""

# ---- 仓库引导（使 std_lib/config 可导入）----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import csv
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from ..base import REPO_ROOT

# 附件 local_path / 下载原文 relpath 的相对基址固定为 regulatory_scrapers（与旧 collectors/ 位置一致）。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "modules", "regulatory_scrapers")

# 确保项目根（含 std_lib 包）在 sys.path，使 `from std_lib.scraper_std.crawler_common import` 可达
if SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, SCRAPERS_ROOT)

# 共享 UA 池（反爬轮换）；缺模块时回退单 UA，避免启动失败
try:
    from std_lib.scraper_std.crawler_common import USER_AGENTS
except ImportError:  # pragma: no cover
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ]

import faulthandler

faulthandler.enable()

try:
    import lxml.html
except ImportError as e:  # pragma: no cover
    sys.exit("缺少依赖 lxml，请先安装: pip install lxml\n%s" % e)

# ----------------------------- 配置 -----------------------------
BASE = "https://www.nfra.gov.cn"
API = BASE + "/cbircweb"
PARENT_ITEM_ID = 926                      # 政策法规
LIST_ENDPOINT = API + "/DocInfo/SelectDocByItemIdAndChild"
CHILD_ENDPOINT = API + "/DocInfo/SelectItemAndDocByItemPId"
DETAIL_ENDPOINT = API + "/DocInfo/SelectByDocId"

PAGE_SIZE = 18  # 与官网前端保持一致

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": (
        BASE + "/cn/view/pages/ItemList.html?itemPId=923&itemId=926"
        "&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84"
    ),
}

_SSL_CTX = ssl.create_default_context()

# 缓存目录（由 --cache-dir 设置）。命中缓存则跳过网络，便于断网/续跑/复现。
from std_lib.scraper_std.cache_store import OfflineMiss, bind_source_cache, docs_root

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
_OfflineMiss = OfflineMiss


def _init_cache(path=None, offline=False):
    """统一缓存根绑定（缺省 cache_store.source_cache_root("nfra")，单物理根）；path 显式可覆盖。"""
    global _RESP
    _RESP = bind_source_cache("nfra", "json", root=path)
    if offline and _RESP is not None:
        _RESP.set_offline(True)


def set_cache_dir(path):
    """兼容旧调用（同目录脚本）：仅设根，沿用当前离线态。"""
    _init_cache(path)


def set_offline(flag):
    if _RESP is not None:
        _RESP.set_offline(flag)


def _cache_path(url, params):
    """根据接口与参数生成确定性缓存文件名（委托通用模块，命名算法保持不变）。"""
    if _RESP is None:
        raise RuntimeError("缓存未初始化：请先调用 set_cache_dir(path)")
    return _RESP.path(url, params)

# ------------------------- 工具函数 -------------------------
def make_opener():
    """返回复用的 OpenerDirector（keep-alive 由 http.client 连接池处理）。"""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))

def http_get_json(opener, url, params, timeout=25, max_retries=4):
    """带超时与重试退避的 JSON GET。"""
    full = url + "?" + urllib.parse.urlencode(params)

    # 1) 缓存命中：离线/续跑
    if _RESP is not None:
        cp = _cache_path(url, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                pass  # 缓存损坏则重新请求
        if _RESP.offline:
            raise _OfflineMiss("%s ? %s" % (url, urllib.parse.urlencode(params)))

    # 2) 实时请求
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(full, headers=DEFAULT_HEADERS, method="GET")
        req.add_header("User-Agent", random.choice(USER_AGENTS))
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = resp.getcode()
                raw = resp.read().decode("utf-8", errors="replace")
            if status != 200:
                raise urllib.error.HTTPError(full, status, "HTTP %d" % status, None, None)  # type: ignore[arg-type]
            data = json.loads(raw)
            if data.get("rptCode") != 200:
                raise RuntimeError("接口返回非成功码 rptCode=%s msg=%s"
                                   % (data.get("rptCode"), data.get("msg")))
            if _RESP is not None:
                try:
                    _RESP.put(url, params, data)
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass
            return data
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, ConnectionError, json.JSONDecodeError, RuntimeError) as e:
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print("      [retry %d/%d] %s，%.1fs 后重试" % (attempt, max_retries, e, wait),
                      flush=True)
                time.sleep(wait)
    raise RuntimeError("请求失败（已重试 %d 次）: %s" % (max_retries, last_err))

def html_to_text(html):
    """将正文 HTML（含 Word 命名空间）转为紧凑纯文本。"""
    if not html:
        return ""
    try:
        doc = lxml.html.fromstring(html)
        for bad in doc.xpath("//script | //style"):
            bad.getparent().remove(bad)
        text = doc.text_content()
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", "\n", html)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text

_DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]+[〔\[]?\d{4}[〕\]]\d+号")
_SHIXING_RE = re.compile(r"自\s*(\d{4}[-年./]\d{1,2}[-月./]\d{1,2})\s*[日]?\s*起施行")

def extract_document_no(text):
    """从正文中抽取发文字号。"""
    if not text:
        return None
    try:
        try:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import in_abolish_context as _a
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
        except ImportError:
            from std_lib.scraper_std.doc_number import extract_doc_number as _u
            from std_lib.scraper_std.doc_number import in_abolish_context as _a
            from std_lib.scraper_std.doc_number import normalize_doc_number as _n
    except Exception:  # pragma: no cover  # noqa: BLE001
        _u = _n = _a = None  # type: ignore[assignment]
    dn = ""
    if _u is not None:
        dn = _u(text) or ""
    if not dn:
        m = _DOC_NO_RE.search(text)
        dn = (_n(m.group(0)) if _n is not None else m.group(0)) if m else ""   # N-67
    if not dn:
        return None
    if _a is not None and _a(text, dn):
        return None
    return dn

def extract_effective_date(text, builddate=None):
    """优先从正文 '自 X 年 X 月 X 日起施行' 抽取施行日期；否则回退成文日期。"""
    if text:
        m = _SHIXING_RE.search(text)
        if m:
            nums = re.findall(r"\d+", m.group(1))
            if len(nums) >= 3:
                return "%s-%02d-%02d" % (nums[0], int(nums[1]), int(nums[2]))
            return m.group(1)
    if builddate:
        return str(builddate).strip()
    return None

def make_summary(text, max_len=200):
    """正文摘要：取开头若干有效句，截断到 max_len。"""
    if not text:
        return ""
    parts = re.split(r"[。\n！？]", text)
    parts = [p.strip() for p in parts if p.strip()]
    summary = ""
    for p in parts:
        if len(summary) + len(p) + 1 > max_len:
            if not summary:
                summary = p[:max_len]
            break
        summary += p + "。"
        if len(summary) >= max_len:
            break
    return summary[:max_len]

def sleep_between(delay_min, delay_max):
    if delay_max > delay_min:
        time.sleep(random.uniform(delay_min, delay_max))
    else:
        time.sleep(delay_min)

# ------------------------- 抓取逻辑 -------------------------
def get_child_items(opener):
    """获取 政策法规 下的子栏目 [{itemId,itemName,type}]。"""
    data = http_get_json(opener, CHILD_ENDPOINT, {"itemId": PARENT_ITEM_ID, "pageSize": 50})
    items = data.get("data") or []
    return [{"item_id": it.get("itemId"),
             "item_name": it.get("itemName"),
             "type": it.get("type")} for it in items]

def get_list_for_item(opener, item_id, delay_min, delay_max):
    """分页遍历某栏目，返回所有 rows。"""
    rows_all: list = []
    page = 1
    while True:
        data = http_get_json(opener, LIST_ENDPOINT,
                             {"itemId": item_id, "pageSize": PAGE_SIZE, "pageIndex": page})
        payload = data.get("data") or {}
        total = payload.get("total", 0)
        rows = payload.get("rows") or []
        rows_all.extend(rows)
        if not rows or len(rows_all) >= total:
            break
        page += 1
        sleep_between(delay_min, delay_max)
    return rows_all

def get_detail(opener, doc_id, delay_min, delay_max):
    """获取单篇详情，返回清洗后的字段字典；失败返回 None。"""
    data = http_get_json(opener, DETAIL_ENDPOINT, {"docId": doc_id})
    d = data.get("data")
    if isinstance(d, list):
        d = d[0] if d else {}
    if not isinstance(d, dict):
        return None
    doc_clob = d.get("docClob") or ""
    text = html_to_text(doc_clob)
    document_no = d.get("documentNo")
    if not document_no:
        document_no = extract_document_no(text)
    effective = extract_effective_date(text, d.get("builddate"))
    issuing = d.get("docSource") or d.get("agencyTypeName") or ""
    summary = d.get("docSummary")
    if not summary:
        summary = make_summary(text)
    return {
        "doc_id": d.get("docId"),
        "title": d.get("docSubtitle") or d.get("docTitle") or "",
        "publish_date": (d.get("publishDate") or "")[:10],
        "build_date": d.get("builddate") or "",
        "document_no": document_no,
        "effective_date": effective,
        "index_no": d.get("indexNo") or "",
        "issuing_authority": issuing,
        "category_type": d.get("interviewTypeName") or "",
        "summary": summary,
        "content": text,
        "doc_file_url": (BASE + d["docFileUrl"]) if d.get("docFileUrl") else "",
        "pdf_file_url": (BASE + d["pdfFileUrl"]) if d.get("pdfFileUrl") else "",
    }

def build_detail_url(doc_id, item_id):
    return "%s/cn/view/pages/ItemDetail.html?docId=%s&itemId=%s" % (BASE, doc_id, item_id)

# 附件产物根（统一 data/docs，与 nfra_fetch_attachments.py 一致）
_ATT_DIR = docs_root("nfra", "attachments")

def load_attachments(doc_id):
    """读取 nfra_fetch_attachments.py 落盘的附件清单与抽取文本（纯标准库，离线安全）。"""
    mp = os.path.join(_ATT_DIR, str(doc_id), "manifest.json")
    if not os.path.exists(mp):
        return []
    try:
        m = json.load(open(mp, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in m.get("attachments", []):
        item = {
            "title": e.get("title", ""),
            "url": e.get("url", ""),
            "attachment_name": e.get("attachment_name", ""),
            "page_count": e.get("page_count"),
            "char_count": e.get("char_count"),
            "sha256": e.get("sha256"),
            "extracted": e.get("extracted", False),
            "ocr_status": e.get("ocr_status"),
            "quality": e.get("quality", []),
        }
        text = ""
        if e.get("extracted") and e.get("text_file"):
            tf = e["text_file"]
            did = str(e.get("doc_id") or doc_id)
            cands = [
                os.path.join(_ATT_DIR, did, os.path.basename(tf)),
                os.path.join(_ATT_DIR, os.path.basename(tf)),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), tf),
            ]
            for p in cands:
                if not os.path.exists(p):
                    continue
                try:
                    with open(p, encoding="utf-8") as fh:
                        text = fh.read()
                    break
                except Exception:  # noqa: BLE001
                    text = ""
        item["text"] = text
        for k in ("table_structured", "table_raw_text", "table_recovery_method"):
            if k in e:
                item[k] = e[k]
        for k in ("rich_structured", "rich_text", "rich_count"):
            if k in e:
                item[k] = e[k]
        out.append(item)
    return out

_DOC_DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}


def download_original_doc(rec: dict, doc_id: int, scr_root: str) -> str:
    """正文原文下载（doc_file_url/pdf_file_url → data/docs 统一根）。"""
    url = (rec.get("doc_file_url") or "").strip() or (rec.get("pdf_file_url") or "").strip()
    if not url:
        return ""
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".doc", ".docx", ".pdf"):
        return ""
    dest_dir = docs_root("nfra", "downloaded_docs")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{doc_id}{ext}")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return os.path.relpath(dest, scr_root).replace("\\", "/")
    req = urllib.request.Request(url, headers=_DOC_DL_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
    except Exception:  # noqa: BLE001  单条原文下载失败不阻断
        return ""
    if not data:
        return ""
    try:
        with open(dest, "wb") as fh:
            fh.write(data)
    except OSError:
        return ""
    return os.path.relpath(dest, scr_root).replace("\\", "/")


def scrape(args):
    _init_cache(args.cache_dir or None, args.offline)
    opener = make_opener()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    print("[1/4] 获取政策法规子栏目结构 ...", flush=True)
    children = get_child_items(opener)
    children.append({"item_id": PARENT_ITEM_ID, "item_name": "政策法规(本级)", "type": ""})
    print("      子栏目: " + ", ".join("%s(%s)" % (c["item_name"], c["item_id"])
                                       for c in children), flush=True)

    print("[2/4] 分页遍历各栏目列表 ...", flush=True)
    list_map = {}
    ordered_ids = []
    for c in children:
        print("      >> 开始抓取栏目 %s(%s)" % (c["item_name"], c["item_id"]), flush=True)
        try:
            rows = get_list_for_item(opener, c["item_id"], args.delay_min, args.delay_max)
        except Exception as e:  # noqa: BLE001
            print("      [WARN] 栏目 %s 列表抓取失败: %s" % (c["item_name"], e), flush=True)
            rows = []
        for r in rows:
            did = r.get("docId")
            if did is None:
                continue
            if did not in list_map:
                list_map[did] = {
                    "doc_id": did,
                    "title": r.get("docSubtitle") or r.get("docTitle") or "",
                    "publish_date": (r.get("publishDate") or "")[:10],
                    "category": c["item_name"],
                    "item_id": c["item_id"],
                    "is_title_link": r.get("isTitleLink"),
                    "title_link": r.get("titleLink"),
                    "doc_file_url": (BASE + r["docFileUrl"]) if r.get("docFileUrl") else "",
                    "pdf_file_url": (BASE + r["pdfFileUrl"]) if r.get("pdfFileUrl") else "",
                }
                ordered_ids.append(did)
        print("      %-22s 命中 %d 篇（累计去重 %d）" % (c["item_name"], len(rows), len(ordered_ids)),
              flush=True)
        sleep_between(args.delay_min, args.delay_max)

    if args.limit and args.limit > 0:
        ordered_ids = ordered_ids[:args.limit]
        print("      [INFO] 受 --limit 限制，仅处理前 %d 篇" % len(ordered_ids), flush=True)

    # —— 主库并集保护（stale/断层丢 doc_id）——
    _master_path = os.path.join(out_dir, "nfra_regulations.json")
    _master_map = {}
    if os.path.exists(_master_path):
        try:
            with open(_master_path, encoding="utf-8") as _fh:
                _md = json.load(_fh)
            for _mr in (_md.get("records") or []):
                if _mr.get("doc_id") is not None:
                    _master_map[str(_mr["doc_id"])] = _mr
        except Exception as _e:  # noqa: BLE001
            print("      [WARN] 现有主库并集保护读取失败: %s" % _e, flush=True)
    _merged_n = 0
    for _did in sorted(set(_master_map) - {str(d) for d in ordered_ids}):
        _mr = _master_map[_did]
        ordered_ids.append(_did)
        list_map[_did] = {
            "doc_id": _did, "category": _mr.get("category", ""),
            "is_title_link": "0", "title_link": "",
            "title": _mr.get("title", ""), "publish_date": _mr.get("publish_date", ""),
            "doc_file_url": _mr.get("doc_file_url", ""), "pdf_file_url": _mr.get("pdf_file_url", ""),
        }
        _merged_n += 1
    if _merged_n:
        print("      [INFO] 主库并集保护：并入 %d 条列表缺失 doc_id（stale/分页断层）" % _merged_n, flush=True)

    print("[3/4] 逐篇抓取详情（正文/发文字号/发文机关/生效日期）...", flush=True)
    records = []
    errors = []
    for idx, did in enumerate(ordered_ids, 1):
        base = list_map[did]
        if str(did) in _master_map:
            records.append(_master_map[str(did)])
            continue
        rec = None
        if str(base.get("is_title_link")) == "1" and base.get("title_link"):
            rec = {
                "doc_id": did, "title": base["title"], "category": base["category"],
                "publish_date": base["publish_date"], "build_date": "",
                "document_no": "", "effective_date": "", "index_no": "",
                "issuing_authority": "", "category_type": "",
                "summary": "", "content": "",
                "detail_url": base["title_link"],
                "doc_file_url": base["doc_file_url"], "pdf_file_url": base["pdf_file_url"],
            }
        else:
            try:
                if args.no_detail:
                    raise RuntimeError("skip-detail")
                det = get_detail(opener, did, args.delay_min, args.delay_max)
                if det is None:
                    raise RuntimeError("详情为空")
                rec = {
                    "doc_id": did,
                    "title": det["title"] or base["title"],
                    "category": base["category"],
                    "publish_date": det["publish_date"] or base["publish_date"],
                    "build_date": det["build_date"],
                    "document_no": det["document_no"] or "",
                    "effective_date": det["effective_date"] or "",
                    "index_no": det["index_no"],
                    "issuing_authority": det["issuing_authority"],
                    "category_type": det["category_type"],
                    "summary": det["summary"],
                    "content": det["content"],
                    "detail_url": build_detail_url(did, base["item_id"]),
                    "doc_file_url": det["doc_file_url"] or base["doc_file_url"],
                    "pdf_file_url": det["pdf_file_url"] or base["pdf_file_url"],
                }
            except _OfflineMiss:
                rec = {
                    "doc_id": did, "title": base["title"], "category": base["category"],
                    "publish_date": base["publish_date"], "build_date": "",
                    "document_no": "", "effective_date": "", "index_no": "",
                    "issuing_authority": "", "category_type": "",
                    "summary": "", "content": "",
                    "detail_url": build_detail_url(did, base["item_id"]),
                    "doc_file_url": base["doc_file_url"], "pdf_file_url": base["pdf_file_url"],
                }
                print("      [offline] docId=%s 详情未缓存，仅列表面板" % did, flush=True)
            except Exception as e:  # noqa: BLE001
                errors.append({"doc_id": did, "title": base["title"], "error": str(e)})
                print("      [ERR] docId=%s 详情失败: %s" % (did, e), flush=True)
        if rec:
            if getattr(args, "download_originals", False):
                rec["downloaded_doc_path"] = download_original_doc(rec, did, SCRAPERS_ROOT)
            _atts = load_attachments(did)
            rec["attachments"] = _atts
            _tbls = [t for a in _atts for t in (a.get("table_structured") or [])]
            if _tbls:
                rec["table_structured"] = _tbls
                rec["table_recovery_method"] = "structured"
                _raws = [a.get("table_raw_text") for a in _atts if a.get("table_raw_text")]
                if _raws:
                    rec["table_raw_text"] = "\n\n".join(_raws)
            _richo = [o for a in _atts for o in (a.get("rich_structured") or [])]
            if _richo:
                rec["rich_structured"] = _richo
                rec["rich_count"] = len(_richo)
                _rtext = [a.get("rich_text") for a in _atts if a.get("rich_text")]
                if _rtext:
                    rec["rich_text"] = "\n".join(_rtext)
            records.append(rec)
        print("      进度 %d/%d  docId=%s  %s" % (idx, len(ordered_ids), did,
              rec["title"][:30] if rec else "?"), flush=True)
        sleep_between(args.delay_min, args.delay_max)

    print("[4/4] 写出结构化结果 ...", flush=True)
    json_path = os.path.join(out_dir, "nfra_regulations.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {
                "source": BASE,
                "parent_item": PARENT_ITEM_ID,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "total_records": len(records),
                "error_count": len(errors),
            },
            "records": records,
            "errors": errors,
        }, f, ensure_ascii=False, indent=2)

    csv_path = ""
    if getattr(args, "csv", False):
        csv_path = os.path.join(out_dir, "nfra_regulations.csv")
        fields = ["doc_id", "title", "category", "publish_date", "build_date", "document_no",
                  "effective_date", "index_no", "issuing_authority", "category_type",
                  "summary", "detail_url", "doc_file_url", "pdf_file_url",
                  "attachment_count", "attachment_total_pages", "attachment_total_chars",
                  "attachment_text"]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in records:
                row = dict(r)
                row["summary"] = (row.get("summary") or "").replace("\n", " ")
                atts = r.get("attachments") or []
                row["attachment_count"] = len(atts)
                row["attachment_total_pages"] = sum((a.get("page_count") or 0) for a in atts)
                row["attachment_total_chars"] = sum((a.get("char_count") or 0) for a in atts)
                row["attachment_text"] = " ".join(
                    (a.get("text") or "").replace("\n", " ") for a in atts)
                w.writerow(row)

    print("\n完成！输出文件：")
    print("  JSON : %s" % json_path)
    if csv_path:
        print("  CSV  : %s" % csv_path)
    print("成功 %d 篇，失败 %d 篇" % (len(records), len(errors)))
    return json_path, csv_path


class NfraCollector:
    """国家金融监督管理总局 政策法规 抓取器；实现 SourceCollector。"""

    source_id = "nfra"

    def collect(self, out_dir: str, *, limit: int = 0, no_detail: bool = False,
                download_originals: bool = True, csv: bool = False, cache_dir: str = "",
                offline: bool = False, delay_min: float = 1.5, delay_max: float = 3.0) -> str:
        args = argparse.Namespace(
            out_dir=out_dir,
            page_size=PAGE_SIZE,
            limit=limit,
            no_detail=no_detail,
            no_originals=not download_originals,
            download_originals=download_originals,
            csv=csv,
            cache_dir=cache_dir,
            offline=offline,
            delay_min=delay_min,
            delay_max=delay_max,
        )
        scrape(args)
        return os.path.join(out_dir, "nfra_regulations.json")


def main():
    ap = argparse.ArgumentParser(description="国家金融监督管理总局 政策法规 全量抓取")
    ap.add_argument("--out-dir",
                    default=os.path.join(SCRAPERS_ROOT, "data", "raw"))
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE)
    ap.add_argument("--delay-min", type=float, default=1.5, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=3.0, help="请求最大间隔(秒)")
    ap.add_argument("--limit", type=int, default=0,
                    help="仅抓取前 N 篇（0=全部，用于快速验证）")
    ap.add_argument("--no-detail", action="store_true",
                    help="仅抓取列表、跳过详情页（用于快速验证列表遍历）")
    ap.add_argument("--no-originals", action="store_true",
                    help="跳过正文原文下载")
    ap.add_argument("--download-originals", action="store_true",
                    help="[兼容保留] 旧旗标（原文下载已默认开启，本参数为 no-op）")
    ap.add_argument("--csv", action="store_true",
                    help="额外输出 CSV 表格（默认仅写 JSON 主库）")
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()
    args.download_originals = not getattr(args, "no_originals", False)
    try:
        scrape(args)
    except Exception as e:  # 顶层兜底，避免现场丢失
        import traceback
        tb = traceback.format_exc()
        sys.stderr.write("FATAL: %s\n%s\n" % (e, tb))
        with open(os.path.join("logs", "FATAL.log"), "w", encoding="utf-8") as fh:
            fh.write("FATAL: %s\n%s\n" % (e, tb))
        raise

if __name__ == "__main__":
    main()
