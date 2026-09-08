#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国家金融监督管理总局 —— 政策法规全量抓取脚本
=====================================================================
目标页面:
  https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=926
    &itemUrl=ItemListRightMore.html&itemName=政策法规

实现说明（基于逆向前端 JS 得到的数据接口，而非静态 HTML 解析）:
  - 列表数据接口（分页 / "加载更多" 的真实后端）:
        GET {BASE}/cbircweb/DocInfo/SelectDocByItemIdAndChild
            ?itemId={栏目ID}&pageSize=18&pageIndex=N
        返回 {"rptCode":200,"data":{"total":N,"rows":[...]}}
  - 栏目结构接口:
        GET {BASE}/cbircweb/DocInfo/SelectItemAndDocByItemPId?itemId=926
        返回 政策法规(926) 下的子栏目（法律法规=927、政策规章规范性文件=928 …）
  - 详情接口（正文 / 发文字号 / 发文机关 / 成文日期）:
        GET {BASE}/cbircweb/DocInfo/SelectByDocId?docId={文档ID}
        返回 docClob(正文 HTML)、documentNo(发文字号)、docSource/agencyTypeName(发文机关)、
              builddate(成文日期)、indexNo(索引号) 等字段

依赖: 仅 Python 标准库 + lxml（正文 HTML -> 纯文本）。无需 requests/beautifulsoup4。

特性:
  * 会话复用（OpenerDirector）+ 自动重试退避，处理网络抖动与限流（429/5xx）
  * 随机请求间隔，控制抓取频率，规避反爬
  * 分页遍历每个子栏目直到取尽，按 docId 去重保证完整
  * 正文 HTML -> 纯文本；发文字号 / 施行日期正则抽取；摘要自动生成
  * 结构化输出 JSON + CSV + 汇总报告

合规与礼貌原则:
  * 仅抓取公开发布的政策法规数据，不做登录/越权
  * 默认请求间隔 1.5~3.0 秒，必要时可用 --delay-min/--delay-max 调大
  * 遇到连续失败会主动退避并提示，不暴力重试
=====================================================================
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

# 确保项目根（含 std_lib 包）在 sys.path，使 `from std_lib.scraper_std.crawler_common import` 可达
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
# 2026-09-05 重构：核心缓存逻辑下沉至通用模块 std_lib/scraper_std/cache_store，
# 五源统一复用，缓存产物统一落盘 regulatory_scrapers/cache/<source>/。
from std_lib.scraper_std.cache_store import OfflineMiss, ResponseCache  # noqa: E402

_RESP = None  # ResponseCache 实例；None 表示未启用缓存
# 兼容别名（旧代码/脚本引用 m._OfflineMiss）
_OfflineMiss = OfflineMiss

def set_cache_dir(path):
    """设置请求缓存根目录（启用/禁用缓存）。path=None 表示禁用缓存。"""
    global _RESP
    _RESP = ResponseCache(path) if path else None

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
    """带超时与重试退避的 JSON GET。

    非 200、JSON 解析失败、网络异常均触发退避重试；连续失败抛异常由调用方处理。
    若启用缓存（_RESP）且命中，则直接读取本地文件，跳过网络。
    """
    full = url + "?" + urllib.parse.urlencode(params)

    # 1) 缓存命中：离线/续跑
    if _RESP is not None:
        cp = _cache_path(url, params)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass  # 缓存损坏则重新请求
        if _RESP.offline:
            # 离线模式：缓存缺失直接跳过，避免无意义的联网重试
            raise _OfflineMiss("%s ? %s" % (url, urllib.parse.urlencode(params)))

    # 2) 实时请求
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(full, headers=DEFAULT_HEADERS, method="GET")
        # 反爬加固：每次请求轮换 UA（避免单一 UA 被风控聚类）
        req.add_header("User-Agent", random.choice(USER_AGENTS))
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = resp.getcode()
                raw = resp.read().decode("utf-8", errors="replace")
            if status != 200:
                # 限流 / 服务端错误 -> 退避后重试
                raise urllib.error.HTTPError(full, status, "HTTP %d" % status, None, None)
            data = json.loads(raw)
            if data.get("rptCode") != 200:
                raise RuntimeError("接口返回非成功码 rptCode=%s msg=%s"
                                   % (data.get("rptCode"), data.get("msg")))
            # 写入缓存（仅成功响应）
            if _RESP is not None:
                try:
                    _RESP.put(url, params, data)
                except Exception:
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
        # 去除脚本/样式
        for bad in doc.xpath("//script | //style"):
            bad.getparent().remove(bad)
        text = doc.text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", "\n", html)
    # 折叠多余空行与首尾空白
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text

_DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]+[〔\[]?\d{4}[〕\]]\d+号")
_SHIXING_RE = re.compile(r"自\s*(\d{4}[-年./]\d{1,2}[-月./]\d{1,2})\s*[日]?\s*起施行")

# 废止/失效引用语境校验：委托五源统一模块 doc_number.in_abolish_context（唯一事实源，
# 2026-09-07 case 9 及同类 32 例）。详见 std_lib/scraper_std/doc_number.py。

def extract_document_no(text):
    """从正文中抽取发文字号，如 '金监规〔2024〕1号'。

    2026-09-05：优先委托五源统一模块 doc_number（15+ 优先级正则 + 半角 [ ] → 全角〔〕
    规范化 + 脏串清理）；模块不可用/未命中时回退原 _DOC_NO_RE，并对回退值做规范化。
    2026-09-07（case 9）：抽取结果须经废止/失效引用语境校验——若该文号实为被本文件
    废止的他文文号，则返回 None（本文件无字号时留空），禁止错配。
    """
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
    except Exception:  # pragma: no cover
        _u = _n = _a = None
    dn = ""
    if _u is not None:
        dn = _u(text) or ""
    if not dn:
        m = _DOC_NO_RE.search(text)
        dn = (_n(m.group(0)) if _n else m.group(0)) if m else ""
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
    """正文摘要：取开头若干有效句，截断到 max_len；首句超长时直接截断补齐。"""
    if not text:
        return ""
    parts = re.split(r"[。\n！？]", text)
    parts = [p.strip() for p in parts if p.strip()]
    summary = ""
    for p in parts:
        if len(summary) + len(p) + 1 > max_len:
            if not summary:           # 首句即超长：截断补齐
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
    """分页遍历某栏目，返回所有 rows（list 中每项含 docId/docSubtitle/publishDate 等）。"""
    rows_all = []
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

# 附件缓存根目录（与 nfra_fetch_attachments.py 默认落盘位置一致：<脚本目录>/cache/attachments）
_ATT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "attachments")

def load_attachments(doc_id):
    """读取 nfra_fetch_attachments.py 落盘的附件清单与抽取文本（纯标准库，离线安全）。

    返回 list[{title,url,attachment_name,page_count,char_count,sha256,
               extracted,ocr_status,quality,text}]；无附件则返回 []。
    """
    mp = os.path.join(_ATT_DIR, str(doc_id), "manifest.json")
    if not os.path.exists(mp):
        return []
    try:
        m = json.load(open(mp, encoding="utf-8"))
    except Exception:
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
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), e["text_file"])
            try:
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                text = ""
        item["text"] = text
        out.append(item)
    return out

