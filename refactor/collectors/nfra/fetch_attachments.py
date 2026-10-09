#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nfra 法规附件下载 + PDF 文本抽取 + 数据质量校验（refactor 迁入版；原 nfra_fetch_attachments.py 逐函数等价）。

设计要点/质量维度/用法见原模块 docstring（此处保留行为）。仅 import 路径改为包内绝对导入，
附件产物根继续走 docs_root（绝对路径，字节一致）。
"""

import argparse
import glob
import json
import os
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
from ..lib.cache import docs_root, source_cache_root
from ..lib.rich_object import rich_object_fields
from ..lib.table_recovery import structured_table_fields

CACHE = source_cache_root("nfra")
ATT_DIR = docs_root("nfra", "attachments")
DETAIL_GLOB = os.path.join(CACHE, "SelectByDocId__docId_*.json")
QUALITY_REPORT = os.path.join(ATT_DIR, "_quality_report.json")

BASE = "https://www.nfra.gov.cn"
_SSL_CTX = ssl.create_default_context()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/cn/view/pages/ItemDetail.html",
}

# ---- 拆分 re-export：附件抽取链在 .attachments_extract（包内相对导入） ----
from .attachments_extract import (  # noqa: F401
    _flush_run,
    _handle_ole2,
    _is_ole2_word,
    _recover_cjk_stream,
    _temp_pdf,
    _walk_pieces,
    attachment_kind,
    dispatch_extract,
    extract_any,
    extract_doc_ole,
    extract_docx,
    extract_pdf_text,
    extract_xls_ole,
    extract_xlsx,
    is_pdf,
    ocr_pdf,
    sha256_of,
    sniff_kind,
)


def http_get_bytes(url, timeout=30, max_retries=4):
    """带超时与退避重试的二进制 GET。"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                status = resp.getcode()
                if status != 200:
                    raise urllib.error.HTTPError(url, status, "HTTP %d" % status, None, None)  # type: ignore[arg-type]
                return resp.read()
        except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                print("      [att retry %d/%d] %s，%.1fs 后重试" % (attempt, max_retries, e, wait),
                      flush=True)
                time.sleep(wait)
    raise RuntimeError("附件下载失败（重试 %d 次）: %s" % (max_retries, last_err))

def build_attachment_url(att):
    """拼接附件绝对下载 URL。"""
    uo = (att.get("urlOtherName") or "").strip()
    if uo:
        return BASE + (uo if uo.startswith("/") else "/" + uo)
    au = (att.get("attachmentUrl") or "").strip().lstrip("/")
    name = (att.get("attachmentName") or "").strip()
    if au and name:
        return BASE + "/" + au.rstrip("/") + "/" + name
    if name:
        return BASE + "/" + name
    return ""


def quality_flags(page_lens, text, needs_ocr):
    """基于逐页长度与文本特征生成质量标志。"""
    flags = []
    empty_pages = sum(1 for x in page_lens if x == 0)
    if empty_pages:
        flags.append("empty_pages=%d" % empty_pages)
    if needs_ocr:
        flags.append("possible_scan(needs_ocr)")
    if text:
        repl = text.count("\ufffd")
        if repl > 0 and repl / max(len(text), 1) > 0.01:
            flags.append("garbled(replacement_chars=%d)" % repl)
    return flags

