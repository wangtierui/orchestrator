# -*- coding: utf-8 -*-
"""本地统一 OCR 调用模块（移植自 std_lib.scraper_std.ocr_engine，精简版）。

与上游等价：PaddleOCR(默认) → Tesseract(降级)，PDF 文本层优先 → 扫描页走引擎链，
全部失败返回 engine="none"（不向上抛异常）。差异：配置仅来自环境变量
（OCR_TESSERACT_BIN / OCR_TESSDATA_DIR / OCR_PADDLEOCR_ROOT），不再依赖外部
config.loader / ocr_correction，从而自包含。
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("refactor.collectors.lib.ocr")

_TESSDATA_DEFAULT = os.environ.get("OCR_TESSDATA_DIR", "")
_TESSERACT_DEFAULT = os.environ.get("OCR_TESSERACT_BIN", "")

ENGINE_PADDLE = "paddle"
ENGINE_TESSERACT = "tesseract"
ENGINE_TEXT_LAYER = "text_layer"
ENGINE_NONE = "none"
OCR_ENGINE_PRIORITY: list[str] = [ENGINE_PADDLE, ENGINE_TESSERACT]

_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


@dataclass
class OCRConfig:
    """本地 OCR 配置（仅环境变量 + 内置默认）。"""
    paddle_init: dict = field(default_factory=lambda: dict(
        lang="ch", ocr_version="PP-OCRv6", use_textline_orientation=True,
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        text_det_limit_side_len=1280, text_det_limit_type="max",
    ))
    tesseract_cmd: str = _TESSERACT_DEFAULT
    tessdata_prefix: str = _TESSDATA_DEFAULT
    paddleocr_root: str = os.environ.get("OCR_PADDLEOCR_ROOT", "")
    tesseract_langs: str = "chi_sim+eng"
    tesseract_config: str = "--psm 6 -c preserve_interword_spaces=0"
    dpi: int = 220
    min_text_len: int = 50
    min_cjk_for_text: int = 10
    enable_correction: bool = False  # 本地不引入 ocr_correction
    disable_mkldnn: bool = True


@dataclass
class OCRResult:
    text: str
    engine: str
    success: bool
    mode: str = "ocr"
    error: str | None = None
    attempts: list[Any] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return not self.success


@dataclass
class PDFExtractResult:
    text: str
    source: str
    engine: str
    success: bool
    page_count: int = 0
    ocr_results: list[Any] = field(default_factory=list)
    error: str | None = None


class BaseOCREngine:
    name: str = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def recognize(self, img: Any) -> str:
        raise NotImplementedError


class PaddleOCREngine(BaseOCREngine):
    name = ENGINE_PADDLE

    def __init__(self, init_params: dict, allow_download: bool = True, disable_mkldnn: bool = True):
        self._init_params = dict(init_params)
        self._allow_download = allow_download
        self._disable_mkldnn = disable_mkldnn
        self._instance = None
        self._import_error: str | None = None

    def available(self) -> bool:
        if self._import_error:
            return False
        try:
            self._apply_mkldnn_env()
            import paddleocr  # noqa: F401
            return True
        except Exception as e:  # noqa: BLE001
            self._import_error = f"import paddleocr failed: {e}"
            LOG.warning("PaddleOCR 不可用：%s", self._import_error)
            return False

    @staticmethod
    def _apply_mkldnn_env():
        os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
        os.environ["FLAGS_use_mkldnn"] = "0"

    def _ensure_instance(self):
        if self._instance is not None:
            return self._instance
        if self._disable_mkldnn:
            self._apply_mkldnn_env()
        import paddleocr
        self._instance = paddleocr.PaddleOCR(**self._init_params)
        return self._instance

    @staticmethod
    def _to_text(result: Any) -> str:
        items = result if isinstance(result, list) else [result]
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("rec_texts", "rec_text"):
                v = item.get(key)
                if isinstance(v, list):
                    lines.extend(str(x) for x in v if x)
                elif isinstance(v, str) and v:
                    lines.append(v)
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize(img: Any):
        import numpy as np
        from PIL import Image

        if isinstance(img, str) or isinstance(img, (bytes, bytearray)):
            return img
        if isinstance(img, Image.Image):
            return np.asarray(img.convert("RGB"))
        if isinstance(img, np.ndarray):
            return img
        return np.asarray(Image.open(img).convert("RGB"))

    def recognize(self, img: Any) -> str:
        eng = self._ensure_instance()
        arr = self._normalize(img)
        result = eng.predict(arr)
        return self._to_text(result)


class TesseractEngine(BaseOCREngine):
    name = ENGINE_TESSERACT

    def __init__(self, cmd: str, tessdata: str, langs: str, config: str):
        self._cmd = cmd
        self._tessdata = tessdata
        self._langs = langs
        self._config = config
        self._import_error: str | None = None

    def available(self) -> bool:
        if not os.path.exists(self._cmd):
            LOG.warning("Tesseract 可执行文件不存在：%s", self._cmd)
            return False
        if not os.path.isdir(self._tessdata):
            LOG.warning("tessdata 目录不存在：%s", self._tessdata)
            return False
        try:
            import pytesseract  # noqa: F401
            return True
        except Exception as e:  # noqa: BLE001
            self._import_error = f"import pytesseract failed: {e}"
            LOG.warning("pytesseract 不可用：%s", self._import_error)
            return False

    @staticmethod
    def _preprocess(pil_img):
        from PIL import ImageFilter, ImageOps
        gray = ImageOps.grayscale(pil_img).convert("L")
        gray = ImageOps.autocontrast(gray)
        gray = gray.filter(ImageFilter.MedianFilter(3))
        return gray

    @staticmethod
    def _normalize_to_pil(img: Any):
        import numpy as np
        from PIL import Image

        if isinstance(img, Image.Image):
            return img
        if isinstance(img, np.ndarray):
            return Image.fromarray(img)
        if isinstance(img, (str, bytes, bytearray)):
            return Image.open(img)
        return Image.open(img)

    def recognize(self, img: Any) -> str:
        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = self._cmd
        os.environ.setdefault("TESSDATA_PREFIX", self._tessdata)
        pil = self._normalize_to_pil(img)
        if not isinstance(pil, Image.Image):
            pil = Image.open(pil)
        gray = self._preprocess(pil)
        return pytesseract.image_to_string(gray, lang=self._langs, config=self._config)


class UnifiedOCR:
    def __init__(self, config: OCRConfig | None = None):
        self.config = config or OCRConfig()
        _root = (self.config.paddleocr_root or "").strip()
        if _root and os.path.isdir(os.path.join(_root, "paddleocr")):
            if _root not in sys.path:
                sys.path.insert(0, _root)
        self._engines: list[BaseOCREngine] = [
            PaddleOCREngine(self.config.paddle_init, disable_mkldnn=self.config.disable_mkldnn),
            TesseractEngine(self.config.tesseract_cmd, self.config.tessdata_prefix,
                            self.config.tesseract_langs, self.config.tesseract_config),
        ]
        self._correction_cache = None

    def health(self) -> dict:
        return {e.name: e.available() for e in self._engines}

    def warmup(self) -> OCRResult:
        import numpy as np
        blank = np.zeros((8, 8, 3), dtype="uint8")
        return self.recognize_image(blank)

    def recognize_image(self, img: Any) -> OCRResult:
        attempts: list[Any] = []
        last_err: str | None = None
        for engine in self._engines:
            if not engine.available():
                attempts.append((engine.name, False, "engine_unavailable"))
                continue
            try:
                raw = engine.recognize(img)
                text = self._post_process(raw)
                if text and text.strip():
                    attempts.append((engine.name, True, None))
                    return OCRResult(text=text, engine=engine.name, success=True, mode="ocr", attempts=attempts)
                attempts.append((engine.name, False, "empty_result"))
                last_err = f"{engine.name}: empty_result"
            except Exception as e:  # noqa: BLE001
                attempts.append((engine.name, False, str(e)))
                last_err = f"{engine.name}: {e}"
                LOG.warning("OCR 引擎 %s 失败，尝试降级：%s", engine.name, e)
        return OCRResult(text="", engine=ENGINE_NONE, success=False, mode="failed", error=last_err, attempts=attempts)

    def _post_process(self, text: str) -> str:
        if not text:
            return ""
        t = _CJK_SPACE.sub("", text)
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    def extract_pdf(self, path: str, *, force_ocr: bool = False, dpi: int | None = None) -> PDFExtractResult:
        render_dpi = dpi or self.config.dpi
        if not os.path.exists(path):
            return PDFExtractResult(text="", source="ocr", engine=ENGINE_NONE, success=False, page_count=0, error=f"file not found: {path}")
        if not force_ocr:
            layer_text = self._extract_text_layer(path)
            cjk = sum(1 for c in layer_text if "\u4e00" <= c <= "\u9fff")
            if len(layer_text) >= self.config.min_text_len and cjk >= self.config.min_cjk_for_text:
                t = self._post_process(layer_text)
                return PDFExtractResult(text=t, source=ENGINE_TEXT_LAYER, engine=ENGINE_TEXT_LAYER, success=True, page_count=self._page_count(path))
        try:
            import pymupdf as fitz
        except Exception as e:  # noqa: BLE001
            return PDFExtractResult(text="", source="ocr", engine=ENGINE_NONE, success=False, page_count=0, error=f"PyMuPDF unavailable: {e}")
        doc = fitz.open(path)
        page_texts: list[str] = []
        ocr_results: list[OCRResult] = []
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=render_dpi)
                import numpy as np
                from PIL import Image
                pil = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                arr = np.asarray(pil)
                res = self.recognize_image(arr)
                ocr_results.append(res)
                page_texts.append(res.text)
        finally:
            doc.close()
        full = "\n".join(t for t in page_texts if t).strip()
        ok = any(r.success for r in ocr_results)
        used_engine = ENGINE_NONE
        for r in ocr_results:
            if r.success:
                used_engine = r.engine
                break
        return PDFExtractResult(text=full, source="ocr", engine=used_engine, success=ok, page_count=len(ocr_results), ocr_results=ocr_results, error=None if ok else (ocr_results[0].error if ocr_results else "no pages"))

    @staticmethod
    def _page_count(path: str) -> int:
        try:
            import pymupdf as fitz
            with fitz.open(path) as d:
                return d.page_count
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _extract_text_layer(path: str) -> str:
        try:
            from pypdf import PdfReader
            parts = []
            reader = PdfReader(path)
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001
                    parts.append("")
            return "\n".join(parts).strip()
        except Exception as e:  # noqa: BLE001
            LOG.warning("pypdf 文本层提取失败：%s", e)
            return ""


_default_ocr: UnifiedOCR | None = None


def get_ocr(config: OCRConfig | None = None) -> UnifiedOCR:
    global _default_ocr
    if _default_ocr is None or config is not None:
        _default_ocr = UnifiedOCR(config)
    return _default_ocr


def recognize_image(img: Any, config: OCRConfig | None = None) -> OCRResult:
    return get_ocr(config).recognize_image(img)


def extract_pdf(path: str, config: OCRConfig | None = None, *, force_ocr: bool = False) -> PDFExtractResult:
    return get_ocr(config).extract_pdf(path, force_ocr=force_ocr)
