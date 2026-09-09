# -*- coding: utf-8 -*-
"""
tools/build_east_backlog.py — EAST2.0 表格类监管文件 supp 收录（工作②实战，2026-09-08）

把《关于修订人身保险公司2024版监管数据标准化规范部分数据项报送口径的通知》
（金非银检函〔2025〕82号，20250410 目录 13 文件）转为 supp backlog 记录：
  - 主通知 pdf → body_text（pypdf）
  - 附件 doc/docx → 正文文本（OLE UTF16 提取 / python-docx）
  - 附件 xlsx ×6 → **表格结构化**（openpyxl：attachment_content 文本轨 + table_structured 结构轨）
  - jpg(E-R) / et(WPS 原生) → 附件登记（注明不可解，kind/status）
输出 backlog.json 交 supp_ingest_batch.py 合入 raw → run_clean_pipeline --project supp。

用法：
  python tools/build_east_backlog.py [--dir <EAST目录>] [--out backlog.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# R4：禁盘符字面量——EAST 素材目录（绝对路径）经 --dir 命令行传入
MAX_XLSX_ROWS = 4000        # 单 sheet 行上限（防超大表爆内存）
MAX_CONTENT_CHARS = 800_000  # attachment_content 合并上限

from std_lib.scraper_std.cache_store import docs_root  # noqa: E402


def _save_er_diagram(src: str, fname: str) -> str:
    """E-R 图（jpg）落盘统一富内容根：docs_root("supp")/diagrams/EAST-20250410/<fname>。
    返回相对 docs_root("supp") 的 image_path（与 rich_object_fields 同语义）。"""
    import shutil  # noqa: PLC0415
    try:
        dest_dir = os.path.join(docs_root("supp"), "diagrams", "EAST-20250410")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fname)
        shutil.copy2(src, dest)
        return os.path.relpath(dest, docs_root("supp")).replace("\\", "/")
    except Exception as e:  # noqa: BLE001
        print(f"[east] E-R 图落盘失败 {fname}: {e}")
        return ""


def _load_extract_doc():
    """按需加载 supp_extract_doc_text.extract（Word97 OLE UTF16 过滤提取），避免导入其主流程。"""
    p = os.path.join(_ROOT, "modules", "regulatory_scrapers", "collectors", "supp_extract_doc_text.py")
    spec = importlib.util.spec_from_file_location("_ext", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.extract


def extract_pdf(path: str) -> str:
    out = []
    try:  # 轨1：pypdf
        from pypdf import PdfReader  # noqa: PLC0415
        r = PdfReader(path)
        for pg in r.pages:
            try:
                t = pg.extract_text() or ""
            except Exception:  # noqa: BLE001
                t = ""
            if t.strip():
                out.append(t)
    except Exception as e:  # noqa: BLE001
        return f"[pdf 提取失败: {e}]"
    if not out:  # 轨2：pymupdf/fitz（覆盖 pypdf 取空文本层情形）
        try:
            import pymupdf  # noqa: PLC0415
        except ImportError:  # noqa: PLC0415
            try:
                import fitz as pymupdf  # noqa: PLC0415
            except ImportError:
                pymupdf = None
        if pymupdf is not None:
            try:
                doc = pymupdf.open(path)
                for pg in doc:
                    t = pg.get_text("text") or ""
                    if t.strip():
                        out.append(t)
                doc.close()
            except Exception as e:  # noqa: BLE001
                if not out:
                    return f"[pdf pymupdf 失败: {e}]"
    return "\n".join(out).strip()


def extract_docx(path: str) -> str:
    from docx import Document  # noqa: PLC0415
    out = []
    try:
        d = Document(path)
        for para in d.paragraphs:
            if para.text.strip():
                out.append(para.text)
        for tbl in d.tables:
            for row in tbl.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                out.append("|".join(cells))
    except Exception as e:  # noqa: BLE001
        return f"[docx 提取失败: {e}]"
    return "\n".join(out).strip()


def extract_xlsx(path: str) -> tuple[str, list]:
    """返回 (attachment_content 文本, table_structured 结构)。结构：[{file, sheet, head, rows 前N}]"""
    from openpyxl import load_workbook  # noqa: PLC0415
    lines, struct = [], []
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            head = []
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                vals = [("" if v is None else str(v)).replace("\n", " ").strip() for v in row]
                if not any(vals):
                    continue
                if i == 0:
                    head = vals
                rows.append(vals)
                if i >= MAX_XLSX_ROWS:
                    break
            lines.append(f"【表:{ws.title}】")
            for rv in ([head] + rows[:200]) if head else rows[:200]:
                lines.append(" | ".join(rv))
            struct.append({"file": os.path.basename(path), "sheet": ws.title,
                           "head": head, "rows": rows[:MAX_XLSX_ROWS]})
        wb.close()
    except Exception as e:  # noqa: BLE001
        return f"[xlsx 提取失败: {e}]", []
    return "\n".join(lines).strip(), struct


def kind_of(fn: str) -> str:
    lo = fn.lower()
    if lo.endswith(".pdf"):
        return "pdf"
    if lo.endswith(".docx"):
        return "docx"
    if lo.endswith(".doc"):
        return "doc"
    if lo.endswith(".xlsx"):
        return "xlsx"
    if lo.endswith(".jpg") or lo.endswith(".jpeg") or lo.endswith(".png"):
        return "image"
    if lo.endswith(".et"):
        return "et"
    return os.path.splitext(fn)[1].lstrip(".") or "unknown"


def build(dir_path: str) -> dict:
    files = sorted(f for f in os.listdir(dir_path)
                   if os.path.isfile(os.path.join(dir_path, f)))
    if not files:
        raise SystemExit(f"目录空/不存在: {dir_path}")

    pdf_main = next((f for f in files if f.lower().endswith(".pdf") and "关于修订" in f), None)
    if not pdf_main:
        raise SystemExit("未找到主通知 pdf（含『关于修订』）")
    body = extract_pdf(os.path.join(dir_path, pdf_main))
    title = os.path.splitext(pdf_main)[0]
    if not (body or "").strip() or body.startswith("["):
        # 扫描件无文本层（本机无 OCR）：不以臆造替代原文；正文=可核验元数据声明（防 ingest 空正文拒绝）
        body = (f"【原文为 PDF 扫描件（{pdf_main}），无文本层，本机无 OCR，未做文本化】。"
                f"文件来源：本地副本 {dir_path}；金非银检函〔2025〕82号。"
                f"修订内容（报送口径）见附件：报送说明/采集技术接口(docx)、数据结构一览表/业务代码表/"
                f"数据检核规则(xlsx 表格结构化)、E-R图(jpg)、主规范与更正表(doc)。")

    attachments = []
    content_parts = []
    table_structs = []
    er_images = []
    doc_ext = _load_extract_doc() if any(f.lower().endswith(".doc") and not f.lower().endswith(".docx") for f in files) else None

    for fn in files:
        if fn == pdf_main:
            continue
        kind = kind_of(fn)
        fp = os.path.join(dir_path, fn)
        rec = {"name": fn, "kind": kind, "bytes": os.path.getsize(fp)}
        try:
            if kind == "xlsx":
                txt, ts = extract_xlsx(fp)
                rec.update({"status": "extracted", "note": "表格结构化"})
                table_structs.extend(ts)
                content_parts.append(f"### 附件 {fn}\n{txt}")
            elif kind == "docx":
                txt = extract_docx(fp)
                rec.update({"status": "extracted" if txt and not txt.startswith("[") else "failed",
                            "note": ("正文已抽取" if txt and not txt.startswith("[") else txt)})
                if txt and not txt.startswith("["):
                    content_parts.append(f"### 附件 {fn}\n{txt}")
            elif kind == "doc" and doc_ext is not None:
                with open(fp, "rb") as _fh:  # extract 契约：Word97 OLE 原始 bytes
                    txt = doc_ext(_fh.read())
                txt = (txt or "").strip()
                rec.update({"status": "extracted" if txt else "failed",
                            "note": "OLE UTF16 正文抽取" if txt else "抽取为空"})
                if txt:
                    content_parts.append(f"### 附件 {fn}\n{txt}")
            elif kind == "image":
                er_path = _save_er_diagram(fp, fn)
                rec.update({"status": "image_registered",
                            "note": "E-R图：已落盘 supp rich diagrams（图像轨）"
                                    if er_path else "E-R图登记（落盘失败）"})
                if er_path:
                    er_images.append({"index": len(er_images) + 1, "kind": "image",
                                      "caption": f"E-R图({fn})", "text": "",
                                      "image_path": er_path})
            elif kind == "et":
                rec.update({"status": "registered", "note": "WPS .et 原生格式，无开放解析器，按附件登记"})
            else:
                rec.update({"status": "registered", "note": f"未支持格式 {kind}"})
        except Exception as e:  # noqa: BLE001
            rec.update({"status": "failed", "note": f"{e!r}"[:120]})
        attachments.append(rec)

    joined = "\n\n".join(content_parts)
    if len(joined) > MAX_CONTENT_CHARS:
        joined = joined[:MAX_CONTENT_CHARS] + "\n…(截断)"
    return {
        "task_index": "EAST-20250410-01",
        "title": title,
        "doc_type": "通知",
        "category": "部门规范性文件",
        "issue_organ": "国家金融监督管理总局保险和非银机构检查局",
        "document_number": "金非银检函〔2025〕82号",
        "publish_date": "2025-04-10",
        "effective_date": "2025-04-10",
        "source": "gov.cn补充",
        "source_url": "",
        "body_text": body,
        "body_source": "downloaded_doc",
        "status": "现行有效",
        "timeliness_status": "valid",
        "verification_source": "官网原文（20250410 本地副本，金非银检函〔2025〕82号）",
        "keyword": "监管数据标准化规范/EAST/报送口径/修订",
        "summary": f"修订人身保险公司2024版监管数据标准化规范部分数据项报送口径；含附件 {len(attachments)} 件"
                   f"（主规范 doc、数据结构/代码表/检核规则 xlsx 等表格类结构化附件）。",
        "attachments": attachments,
        "attachment_count": str(len(attachments)),
        "attachment_content": joined,
        "table_structured": table_structs,
        "table_recovery_method": "openpyxl 全 sheet 结构化抽取（原始文件路径见 attachments）",
        # 富内容轨（2026-09-09）：E-R 图落盘 supp diagrams（pipeline 透传 cleaned JSONL）
        "rich_structured": er_images,
        "rich_count": len(er_images),
        "rich_text": "\n".join(f"E-R图({e.get('caption', '')})" for e in er_images),
        "_retrieval_channel": "本地文件收录（EAST 报送口径修订）",
        "_raw_fields": {"east_dir": dir_path, "collected_at": __import__("datetime").date.today().isoformat(),
                        "source_note": "附件6(.et) 为登记型；E-R 图已落盘 rich 图像轨（supp diagrams）"},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True,
                    help="EAST 素材目录（金非银检函〔2025〕82号 20250410 全文件）")
    ap.add_argument("--out", default=os.path.join(_ROOT, "tools", "_east_backlog.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rec = build(a.dir)
    print(f"[east] title={rec['title'][:50]} | body_chars={len(rec['body_text'])} | "
          f"附件={len(rec['attachments'])} | 表格结构化文件数={len(rec['table_structured'])}")
    if not a.dry_run:
        json.dump([rec], open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[east] backlog 已写: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