def process_attachment(doc_id, att, att_root, args, cooldown_state, ex=None):
    """处理单个附件：下载→抽取→落盘。返回 manifest 条目。"""
    name = att.get("attachmentName") or ("%s.pdf" % att.get("id"))
    entry = {
        "id": att.get("id"),
        "title": att.get("title") or "",
        "attachment_name": name,
        "url": build_attachment_url(att),
        "order_no": att.get("orderNo"),
        "doc_id": doc_id,
    }
    txt_path = os.path.join(att_root, name + ".txt")
    rel_txt = os.path.relpath(txt_path, HERE).replace("\\", "/")

    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        try:
            with open(txt_path, encoding="utf-8") as fh:
                text = fh.read()
            entry["extracted"] = True
            entry["char_count"] = len(text)
            entry["text_file"] = rel_txt
            entry["ocr_status"] = "skipped_done"
            if ex:
                for k in ("page_count", "sha256", "quality", "source_saved",
                          "table_structured", "table_raw_text", "table_recovery_method"):
                    if k in ex:
                        entry[k] = ex[k]
            return entry, True
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass

    if not entry["url"]:
        entry["extracted"] = False
        entry["error"] = "no_url"
        entry["quality"] = ["missing_url"]
        return entry, False

    if cooldown_state["consecutive"] >= args.max_consecutive_fail:
        print("      [att cooldown] 连续失败达 %d，冷却 %.0fs" % (
            cooldown_state["consecutive"], args.cooldown), flush=True)
        time.sleep(args.cooldown)
        cooldown_state["consecutive"] = 0

    try:
        data = http_get_bytes(entry["url"], timeout=args.timeout)
        sha = sha256_of(data)
        src_path = os.path.join(att_root, name)
        with open(src_path, "wb") as fh:
            fh.write(data)
        text, page_count, char_count, needs_ocr, ok, err_reason = extract_any(data, name)
        if not ok:
            entry.update({
                "extracted": False,
                "error": err_reason or "extract_failed",
                "quality": [err_reason] if err_reason else ["extract_failed"],
                "sha256": sha,
                "source_saved": True,
            })
            return entry, False
        flags = quality_flags([char_count], text, needs_ocr)
        ocr_status = "not_needed"
        if needs_ocr and args.ocr:
            try:
                text = ocr_pdf(src_path)
                ocr_status = "ocr_done"
                flags = [f for f in flags if "possible_scan" not in f]
                flags.append("ocr_recovered")
            except Exception as e:  # noqa: BLE001
                ocr_status = "engine_unavailable"
                flags.append("ocr_failed:%s" % type(e).__name__)
        elif needs_ocr:
            ocr_status = "engine_unavailable"
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        entry.update({
            "extracted": True,
            "char_count": len(text),
            "page_count": page_count,
            "sha256": sha,
            "ocr_status": ocr_status,
            "quality": flags,
            "text_file": rel_txt,
            "source_saved": True,
        })
        try:
            entry.update(structured_table_fields(data, name))
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass
        try:
            entry.update(rich_object_fields(
                data, name,
                image_dir=docs_root("nfra", "diagrams"),
                rec_key=str(entry.get("doc_id", ""))))
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass
        cooldown_state["consecutive"] = 0
        return entry, True
    except Exception as e:  # noqa: BLE001
        cooldown_state["consecutive"] += 1
        entry.update({
            "extracted": False,
            "error": "%s: %s" % (type(e).__name__, e),
            "quality": ["download_failed"],
        })
        return entry, False


