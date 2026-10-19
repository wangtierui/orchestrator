#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nfra 法规附件下载 + PDF 文本抽取 + 数据质量校验（高精度专业版）
=================================================================
背景
----
主抓取脚本 `collector.py` 仅抓取详情页 `docClob`（印发通知序言），
而大量法规（如《保险公司偿付能力监管规则（Ⅱ）》）的**实质正文挂在 PDF 附件**上
（`attachmentInfoVOList`）。本模块补齐这一缺口：把附件 PDF 下载并抽取为纯文本，
落盘为可离线复用的缓存，由主脚本在重建时并入输出。

设计要点（对齐 DAMA 数据治理 / ISO 8000 数据质量）
-------------------------------------------------
  * 完整性(Completeness)：扫描全部含附件的详情缓存，按附件清单全量覆盖；续跑跳过已处理。
  * 有效性(Validity)：仅对 PDF 抽取；非 PDF 类型标记 `unsupported_type` 不丢元数据。
  * 准确性(Accuracy)：逐页统计空页/低文本密度/乱码，输出质量标志，供人工复核。
  * 唯一性(Uniqueness)：附件文件名本身即内容哈希（如 3dc3532b....pdf），天然去重；
                         另计算内容 sha256 写入清单，便于跨文档重复识别。
  * 可追溯(Traceability)：每个附件记录绝对下载 URL + 来源 docId，可回源核验。
  * 局限性声明：图像型 PDF（扫描件）需 OCR；本环境 tesseract 二进制缺失，
                 paddleocr 若不可用则标记 `ocr_status=engine_unavailable` 并透明上报，
                 不擅自下载重型模型（避免沙箱风险），由数据质量清单显式标注待人工补录。

WAF 礼貌原则
-----------
  * 复用基础 python（含 pypdf 6.x），标准库 urllib 下载。
  * 请求随机间隔 + 连续失败冷却（与 fill_details 同纪律），不暴力重试。
  * 单篇/全量均可，缓存落盘，超时由调用方控制，下一周自动续跑。

用法
----
  python collectors/nfra/fetch_attachments.py                      # 全量（续跑）
  python collectors/nfra/fetch_attachments.py --doc-id 1027892     # 单篇试点
  python collectors/nfra/fetch_attachments.py --keep-pdf           # 保留原始 PDF（默认仅留 .txt）
  python collectors/nfra/fetch_attachments.py --ocr                # 启用 OCR 兜底（需 paddleocr+模型）
=================================================================
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 拆分（2026-09-13，P3）：文档抽取链迁 nfra/attachments_extract，以下 re-export 保持外部调用兼容 ----
# W4（L1 重组）：import 双形态（包形态相对导入 / 脚本形态裸名）；经**模块对象**取符号
# ⇒ 名单只此一份。
try:
    from . import attachments_extract as _ext
except ImportError:  # pragma: no cover - 脚本形态
    import attachments_extract as _ext  # type: ignore[no-redef]

# re-export（显式赋值，规避 F401/F405；外部调用面不变）
_flush_run = _ext._flush_run
_handle_ole2 = _ext._handle_ole2
_is_ole2_word = _ext._is_ole2_word
_recover_cjk_stream = _ext._recover_cjk_stream
_temp_pdf = _ext._temp_pdf
_walk_pieces = _ext._walk_pieces
attachment_kind = _ext.attachment_kind
dispatch_extract = _ext.dispatch_extract
extract_any = _ext.extract_any
extract_doc_ole = _ext.extract_doc_ole
extract_docx = _ext.extract_docx
extract_pdf_text = _ext.extract_pdf_text
extract_xls_ole = _ext.extract_xls_ole
extract_xlsx = _ext.extract_xlsx
is_pdf = _ext.is_pdf
ocr_pdf = _ext.ocr_pdf
sniff_kind = _ext.sniff_kind
# ⚠️ W4/L1（2026-10-15）：`sha256_of` **不是**死符号——本文件有两处活调用点（下载后与
# 校验重算）。B2 批把它从 attachments_extract 删除（与 SSOT 重复）时**未同步本文件的
# 调用点**，两形态 import 均在此 NameError（潜伏断裂，由 L1 双形态契约测试暴露）。
# 现直接绑 SSOT 实现（`sha256_bytes(data: bytes) -> str`），不再经兄弟模块中转。


def classify_entry(entry, att_root):
    """基于 manifest 条目 + 磁盘源文件魔数，判定真实类型与处置状态（用于质量报告细分）。

    返回 (category, extracted_bool)。category 取值：
      pdf / docx / xlsx / xls_legacy（OLE2 经 xlrd 抽取）
      doc_legacy（旧版 .doc 二进制，源已留存，需外部转换）
      archive（rar/未知压缩包，源已留存）
      pdf_corrupt（PDF 解析失败，源已留存）
      docx_error / xlsx_error / download_failed / other_unextracted
    """
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
            # 经抽取成功；用 olefile 判定是 Word 还是 Excel（Word→doc_legacy，Excel→xls_legacy）
            if _is_ole2_word(src):
                return "doc_legacy", True
            return "xls_legacy", True
        return "other_extracted", True
    # 未抽取
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

