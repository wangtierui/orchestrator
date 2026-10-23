# -*- coding: utf-8 -*-
"""nfra.attachment_process — **落盘层**（E5 第二层，2026-10-19）

职责：单附件下载→抽取→落盘，以及单篇文档的全附件处理。
**触网与触磁盘都发生在本层**，但职责单一：把"拿到的字节"变成
"落盘的文本 + manifest 条目"。

函数体自 E5 起**逐字保留**（含原注释），仅改依赖来源。

依赖方向：fetch ← **process** ← fetch_attachments
  · 依赖 fetch 层的两个取数函数（`http_get_bytes` / `build_attachment_url`）；
  · `ATT_DIR` 由**编排层**提供（被 process/report/编排三层共用，
    留编排层避免"各复制一份必漏改其一"）。
  ⚠️ 层间 import 置于**所有使用点之前**（常量在导入期求值——E3/E4 的教训）。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：路径派生常量计算需要它
del _p, _os, _sys

import json
import os
import random
import time

# 共享工具层（2026-10-23，TD-28）：这些名字原先由**单体模块**定义在被拆出去的那一半，
# 拆分后调用点全在这里、定义全在别处 ⇒ 运行时 `NameError`（只在真正处理附件时才炸，
# 所以两年没被发现是"运气"，不是"没问题"）。
# 规范来源（去重后的唯一实现，不要再本地复制）：
#   · `sha256_bytes`  ← std_lib.common_lib.io_atomic（原 `sha256_of` 已按 B2/W4 去重）
#   · `docs_root`     ← std_lib.scraper_std.cache_store（附件/图表根目录的唯一口径）
#   · `structured_table_fields` / `rich_object_fields` ← std_lib.scraper_std（与 mof/pbc 同源）
from std_lib.common_lib.io_atomic import sha256_bytes
from std_lib.scraper_std.cache_store import docs_root
from std_lib.scraper_std.rich_object import rich_object_fields
from std_lib.scraper_std.table_recovery import structured_table_fields

# 层间依赖（process → fetch 的取数函数 + 编排层的 ATT_DIR；双形态）
try:  # 包形态
    from .attachment_fetch import build_attachment_url, http_get_bytes
    from .attachment_paths import ATT_DIR, HERE
    from .attachment_quality import quality_flags
    from .attachments_extract import extract_any, ocr_pdf
except ImportError:  # 脚本形态
    from attachment_fetch import build_attachment_url, http_get_bytes  # type: ignore[no-redef]
    from attachment_paths import ATT_DIR, HERE  # type: ignore[no-redef]
    from attachment_quality import quality_flags  # type: ignore[no-redef]
    from attachments_extract import extract_any, ocr_pdf  # type: ignore[no-redef]

def process_attachment(doc_id, att, att_root, args, cooldown_state, ex=None):
    """处理单个附件：下载→抽取→落盘。返回 manifest 条目。

    ex 为已有 manifest 中同名的条目（续跑时传入），用于恢复 page_count/sha256/
    quality/source_saved 等关键统计，避免续跳导致这些字段丢失（如 page_count=null）。
    """
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

    # 已完成（已抽取文本文件存在且非空）→ 续跑跳过
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 0:
        try:
            with open(txt_path, encoding="utf-8") as fh:
                text = fh.read()
            entry["extracted"] = True
            entry["char_count"] = len(text)
            entry["text_file"] = rel_txt
            entry["ocr_status"] = "skipped_done"
            # 从既有 manifest 条目恢复关键统计字段（page_count/sha256/quality/source_saved + 表格键）
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

    # 连续失败冷却
    if cooldown_state["consecutive"] >= args.max_consecutive_fail:
        print("      [att cooldown] 连续失败达 %d，冷却 %.0fs" % (
            cooldown_state["consecutive"], args.cooldown), flush=True)
        time.sleep(args.cooldown)
        cooldown_state["consecutive"] = 0

    try:
        data = http_get_bytes(entry["url"], timeout=args.timeout)
        sha = sha256_bytes(data)
        # 始终留存源文件（权威原件，保障可追溯/后续外部转换），不依赖 --keep-pdf
        src_path = os.path.join(att_root, name)
        with open(src_path, "wb") as fh:
            fh.write(data)
        text, page_count, char_count, needs_ocr, ok, err_reason = extract_any(data, name)
        if not ok:
            # 无法抽取（.doc 旧版 Word / .rar 等）：源已留存，透明标记，不写文本
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
            ocr_status = "engine_unavailable"  # 未启用 OCR 或引擎不可用 → 透明标记
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
        # 表格结构化（2026-09-08 仿 supp 打通）：xlsx/docx 附件表 → manifest entry 表键
        try:
            entry.update(structured_table_fields(data, name))
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass  # 表格抽取失败不影响文本/落盘/续跑
        # 富内容轨（2026-09-09 rich_object）：docx/xlsx 图形/公式/图片
        try:
            entry.update(rich_object_fields(
                data, name,
                image_dir=docs_root("nfra", "diagrams"),
                rec_key=str(entry.get("doc_id", ""))))
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
            pass  # 富内容失败不影响文本/落盘/续跑
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

    # 续跑：读取已有 manifest，仅处理缺失项
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
    # 永久失败类型：扩展名/格式无法用现有手段抽取（.doc 旧版 Word / .rar 等），
    # 源文件已留存，续跑不再重复下载。--force 可强制重处理（如部署转换引擎后）。
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


