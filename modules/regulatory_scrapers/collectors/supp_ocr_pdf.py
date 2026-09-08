# -*- coding: utf-8 -*-
"""
supp_ocr_pdf.py —— 扫描件 PDF 文本提取模块（统一 OCR 封装的薄包装）

职责：封装 std_lib/scraper_std/ocr_engine.py 的 UnifiedOCR，对外维持历史接口：
  - extract_pdf_text(path) -> (text, mode)    # mode ∈ {"text", "ocr"}
  - ocr_scanned(path, *, dpi=220) -> str       # 强制 OCR（扫描件）

OCR 方案调用优先级（由高到低，详见 ocr_engine 模块）：
  1. PaddleOCR 3.7.0（PP-OCRv6，中文；默认引擎，优先级最高）
  2. Tesseract v5（系统 Tesseract + 工作区 tessdata，chi_sim+eng；降级引擎）

PDF 提取范式（与历史一致）：
  文本层优先（pypdf）→ 含足够中文直接采用；否则判为扫描件走 OCR 引擎链；
  OCR 失败/异常自动降级下一引擎；全部失败返回失败标记（空串），不抛未捕获异常。

依赖注入：本模块在导入时把项目根（regulatory_scrapers）加入 sys.path，再
import std_lib.scraper_std.ocr_engine；若统一模块不可用，则回退到内联
Tesseract 实现，保证 supp_parser.py 等调用方不中断。
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

_HERE = os.path.dirname(os.path.abspath(__file__))            # supplementary_regulations_scraper/scripts/
_PROJECT = os.path.dirname(_HERE)                             # supplementary_regulations_scraper/
_REPO = os.path.dirname(_PROJECT)                             # regulatory_scrapers/
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_TESSERACT_CMD = os.environ.get("OCR_TESSERACT_BIN", "")  # R17：env/config 提供，空则降级
_TESSDATA = os.path.join(_REPO, "tessdata")

# 相邻中文间误插空格清理（保留数字/英文单词间空格）
import re

_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_CJK_HALF = re.compile(
    r"(?<=[\u4e00-\u9fff])(\s+)(?=[A-Za-z0-9(（)）《》〔〕])"
    r"|(?<=[A-Za-z0-9(（)）《》〔〕])(\s+)(?=[\u4e00-\u9fff])"
)

# ---------------------------------------------------------------------------
# 统一模块接入（带防御性回退）
# ---------------------------------------------------------------------------
_UNIFIED_OK = False
_get_ocr = None
try:
    from std_lib.scraper_std.ocr_engine import get_ocr as _get_ocr  # noqa: E402
    _UNIFIED_OK = True
except Exception as _imp_err:  # pragma: no cover - 共享库缺失时的保险
    import logging
    logging.getLogger("ocr_pdf").warning("统一 OCR 模块不可用，回退内联 Tesseract：%s", _imp_err)

def _post_process(text: str) -> str:
    t = _CJK_SPACE.sub("", text)
    t = _CJK_HALF.sub(lambda m: m.group(1) or m.group(2), t)  # 保 CJK-ASCII 边界空格
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

# ---------------------------------------------------------------------------
# 对外接口（兼容 supp_parser.py / 历史调用方）
# ---------------------------------------------------------------------------
def extract_text_layer(path: str) -> str:
    """pypdf 文本层提取（纯文本层，不做 OCR）。"""
    if _UNIFIED_OK:
        return _get_ocr()._extract_text_layer(path)
    from pypdf import PdfReader
    parts = []
    reader = PdfReader(path)
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts).strip()

def ocr_scanned(path: str, *, dpi: int = 220) -> str:
    """扫描件 PDF → OCR 文本（强制走 OCR 引擎链；PaddleOCR 优先，Tesseract 降级）。

    返回整篇文本；全部引擎失败返回空串。
    """
    if _UNIFIED_OK:
        res = _get_ocr().extract_pdf(path, force_ocr=True, dpi=dpi)
        return res.text
    # 回退：内联 Tesseract（与历史实现一致）
    return _tesseract_fallback(path, dpi=dpi)

def extract_pdf_text(path: str) -> tuple[str, str]:
    """PDF → (text, mode)；mode ∈ {"text", "ocr"}。

    文本层含足够中文（≥50 字符且 CJK ≥10）→ 直接采用 mode="text"；
    否则判为扫描件走 OCR 引擎链 → mode="ocr"。
    """
    if _UNIFIED_OK:
        res = _get_ocr().extract_pdf(path)
        mode = "text" if res.source == "text_layer" else "ocr"
        return res.text, mode
    # 回退：文本层优先 + Tesseract
    text = extract_text_layer(path)
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if len(text) >= 50 and cjk >= 10:
        return _post_process(text), "text"
    return _tesseract_fallback(path), "ocr"

# ---------------------------------------------------------------------------
# 内联 Tesseract 回退（仅当统一模块不可用时启用）
# ---------------------------------------------------------------------------
def _tesseract_fallback(path: str, *, dpi: int = 220) -> str:
    import fitz
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps

    os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA)
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
    parts: list[str] = []
    doc = fitz.open(path)
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            gray = ImageOps.grayscale(img).convert("L")
            gray = ImageOps.autocontrast(gray)
            gray = gray.filter(ImageFilter.MedianFilter(3))
            cfg = "--psm 6 -c preserve_interword_spaces=0"
            t = pytesseract.image_to_string(gray, lang="chi_sim+eng", config=cfg)
            parts.append(_post_process(t))
    finally:
        doc.close()
    return "\n".join(parts).strip()

if __name__ == "__main__":  # 命令行自测
    if len(sys.argv) < 2:
        print("用法: python supp_ocr_pdf.py <pdf路径> [--force-ocr]")
        sys.exit(1)
    force = "--force-ocr" in sys.argv
    path = sys.argv[1]
    if force:
        text, mode = ocr_scanned(path), "ocr"
    else:
        text, mode = extract_pdf_text(path)
    print(f"[mode={mode}][unified={_UNIFIED_OK}] 提取字符数: {len(text)}")
    print(text[:500])
