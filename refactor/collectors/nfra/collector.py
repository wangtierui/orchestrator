#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国家金融监督管理总局 —— 政策法规全量抓取脚本（refactor 迁入版）。

原 modules/regulatory_scrapers/collectors/nfra_collector.py 迁出，逻辑逐函数等价。
import 全部收口到 refactor/collectors 内部（自包含，不依赖外部 std_lib/config/modules）；
附件 local_path / 缓存 / 附件产物统一走 refactor/data 下的 docs_root / source_cache_root。
实现 SourceCollector：NfraCollector.collect(out_dir) 为被 COLLECT_CMD 调用的入口。
"""

import argparse
import json
from ..lib.cli import add_common_args, is_full_mode, make_known_pred_full
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from ..base import REPO_ROOT, SourceCollector
from ..storage import get_storage, StorageSettings, open_storage
from ..lib.pipeline import CollectorPipeline
from ..lib.text_utils import flatten_attachment_extras, html_to_text

# 附件 local_path / 下载原文 relpath 的相对基址收口到 refactor/data/raw（自包含）。
SCRAPERS_ROOT = os.path.join(REPO_ROOT, "data", "raw")

# 共享 UA 池（反爬轮换）
from ..lib.http import USER_AGENTS

import faulthandler

faulthandler.enable()


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

# 可选请求缓存（仅显式 --cache-dir 时启用，用于 WAF 规避两阶段预取）；
# 缺省实时抓取，不落盘响应（透明 HTTP 响应重放已去除）。
from ..lib.cache import OfflineMiss, bind_source_cache
from ..lib.paths import docs_root

_RESP = None  # ResponseCache 实例；None 表示未启用缓存（实时抓取）
_OfflineMiss = OfflineMiss


def _init_cache(path=None, offline=False):
    """绑定请求缓存根。

    缺省 path=None → 不启用缓存，实时抓取（去除透明 HTTP 响应重放）；
    显式 path（如预取流水线根）→ 启用缓存，可选 offline 离线只读（WAF 规避）。
    """
    global _RESP
    if path is None:
        _RESP = None
        return
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


_DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]+[〔\[]?\d{4}[〕\]]\d+号")
_SHIXING_RE = re.compile(r"自\s*(\d{4}[-年./]\d{1,2}[-月./]\d{1,2})\s*[日]?\s*起施行")

def extract_document_no(text):
    """从正文中抽取发文字号。"""
    if not text:
        return None
    from ..lib.doc_number import extract_doc_number as _u, in_abolish_context as _a, normalize_doc_number as _n
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
        "document_number": document_no,
        "effective_date": effective,
        "index_no": d.get("indexNo") or "",
        "issuing_authority": issuing,
        "category": d.get("interviewTypeName") or "",
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


class NfraCollector(CollectorPipeline):
    """国家金融监督管理总局 政策法规 抓取器；实现 SourceCollector 协议。

    主循环迁入通用 CollectorPipeline：
      - iter_list_entries：子栏目 × 分页遍历，逐条产出列表条目（dup 由 tracker 去重）；
      - handle_new：抓详情（含 title_link / offline / 失败三分支）+ 附件 + 原文下载；
      - handle_known（基类默认）：主库已有 → 直接复用旧记录（跳过详情）；
      - preserve_missing_master=True：主库并集保护（列表缺失的 doc_id 重新并入）；
      - build_extra / save：errors 规范记录 + 可选 CSV。
    """

    source_id = "nfra"
    master_filename = "nfra_regulations.json"

    # ----------------------------- CollectorPipeline 钩子 -----------------------------
    def key_of(self, item: dict):
        return item.get("doc_id")

    def _setup(self, args):
        if is_full_mode(args):
            self.known_pred = make_known_pred_full()
        # 站点级准备（缓存/opener/落盘目录），在 run() 之前完成。
        _init_cache(args.cache_dir or None, args.offline)
        self._opener = make_opener()
        self._args = args
        self._errors: list = []
        os.makedirs(args.out_dir, exist_ok=True)
        os.makedirs("logs", exist_ok=True)

    def iter_list_entries(self, ctx):
        args = self._args
        print("[1/4] 获取政策法规子栏目结构 ...", flush=True)
        children = get_child_items(self._opener)
        children.append({"item_id": PARENT_ITEM_ID, "item_name": "政策法规(本级)", "type": ""})
        print("      子栏目: " + ", ".join("%s(%s)" % (c["item_name"], c["item_id"])
                                           for c in children), flush=True)
        print("[2/4] 分页遍历各栏目列表 ...", flush=True)
        limit = getattr(args, "limit", 0) or 0
        yielded = 0
        seen = set()  # 本运行跨页去重（真正的 known/new/dup 判定交给 _collect 的 tracker）
        for c in children:
            print("      >> 开始抓取栏目 %s(%s)" % (c["item_name"], c["item_id"]), flush=True)
            try:
                rows = get_list_for_item(self._opener, c["item_id"], args.delay_min, args.delay_max)
            except Exception as e:  # noqa: BLE001
                print("      [WARN] 栏目 %s 列表抓取失败: %s" % (c["item_name"], e), flush=True)
                rows = []
            for r in rows:
                did = r.get("docId")
                if did is None:
                    continue
                if did in seen:  # 跨页重复，跳过（不计入 yield）
                    continue
                seen.add(did)
                yield {
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
                yielded += 1
                if limit and yielded >= limit:
                    return
            print("      %-22s 命中 %d 篇（累计去重 %d）" % (c["item_name"], len(rows), yielded),
                  flush=True)
            sleep_between(args.delay_min, args.delay_max)

    def handle_new(self, entry, ctx):
        args = self._args
        did = entry["doc_id"]
        rec = None
        if str(entry.get("is_title_link")) == "1" and entry.get("title_link"):
            rec = {
                "doc_id": did, "title": entry["title"], "category": entry["category"],
                "publish_date": entry["publish_date"], "build_date": "",
                "document_number": "", "effective_date": "", "index_no": "",
                "issuing_authority": "", "category": "",
                "summary": "", "content": "",
                "detail_url": entry["title_link"],
                "fetch_status": "title_link_only",
                "doc_file_url": entry["doc_file_url"], "pdf_file_url": entry["pdf_file_url"],
            }
        elif args.no_detail:
            # 仅列表模式：用列表已有字段产出 title_link_only 占位记录，
            # 不再静默丢弃（旧实现 raise skip-detail 导致 0 条入库）。
            rec = {
                "doc_id": did, "title": entry["title"], "category": entry["category"],
                "publish_date": entry["publish_date"], "build_date": "",
                "document_number": "", "effective_date": "", "index_no": "",
                "issuing_authority": "", "category": entry["category"],
                "summary": "", "content": "",
                "detail_url": build_detail_url(did, entry["item_id"]),
                "doc_file_url": entry["doc_file_url"], "pdf_file_url": entry["pdf_file_url"],
                "fetch_status": "title_link_only",
            }
        else:
            try:
                det = get_detail(self._opener, did, args.delay_min, args.delay_max)
                if det is None:
                    raise RuntimeError("详情为空")
                rec = {
                    "doc_id": did,
                    "title": det["title"] or entry["title"],
                    "category": entry["category"],
                    "publish_date": det["publish_date"] or entry["publish_date"],
                    "build_date": det["build_date"],
                    "document_number": det["document_number"] or "",
                    "effective_date": det["effective_date"] or "",
                    "index_no": det["index_no"],
                    "issuing_authority": det["issuing_authority"],
                    "category": det["category"],
                    "summary": det["summary"],
                    "content": det["content"],
                    "detail_url": build_detail_url(did, entry["item_id"]),
                    "doc_file_url": det["doc_file_url"] or entry["doc_file_url"],
                    "pdf_file_url": det["pdf_file_url"] or entry["pdf_file_url"],
                    "fetch_status": "ok",
                }
            except _OfflineMiss:
                rec = {
                    "doc_id": did, "title": entry["title"], "category": entry["category"],
                    "publish_date": entry["publish_date"], "build_date": "",
                    "document_number": "", "effective_date": "", "index_no": "",
                    "issuing_authority": "", "category": "",
                    "summary": "", "content": "",
                    "detail_url": build_detail_url(did, entry["item_id"]),
                    "fetch_status": "offline_missing",
                    "doc_file_url": entry["doc_file_url"], "pdf_file_url": entry["pdf_file_url"],
                }
                print("      [offline] docId=%s 详情未缓存，仅列表面板" % did, flush=True)
            except Exception as e:  # noqa: BLE001
                self._errors.append({"doc_id": did, "title": entry["title"], "error": str(e)})
                print("      [ERR] docId=%s 详情失败: %s" % (did, e), flush=True)
                return None
        if rec:
            if getattr(args, "download_originals", False):
                rec["downloaded_doc_path"] = download_original_doc(rec, did, SCRAPERS_ROOT)
            _atts = load_attachments(did)
            rec["attachments"] = _atts
            rec.update(flatten_attachment_extras(_atts))
        return rec

    def preserve_missing_master(self, ctx):
        return True

        # ── 治理步骤（采集边界，非采集逻辑）：错误副产物挂主库 规范记录 ──
    def build_extra(self, ctx, records):
        return {"errors": self._errors}

    def save(self, records, ctx):
        return ctx.storage.save_master(records, extra=self.build_extra(ctx, records))

    # ----------------------------- SourceCollector 协议入口 -----------------------------
    def collect(self, out_dir: str, *, limit: int = 0, mode: str = "incremental",
                no_detail: bool = False, cache_dir: str = "",
                offline: bool = False, delay: float = 1.5) -> str:
        args = argparse.Namespace(
            out_dir=out_dir,
            page_size=PAGE_SIZE,
            limit=limit,
            no_detail=no_detail,
            # 原 download_originals/no_originals/csv 已移除 CLI；保留内部读数维持默认行为
            no_originals=False,
            download_originals=True,
            csv=False,
            cache_dir=cache_dir,
            offline=offline,
            delay_min=max(0.4, delay * 0.8),
            delay_max=delay * 1.6,
        )
        self._setup(args)
        return self.run(out_dir)


def scrape(args):
    """兼容入口（同目录脚本 / CLI）：映射参数后委托 NfraCollector。"""
    return NfraCollector().collect(
        args.out_dir,
        limit=getattr(args, "limit", 0),
        mode=getattr(args, "mode", "incremental"),
        no_detail=getattr(args, "no_detail", False),
        cache_dir=getattr(args, "cache_dir", ""),
        offline=getattr(args, "offline", False),
        delay=getattr(args, "delay", 1.5),
    )


def main():
    ap = argparse.ArgumentParser(description="国家金融监督管理总局 政策法规 全量抓取")
    add_common_args(ap, with_category=False, with_attachments=False,
                    default_out=os.path.join(SCRAPERS_ROOT, "data", "raw"))
    ap.add_argument("--cache-dir", default="",
                    help="请求缓存目录（显式覆盖）")
    ap.add_argument("--offline", action="store_true",
                    help="纯离线模式：仅读取 --cache-dir 缓存，缓存缺失即跳过（不联网）")
    args = ap.parse_args()
    try:
        NfraCollector().collect(
            args.out_dir, limit=args.limit, mode=args.mode, no_detail=args.no_detail,
            cache_dir=args.cache_dir, offline=args.offline, delay=args.delay,
        )
    except Exception as e:  # 顶层兜底，避免现场丢失
        import traceback
        tb = traceback.format_exc()
        sys.stderr.write("FATAL: %s\n%s\n" % (e, tb))
        with open(os.path.join("logs", "FATAL.log"), "w", encoding="utf-8") as fh:
            fh.write("FATAL: %s\n%s\n" % (e, tb))
        raise

if __name__ == "__main__":
    main()
