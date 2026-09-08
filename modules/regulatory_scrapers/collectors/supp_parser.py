# -*- coding: utf-8 -*-
"""
supp_parser.py —— 解析/清洗模块（交付标准第三节：scripts/ 按模块拆分）

职责：正文抽取、基础清洗、断句修复、OCR 校正、表格结构还原的按职责封装。
与四源（gov/mof/nfra/pbc）的 scripts/supp_parser.py 结构一致；本项目不携带
crawler_common 模块，文档文本提取基于 pypdf 文本层 + scripts/supp_ocr_pdf.py
（扫描件 Tesseract OCR 回退），表格复用 utils/scraper_std.table_recovery。

用法示例：
    from supp_parser import parse_document
    result = parse_document(data_bytes, "scan.pdf")
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "utils")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from supp_ocr_pdf import ocr_scanned  # noqa: E402

from std_lib.scraper_std.cleaner import clean_text, is_table_block, normalize_ws  # noqa: E402
from std_lib.scraper_std.sentence_split import repair_text  # noqa: E402
from std_lib.scraper_std.table_recovery import extract_tables_from_doc  # noqa: E402


def extract_document_text(data: bytes, name: str = "", *, enable_ocr: bool = True) -> dict:
    """文档字节 → 文本（pypdf 文本层优先；扫描件回退 Tesseract OCR）。"""
    try:
        import io

        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if len(text) >= 50 and cjk >= 10:
            return {"text": text, "method": "text"}
        if enable_ocr:
            # 扫描件：写临时文件后走 ocr_pdf（TESSDATA_PREFIX 已在模块内设定）
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                text = ocr_scanned(tmp)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return {"text": text, "method": "ocr"}
        return {"text": "", "method": "none"}
    except Exception as e:  # 非 PDF 或解析失败：不阻断，返回空
        return {"text": "", "method": f"error:{type(e).__name__}"}

def parse_document(data: bytes, name: str = "", *, enable_ocr: bool = True) -> dict:
    """文档字节 → 结构化解析结果（正文 + 表格 + 基础清洗）。"""
    ex = extract_document_text(data, name, enable_ocr=enable_ocr)
    text = (ex.get("text") or "").strip()
    out = dict(ex)
    if text:
        out["text_cleaned"] = clean_text(text)
        out["repaired"] = repair_text(out["text_cleaned"], source="document")
    out["tables"] = extract_tables_from_doc(data, name)
    return out

__all__ = [
    "extract_document_text", "clean_text", "normalize_ws", "is_table_block",
    "repair_text", "extract_tables_from_doc", "parse_document",
]