# ---- E5 第二层（2026-10-19）：取数/落盘/报表三层迁出 ----
#   attachment_fetch   http_get_bytes / build_attachment_url + BASE/_SSL_CTX/DEFAULT_HEADERS
#   attachment_process process_attachment / process_doc（触网+触磁盘）
#   attachment_report  build_and_write_report / refresh_stats + QUALITY_REPORT
# 依赖方向：fetch ← process ← fetch_attachments；report 只依赖本层的 ATT_DIR/classify_entry。
# 外部调用面经**显式赋值 re-export** 保持不变（P3 既有约定；规避 F401/F405 与 mypy no-redef）。
try:  # 包形态
    from . import attachment_fetch as _fetch
    from . import attachment_paths as _paths
    from . import attachment_process as _proc
    from . import attachment_report as _rep
except ImportError:  # 脚本形态
    import attachment_fetch as _fetch  # type: ignore[no-redef]
    import attachment_paths as _paths  # type: ignore[no-redef]
    import attachment_process as _proc  # type: ignore[no-redef]
    import attachment_report as _rep  # type: ignore[no-redef]

# 路径常量层（单一事实源；被三层共用 ⇒ 下沉最底层，避免成环与重复定义）
CACHE = _paths.CACHE
ATT_DIR = _paths.ATT_DIR
DETAIL_GLOB = _paths.DETAIL_GLOB
QUALITY_REPORT = _paths.QUALITY_REPORT
HERE = _paths.HERE
# 取数层
BASE = _fetch.BASE
_SSL_CTX = _fetch._SSL_CTX
DEFAULT_HEADERS = _fetch.DEFAULT_HEADERS
http_get_bytes = _fetch.http_get_bytes
build_attachment_url = _fetch.build_attachment_url
# 落盘层
process_attachment = _proc.process_attachment
process_doc = _proc.process_doc
# 报表层
build_and_write_report = _rep.build_and_write_report
refresh_stats = _rep.refresh_stats




def retry_doc_legacy_offline():
    """离线重抽旧版 .doc（OLE2 Word）：仅对已是 doc_legacy 且源文件在盘的条目，
    用 olefile 重抽；成功则写 .txt 并标记 extracted，失败（疑似加密/外语）保持源留存。

    不联网、不重下载，零 WAF 消耗（源文件已在历次处理中留存）。返回 (新抽取数, 仍失败数)。
    """
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
                # 仍不可抽（疑似加密/外语/非 Word）：保持源留存，error 不变以维持续跳
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
            # 重算文档级提取计数，保持 manifest 一致性
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
                    help="仅基于已有 manifest + 源文件重算质量报告，不下载/不抽取（避免重复消耗 WAF）")
    ap.add_argument("--refresh-stats", action="store_true",
                    help="离线从本地源文件重算 manifest 的 page_count/sha256（修复续跑跳过导致的字段缺失）")
    ap.add_argument("--retry-doc-legacy", action="store_true",
                    help="离线用 olefile 重抽已留存的旧版 .doc（OLE2 Word）源文件，零 WAF 消耗")
    args = ap.parse_args()

    os.makedirs(ATT_DIR, exist_ok=True)

    # --refresh-stats：离线从源文件重算统计（不联网、不重下载）
    if args.refresh_stats:
        print("[att] --refresh-stats：从本地源文件离线重算 manifest 统计 ...", flush=True)
        refresh_stats()
        build_and_write_report(args, classify_entry=classify_entry)
        return

    # --report-only：仅基于已有 manifest + 源文件重算质量报告，不联网、不重下载
    if args.report_only:
        print("[att] --report-only：扫描已有 manifest + 源文件，重算质量报告 ...", flush=True)
        build_and_write_report(args, classify_entry=classify_entry)
        return

    # --retry-doc-legacy：离线用 olefile 重抽已留存的旧版 .doc 源文件（零 WAF）
    if args.retry_doc_legacy:
        print("[att] --retry-doc-legacy：离线用 olefile 重抽旧版 .doc 源文件 ...", flush=True)
        retry_doc_legacy_offline()
        build_and_write_report(args, classify_entry=classify_entry)
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

    build_and_write_report(args, docs_total=total_docs, att_total=total_att, classify_entry=classify_entry,
                           ok_total=total_ok, ocr_total=total_ocr, failed_docs=failed_docs)

if __name__ == "__main__":
    main()
