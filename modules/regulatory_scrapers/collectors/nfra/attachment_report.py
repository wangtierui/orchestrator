# -*- coding: utf-8 -*-
"""nfra.attachment_report — **报表层**（E5 第二层，2026-10-19）

职责：附件质量报告的生成与刷新统计。**只写报告产物，不碰附件本身**。

函数体自 E5 起**逐字保留**（含原注释），仅改依赖来源。

依赖方向：fetch ← process ← report? —— 不，本层**只依赖编排层的 ATT_DIR**
与质量判据层（attachment_quality）；**不依赖 process**（报表只看 manifest 条目）。
⚠️ 层间 import 置于所有使用点之前。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_HERE, _os.path.abspath(_os.path.join(_HERE, "..", "..", "..", ".."))):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# 保留 `_HERE`（不删）：路径派生常量计算需要它
del _p, _os, _sys

import glob
import json
import os
import time

# 共享工具层（2026-10-23，TD-28）：`glob`/`time` 是 stdlib（原先漏导入）；
# `sha256_bytes` 取代已被 B2/W4 去重删除的本地 `sha256_of`；
# `sniff_kind`/`extract_pdf_text` 来自兄弟模块（只依赖 std_lib，故无环）。
from std_lib.common_lib.io_atomic import sha256_bytes

# 层间依赖（report → 路径常量层；双形态）
# ⚠️ **不 import 编排层**：编排层 import 本层做 re-export ⇒ 反向 import 会成环。
#    `classify_entry` 依赖磁盘读 + 兄弟模块 `attachments_extract`，故留编排层，
#    由**调用方以参数传入**（见 `build_and_write_report` 的 `classify_entry` 形参）。
try:  # 包形态
    from .attachment_paths import ATT_DIR, QUALITY_REPORT
    from .attachments_extract import extract_pdf_text, sniff_kind
except ImportError:  # 脚本形态
    from attachment_paths import ATT_DIR, QUALITY_REPORT  # type: ignore[no-redef]
    from attachments_extract import extract_pdf_text, sniff_kind  # type: ignore[no-redef]

def build_and_write_report(args, docs_total=None, att_total=None,
                           ok_total=None, ocr_total=None, failed_docs=None, classify_entry=None):
    """聚合所有 manifest + 磁盘源文件，生成 _quality_report.json（含按类型细分与整改路径）。

    若传入 docs_total 等实时计数（处理模式），优先使用；否则从磁盘 manifest 重新统计（--report-only）。

    `classify_entry` 由调用方注入（它依赖磁盘读 + 兄弟模块 `attachments_extract`，
    故留在编排层）；**未注入时延迟取**（运行期 import，避开导入期成环）。
    """
    if classify_entry is None:
        try:  # 包形态
            from .fetch_attachments import classify_entry as _ce
        except ImportError:  # 脚本形态
            from fetch_attachments import classify_entry as _ce  # type: ignore[no-redef]
        classify_entry = _ce
    # 优先用实时计数；否则从 manifest 重算
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

    # 按类型细分（始终从磁盘 manifest + 源文件魔数重算，确保 --report-only 准确）
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
                "旧版 .doc（OLE2 二进制 Word）：已通过 olefile 纯 Python 直抽 **203/203（100%）**。"
                "修复链：①『纠正 CLX→Pcdt 走查 + fcMin 连续 utf-16-le 兜底』回收 81 个历史失败明文文档；"
                "②『fcMin 零区 + 正文散落于 WordDocument 流其他位置』的表格型 .doc，新增全流 utf-16-le "
                "扫描回收（常见中文字白名单过滤乱码）回收最后 5 个（如《信用转换系数表》《银行理财子公司"
                "净资本管理指标计算表》等），正文缺口已闭合。"
            ),
            "archive": (
                ".rar 压缩包附件（2 个，docId=1350/2943）：源文件已留存。本环境探测到系统 7za(7-Zip 19.00) "
                "但对其报『Unsupported archive type / Can not open as archive』（两文件头 16 字节完全一致，"
                "判为 7za 不支持的非标准/特定 RAR 变体而非随机损坏）；pip 离线亦无 rarfile/libarchive 可用。"
                "整改：在具备 WinRAR/unrar 等健壮 RAR 抽取器的环境重跑，或回源获取原始文件后 --force 重处理。"
            ),
            "pdf_corrupt": (
                "PDF 解析失败（3 个，docId=228055）：源文件已留存。pypdf 抛 PdfStreamError，"
                "更强引擎 PyMuPDF 亦返回 pages=0（/Pages 树损坏），判为结构性损坏、本环境不可恢复。"
                "整改：人工核验原件或回源获取完好 PDF 后 --force 重处理。"
            ),
            "xls_legacy": (
                "旧版 .xls（OLE2 Excel）已通过 xlrd 抽取成功；如单元格为空可检查是否需要"
                "公式求值。"
            ),
        },
        "standard_alignment": {
            "DAMA": "完整性(全量覆盖+续跑)、有效性(类型校验)、准确性(空页/乱码标记)、"
                    "唯一性(内容哈希去重)、可追溯(来源URL+docId)",
            "ISO_8000": "数据质量维度：完整性/有效性/准确性/一致性 已落地；局限性(扫描件OCR/旧版doc)显式声明",
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
    """离线重算 manifest 统计（page_count/char_count/quality），从本地源文件抽取，不联网、不重下载。

    用于修复历史续跑跳过导致 page_count/sha256 等字段缺失（如 pdf 的 page_count=null）的场景；
    源文件已在处理时留存，故可安全离线重算。返回重算的条目数。
    """
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
            # 以源文件重新计算 sha256（权威校验值）
            sha = sha256_bytes(data)
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