def scrape(args):
    set_cache_dir(args.cache_dir)
    set_offline(args.offline)
    opener = make_opener()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    print("[1/4] 获取政策法规子栏目结构 ...", flush=True)
    children = get_child_items(opener)
    # 同时抓取父栏目自身（可能含未归入子栏目的文档），最终按 docId 去重
    children.append({"item_id": PARENT_ITEM_ID, "item_name": "政策法规(本级)", "type": ""})
    print("      子栏目: " + ", ".join("%s(%s)" % (c["item_name"], c["item_id"])
                                       for c in children), flush=True)

    print("[2/4] 分页遍历各栏目列表 ...", flush=True)
    list_map = {}          # docId -> 列表条目（含 category/itemId）
    ordered_ids = []
    for c in children:
        print("      >> 开始抓取栏目 %s(%s)" % (c["item_name"], c["item_id"]), flush=True)
        try:
            rows = get_list_for_item(opener, c["item_id"], args.delay_min, args.delay_max)
        except Exception as e:
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

    # 可选：限制抓取数量（用于快速验证）
    if args.limit and args.limit > 0:
        ordered_ids = ordered_ids[:args.limit]
        print("      [INFO] 受 --limit 限制，仅处理前 %d 篇" % len(ordered_ids), flush=True)

    # —— 主库并集保护（2026-09-04 修复：stale/断层丢 doc_id）——
    # 列表接口分页断层或站点下架（stale）会导致列表缓存不含历史 doc_id，重建即丢数据
    # （曾致统一主库 1939 条中的 14 条 cache 外条目无法由重建产出）。
    # 读现有主库（out_dir/nfra_regulations.json）并入缺失 doc_id，记录整体复用保留正文。
    _master_path = os.path.join(out_dir, "nfra_regulations.json")
    _master_map = {}
    if os.path.exists(_master_path):
        try:
            with open(_master_path, encoding="utf-8") as _fh:
                _md = json.load(_fh)
            for _mr in (_md.get("records") or []):
                if _mr.get("doc_id") is not None:
                    _master_map[str(_mr["doc_id"])] = _mr
        except Exception as _e:
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
            # 并集保护的 stale/断层条目：直接复用主库完整记录（保留正文），不重复抓详情
            records.append(_master_map[str(did)])
            continue
        rec = None
        # 外链型条目（isTitleLink==1）无站内详情页，直接采用列表信息
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
                # 离线模式：该文档详情未缓存，退化为仅含列表级字段的记录
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
            except Exception as e:
                errors.append({"doc_id": did, "title": base["title"], "error": str(e)})
                print("      [ERR] docId=%s 详情失败: %s" % (did, e), flush=True)
        if rec:
            rec["attachments"] = load_attachments(did)
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

    # 汇总报告
    report_path = os.path.join(out_dir, "README.md")
    by_cat = {}
    for r in records:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 国家金融监督管理总局 · 政策法规抓取结果\n\n")
        f.write("- 数据源: %s\n" % BASE)
        f.write("- 抓取时间: %s\n" % datetime.now().isoformat(timespec="seconds"))
        f.write("- 成功记录: %d 篇\n" % len(records))
        f.write("- 失败记录: %d 篇\n" % len(errors))
        f.write("\n## 分类统计\n\n")
        for k, v in by_cat.items():
            f.write("- %s：%d 篇\n" % (k, v))
        f.write("\n## 字段说明（口径）\n\n")
        f.write("| 字段 | 口径/来源 |\n")
        f.write("|------|----------|\n")
        f.write("| title | 标题（docSubtitle/docTitle） |\n")
        f.write("| category | 所属子栏目（法律法规 / 政策规章规范性文件） |\n")
        f.write("| publish_date | 发布日期（publishDate 截取日期） |\n")
        f.write("| build_date | 成文日期（builddate） |\n")
        f.write("| document_no | 发文字号（documentNo 字段；缺失时从正文抽取，"
                "命中废止/引用他文语境则留空，可能为空） |\n")
        f.write("| effective_date | 施行/生效日期（优先正文'自X起施行'，否则回退成文日期） |\n")
        f.write("| issuing_authority | 发文机关（docSource/agencyTypeName） |\n")
        f.write("| index_no | 索引号（indexNo） |\n")
        f.write("| summary | 内容摘要（docSummary 缺失时由正文自动生成） |\n")
        f.write("| detail_url | 站内详情页链接 |\n")
        f.write("| content | 正文全文（HTML 清洗后的纯文本，仅存于 JSON） |\n")
        f.write("| attachments | 附件清单（标题/URL/页数/字数/sha256/抽取状态/质量标志/正文），仅存于 JSON |\n")
        f.write("| attachment_count / attachment_total_pages / attachment_total_chars / attachment_text | 附件聚合指标与正文拼接，存于 CSV |\n")

        # 附件数据质量章节（对齐 DAMA / ISO 8000）
        qr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "cache", "attachments", "_quality_report.json")
        if os.path.exists(qr_path):
            try:
                qr = json.load(open(qr_path, encoding="utf-8"))
                f.write("\n## 附件数据质量（DAMA / ISO 8000 维度）\n\n")
                f.write("- 含附件文档数：%d 篇\n" % qr.get("docs_with_attachments", 0))
                f.write("- 附件总数：%d 个\n" % qr.get("total_attachments", 0))
                f.write("- 抽取成功：%d 个（抽取率 %.1f%%）\n"
                         % (qr.get("extracted", 0), (qr.get("extraction_rate", 0) or 0) * 100))
                f.write("- 疑似扫描件（需 OCR）：%d 个\n" % qr.get("needs_ocr", 0))
                f.write("- OCR 是否启用：%s\n" % ("是" if qr.get("ocr_enabled") else "否（仅标记，未抽取）"))
                if qr.get("failed_docs"):
                    f.write("- 处理失败文档：%d 篇（见 quality_report 明细）\n" % len(qr["failed_docs"]))
                by_type = qr.get("by_type") or {}
                if by_type:
                    f.write("\n### 按真实类型细分（魔数校验，已纠正扩展名误标）\n\n")
                    f.write("| 类型 | 说明 | 附件数 | 已抽取 | 源文件留存 | 抽取字数 |\n")
                    f.write("|------|------|-------:|-------:|-----------:|---------:|\n")
                    type_label = {
                        "pdf": "PDF（文本型）",
                        "docx": "Word(.docx OOXML)",
                        "xlsx": "Excel(.xlsx OOXML)",
                        "xls_legacy": "Excel(.xls 旧版 OLE2，xlrd 抽取)",
                        "doc_legacy": "Word(.doc 旧版 OLE2，需外部转换)",
                        "archive": "压缩包(.rar/未知)",
                        "pdf_corrupt": "PDF 解析失败(损坏/加密)",
                        "docx_error": "docx 抽取异常",
                        "xlsx_error": "xlsx 抽取异常",
                        "download_failed": "下载失败",
                        "other_extracted": "其他已抽取",
                        "other_unextracted": "其他未抽取",
                    }
                    for k, v in sorted(by_type.items()):
                        f.write("| %s | %s | %d | %d | %d | %d |\n" % (
                            k, type_label.get(k, k), v["total"], v["extracted"],
                            v["source_preserved"], v["char_total"]))
                f.write("\n### 治理维度映射\n\n")
                f.write("- **完整性**：全量扫描含附件文档，按附件清单覆盖；缓存落盘支持断点续跑。\n")
                f.write("- **有效性**：仅对 PDF 抽取；非 PDF 类型标记 `unsupported_type` 不丢元数据。\n")
                f.write("- **准确性**：逐页统计空页/低文本密度/乱码，输出质量标志供人工复核。\n")
                f.write("- **唯一性**：附件文件名即内容哈希，天然去重；另记内容 sha256。\n")
                f.write("- **可追溯**：每附件记录绝对下载 URL + 来源 docId，可回源核验。\n")
                f.write("\n### 局限性声明（数据质量边界）\n\n")
                f.write("- %s\n" % qr.get("ocr_note", "未发现需 OCR 的扫描件。"))
                f.write("- 本环境 tesseract 二进制缺失、paddleocr 模型未就绪，故图像型 PDF 暂以"
                        "`ocr_status=engine_unavailable` 标记，待部署 OCR 引擎后重跑即可补录，"
                        "不影响既有文本型附件的完整性与可用性。\n")
                rem = qr.get("remediation") or {}
                if rem:
                    f.write("\n### 整改路径（不可抽取附件，源文件均已留存）\n\n")
                    if rem.get("doc_legacy"):
                        f.write("- **旧版 .doc（%d 个）**：%s\n"
                                % (by_type.get("doc_legacy", {}).get("total", 0), rem["doc_legacy"]))
                    if rem.get("archive"):
                        f.write("- **压缩包（%d 个）**：%s\n"
                                % (by_type.get("archive", {}).get("total", 0), rem["archive"]))
                    if rem.get("pdf_corrupt"):
                        f.write("- **损坏 PDF（%d 个）**：%s\n"
                                % (by_type.get("pdf_corrupt", {}).get("total", 0), rem["pdf_corrupt"]))
                    if rem.get("xls_legacy"):
                        f.write("- **旧版 .xls（%d 个）**：%s\n"
                                % (by_type.get("xls_legacy", {}).get("total", 0), rem["xls_legacy"]))
            except Exception:
                pass
        if errors:
            f.write("\n## 失败明细\n\n")
            for e in errors:
                f.write("- docId=%s %s：%s\n" % (e["doc_id"], e["title"], e["error"]))

    print("\n完成！输出文件：")
    print("  JSON : %s" % json_path)
    print("  CSV  : %s" % csv_path)
    print("  报告 : %s" % report_path)
    print("成功 %d 篇，失败 %d 篇" % (len(records), len(errors)))
    return json_path, csv_path, report_path

def main():
    ap = argparse.ArgumentParser(description="国家金融监督管理总局 政策法规 全量抓取")
    ap.add_argument("--out-dir",
                    default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw"))
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE)
    ap.add_argument("--delay-min", type=float, default=1.5, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=3.0, help="请求最大间隔(秒)")
    ap.add_argument("--limit", type=int, default=0,
                    help="仅抓取前 N 篇（0=全部，用于快速验证）")
    ap.add_argument("--no-detail", action="store_true",
                    help="仅抓取列表、跳过详情页（用于快速验证列表遍历）")
    ap.add_argument("--cache-dir",
                    default=os.path.join(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))), "cache", "nfra"),
                    help="请求缓存目录：抓取时落盘、断网时读盘（支持断点续跑/离线复现）。"
                         "默认 regulatory_scrapers/cache/nfra（五源统一缓存根）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()
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