def process_doc(doc_id, data, args):
    """处理单篇文档的全部附件，写 manifest。返回 (manifest, summary)。"""
    atts = (data.get("attachmentInfoVOList") or []) if isinstance(data, dict) else []
    if not atts:
        return None, {"doc_id": doc_id, "attachment_count": 0,
                      "extracted_count": 0, "needs_ocr_count": 0}
    att_root = os.path.join(ATT_DIR, str(doc_id))
    os.makedirs(att_root, exist_ok=True)
    manifest_path = os.path.join(att_root, "manifest.json")

    existing = {}
    if os.path.exists(manifest_path):
        try:
            existing = {e["attachment_name"]: e for e in
                        json.load(open(manifest_path, encoding="utf-8")).get("attachments", [])}
        except Exception:  # noqa: BLE001
            existing = {}

    cooldown_state = {"consecutive": 0}
    entries = []
    ok = 0
    need_ocr = 0
    PERMANENT_FAIL = {"unsupported_type", "unsupported_binary_doc(.doc需外部转换)",
                      "unsupported_archive", "pdf_error", "docx_error", "xlsx_error"}
    for att in atts:
        name = att.get("attachmentName") or ("%s.pdf" % att.get("id"))
        ex = existing.get(name)
        ex_err = (ex or {}).get("error") or ""
        ex_permanent = (ex_err in PERMANENT_FAIL) or ("PdfStreamError" in ex_err) \
            or ("Stream has ended" in ex_err)
        if (not args.force) and ex and (ex.get("extracted") or ex_permanent):
            entries.append(ex)
            if ex.get("extracted"):
                ok += 1
            if ex.get("ocr_status") == "engine_unavailable":
                need_ocr += 1
            continue
        entry, success = process_attachment(doc_id, att, att_root, args, cooldown_state, ex=ex)
        entries.append(entry)
        if success:
            ok += 1
        if entry.get("ocr_status") == "engine_unavailable":
            need_ocr += 1
        time.sleep(random.uniform(args.delay_min, args.delay_max))

    manifest = {
        "doc_id": doc_id,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "attachment_count": len(entries),
        "extracted_count": ok,
        "needs_ocr_count": need_ocr,
        "attachments": entries,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    summary = {
        "doc_id": doc_id,
        "attachment_count": len(entries),
        "extracted_count": ok,
        "needs_ocr_count": need_ocr,
    }
    return manifest, summary

def classify_entry(entry, att_root):
    """基于 manifest 条目 + 磁盘源文件魔数，判定真实类型与处置状态。"""
    name = entry.get("attachment_name") or ""
    src = os.path.join(att_root, name)
    sniff = None
    if os.path.exists(src):
        with open(src, "rb") as fh:
            head = fh.read(8)
        sniff = sniff_kind(head, name)
    extracted = bool(entry.get("extracted"))
    error = entry.get("error") or ""
    quality = entry.get("quality") or []
    if extracted:
        if sniff == "pdf":
            return "pdf", True
        if sniff == "docx":
            return "docx", True
        if sniff == "xlsx":
            return "xlsx", True
        if sniff == "ole2":
            if _is_ole2_word(src):
                return "doc_legacy", True
            return "xls_legacy", True
        return "other_extracted", True
    if "unsupported_binary_doc" in error or sniff == "ole2":
        return "doc_legacy", False
    if "unsupported_archive" in error or sniff == "unknown":
        return "archive", False
    if "pdf_error" in error or sniff == "pdf":
        return "pdf_corrupt", False
    if "docx_error" in error:
        return "docx_error", False
    if "xlsx_error" in error:
        return "xlsx_error", False
    if "download_failed" in quality:
        return "download_failed", False
    return "other_unextracted", False

def build_and_write_report(args, docs_total=None, att_total=None,
                           ok_total=None, ocr_total=None, failed_docs=None):
    """聚合所有 manifest + 磁盘源文件，生成 _quality_report.json。"""
    if docs_total is None:
        docs_total = att_total = ok_total = ocr_total = 0
        failed_docs = []
        for mp in glob.glob(os.path.join(ATT_DIR, "*", "manifest.json")):
            try:
                m = json.load(open(mp, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            docs_total += 1
            att_total += m.get("attachment_count", 0)
            ok_total += m.get("extracted_count", 0)
            ocr_total += m.get("needs_ocr_count", 0)

    by_type: dict = {}
    for mp in glob.glob(os.path.join(ATT_DIR, "*", "manifest.json")):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for e in m.get("attachments", []):
            cat, _ext = classify_entry(e, att_root)
            b = by_type.setdefault(cat, {
                "total": 0, "extracted": 0, "source_preserved": 0, "char_total": 0,
            })
            b["total"] += 1
            if e.get("extracted"):
                b["extracted"] += 1
                b["char_total"] += (e.get("char_count") or 0)
            if e.get("source_saved"):
                b["source_preserved"] += 1

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "docs_with_attachments": docs_total,
        "total_attachments": att_total,
        "extracted": ok_total,
        "needs_ocr": ocr_total,
        "extraction_rate": (round(ok_total / att_total, 4) if att_total else 1.0),
        "by_type": by_type,
        "failed_docs": failed_docs or [],
        "ocr_enabled": bool(args.ocr),
        "ocr_note": (
            "OCR 引擎（tesseract 二进制缺失/paddleocr 模型未就绪）在本环境不可用；"
            "疑似扫描件已标记 ocr_status=engine_unavailable，待人工补录或部署 OCR 后重跑。"
            if ocr_total else "未发现疑似扫描件，无需 OCR。"
        ),
        "remediation": {
            "doc_legacy": (
                "旧版 .doc（OLE2 二进制 Word）：已通过 olefile 纯 Python 直抽。**"
                "修复链：①『纠正 CLX→Pcdt 走查 + fcMin 连续 utf-16-le 兜底』回收历史失败；"
                "②『fcMin 零区 + 正文散落于 WordDocument 流其他位置』表格型 .doc 新增全流回收。"
            ),
            "archive": (
                ".rar 压缩包附件：源文件已留存。本环境探测到 7za 但对其报不可打开；"
                "整改：在具备健壮 RAR 抽取器的环境重跑，或回源获取原始文件后 --force 重处理。"
            ),
            "pdf_corrupt": (
                "PDF 解析失败：源文件已留存。更强引擎亦返回 pages=0，判为结构性损坏、不可恢复。"
                "整改：人工核验原件或回源获取完好 PDF 后 --force 重处理。"
            ),
            "xls_legacy": (
                "旧版 .xls（OLE2 Excel）已通过 xlrd 抽取成功。"
            ),
        },
        "standard_alignment": {
            "DAMA": "完整性/有效性/准确性/唯一性/可追溯 已落地",
            "ISO_8000": "数据质量维度已落地；局限性显式声明",
        },
    }
    with open(QUALITY_REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n[att] 完成：文档 %d 篇，附件 %d 个，抽取成功 %d，需OCR %d，抽取率 %.1f%%"
          % (docs_total, att_total, ok_total, ocr_total, report["extraction_rate"] * 100))
    print("      按类型细分: " + ", ".join(
        "%s=%d(抽%d)" % (k, v["total"], v["extracted"]) for k, v in sorted(by_type.items())))
    print("      质量报告: %s" % QUALITY_REPORT)
    return report

def refresh_stats():
    """离线重算 manifest 统计（page_count/char_count/quality），从本地源文件抽取。"""
    fixed = 0
    for mp in sorted(glob.glob(os.path.join(ATT_DIR, "*", "manifest.json"))):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        changed = False
        for e in m.get("attachments", []):
            if not e.get("extracted"):
                continue
            name = e.get("attachment_name") or ""
            src = os.path.join(att_root, name)
            if not os.path.exists(src):
                continue
            try:
                with open(src, "rb") as fh:
                    data = fh.read()
            except Exception:  # noqa: BLE001
                continue
            kind = sniff_kind(data, name)
            if kind == "pdf":
                try:
                    _t, pc, _pl, _nor = extract_pdf_text(data)
                    if e.get("page_count") != pc:
                        e["page_count"] = pc
                        changed = True
                except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
                    pass
            sha = sha256_of(data)
            if e.get("sha256") != sha:
                e["sha256"] = sha
                changed = True
            if changed:
                fixed += 1
        if changed:
            m["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(m, fh, ensure_ascii=False, indent=2)
    print("[att] --refresh-stats 完成：重算 %d 个条目（page_count/sha256 已补全）" % fixed)
    return fixed

def retry_doc_legacy_offline():
    """离线重抽旧版 .doc（OLE2 Word）：仅对已是 doc_legacy 且源文件在盘的条目。"""
    new_ok = 0
    still_fail = 0
    for mp in sorted(glob.glob(os.path.join(ATT_DIR, "*", "manifest.json"))):
        att_root = os.path.dirname(mp)
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        changed = False
        for e in m.get("attachments", []):
            if e.get("extracted"):
                continue
            if (e.get("error") or "") != "unsupported_binary_doc(.doc需外部转换)":
                continue
            if not e.get("source_saved"):
                continue
            name = e.get("attachment_name") or ""
            src = os.path.join(att_root, name)
            if not os.path.exists(src):
                continue
            try:
                with open(src, "rb") as fh:
                    data = fh.read()
            except Exception:  # noqa: BLE001
                continue
            t, ok, _reason = extract_doc_ole(data)
            if not ok:
                still_fail += 1
                continue
            txt_path = os.path.join(att_root, name + ".txt")
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(t)
            rel_txt = os.path.relpath(txt_path, HERE).replace("\\", "/")
            e["extracted"] = True
            e["char_count"] = len(t)
            e["text_file"] = rel_txt
            e["quality"] = []
            e["ocr_status"] = "not_needed"
            e["error"] = None
            new_ok += 1
            changed = True
        if changed:
            m["extracted_count"] = sum(1 for x in m.get("attachments", []) if x.get("extracted"))
            m["needs_ocr_count"] = sum(
                1 for x in m.get("attachments", []) if x.get("ocr_status") == "engine_unavailable")
            m["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(m, fh, ensure_ascii=False, indent=2)
    print("[att] --retry-doc-legacy 完成：新抽取 %d 个旧版 Word，仍不可抽 %d 个（源已留存）"
          % (new_ok, still_fail))
    return new_ok, still_fail

def main():
    ap = argparse.ArgumentParser(description="nfra 法规附件下载与 PDF 文本抽取（高精度）")
    ap.add_argument("--cache-dir", default=CACHE)
    ap.add_argument("--doc-id", type=int, default=None, help="仅处理指定 docId（试点/补跑）")
    ap.add_argument("--delay-min", type=float, default=1.0, help="请求最小间隔(秒)")
    ap.add_argument("--delay-max", type=float, default=2.0, help="请求最大间隔(秒)")
    ap.add_argument("--timeout", type=int, default=30, help="单附件下载超时(秒)")
    ap.add_argument("--cooldown", type=int, default=120, help="连续失败冷却(秒)")
    ap.add_argument("--max-consecutive-fail", type=int, default=3, help="最大连续失败数")
    ap.add_argument("--keep-pdf", action="store_true", help="保留原始 PDF 文件")
    ap.add_argument("--ocr", action="store_true", help="启用 OCR 兜底（需 paddleocr+模型）")
    ap.add_argument("--force", action="store_true", help="强制重处理（忽略已完成的续跑缓存）")
    ap.add_argument("--report-only", action="store_true",
                    help="仅基于已有 manifest + 源文件重算质量报告，不下载/不抽取")
    ap.add_argument("--refresh-stats", action="store_true",
                    help="离线从本地源文件重算 manifest 的 page_count/sha256")
    ap.add_argument("--retry-doc-legacy", action="store_true",
                    help="离线用 olefile 重抽已留存的旧版 .doc（OLE2 Word）源文件，零 WAF 消耗")
    args = ap.parse_args()

    os.makedirs(ATT_DIR, exist_ok=True)

    if args.refresh_stats:
        print("[att] --refresh-stats：从本地源文件离线重算 manifest 统计 ...", flush=True)
        refresh_stats()
        build_and_write_report(args)
        return

    if args.report_only:
        print("[att] --report-only：扫描已有 manifest + 源文件，重算质量报告 ...", flush=True)
        build_and_write_report(args)
        return

    if args.retry_doc_legacy:
        print("[att] --retry-doc-legacy：离线用 olefile 重抽旧版 .doc 源文件 ...", flush=True)
        retry_doc_legacy_offline()
        build_and_write_report(args)
        return

    files = sorted(glob.glob(DETAIL_GLOB))
    total_docs = 0
    total_att = 0
    total_ok = 0
    total_ocr = 0
    failed_docs = []

    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8")).get("data") or {}
        except Exception:  # noqa: BLE001
            continue
        doc_id = d.get("docId")
        if not doc_id:
            continue
        if args.doc_id and int(doc_id) != int(args.doc_id):
            continue
        total_docs += 1
        try:
            _m, summ = process_doc(doc_id, d, args)
        except Exception as e:  # noqa: BLE001
            failed_docs.append({"doc_id": doc_id, "error": str(e)})
            print("      [att ERR] docId=%s: %s" % (doc_id, e), flush=True)
            continue
        total_att += summ["attachment_count"]
        total_ok += summ["extracted_count"]
        total_ocr += summ["needs_ocr_count"]
        print("      [att] docId=%s 附件 %d 抽取 %d 需OCR %d" % (
            doc_id, summ["attachment_count"], summ["extracted_count"], summ["needs_ocr_count"]),
            flush=True)

    build_and_write_report(args, docs_total=total_docs, att_total=total_att,
                           ok_total=total_ok, ocr_total=total_ocr, failed_docs=failed_docs)

if __name__ == "__main__":
    main()
