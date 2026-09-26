# -*- coding: utf-8 -*-
"""
ocr_engine.py —— 统一 OCR 调用模块（监管爬虫工程共享库）

设计目标
--------
为各爬虫项目（gov/mof/nfra/pbc/supplementary）提供**单一、可配置、可降级**的
OCR 调用入口，屏蔽底层引擎差异。

OCR 方案调用优先级顺序（由高到低）
----------------------------------
  1. PaddleOCR 3.7.0  （PP-OCRv6，中文 PP-OCRv6_medium；默认引擎，优先级最高）
  2. Tesseract v5      （系统 Tesseract + 工作区 tessdata，chi_sim+eng；降级引擎）

PDF 提取的额外前置步骤（非 OCR 引擎，仅 PDF 场景）：
  * 文本层优先（text layer）：pypdf 提取，含足够中文即直接采用，避免对正常
    文本做 OCR。仅当文本层不足（扫描件）时才进入上面的 OCR 引擎优先级链。

降级策略（识别失败 / 异常）
---------------------------
  * 按优先级依次尝试引擎；某引擎抛异常或返回空，自动切换下一个引擎；
  * 全部引擎均失败 → 返回**失败标记**（OCRResult.success=False, engine="none",
    text=""），**不向上抛未捕获异常**，由调用方按 marker 决策（留空 / 记日志 /
    走人工核验）；
  * 图片 OCR 与 PDF 扫描页 OCR 共用同一套引擎优先级与降级逻辑。

初始化流程
----------
  * UnifiedOCR 构造**不立即加载重型引擎**（懒加载），避免无谓开销；
  * 首次调用对应引擎时才真正实例化（PaddleOCR 首调用会按需下载模型权重，
    需要联网；下载/加载失败则该引擎标记为不可用并降级）；
  * 可选 .warmup() 预加载默认引擎（触发模型下载/加载），用于启动时探活；
  * .health() 返回各引擎可用性快照。

可配置项（OCRConfig）
-------------------
  paddle_init        PaddleOCR 构造参数（lang / ocr_version / use_textline_orientation ...；注意 3.x 已移除 show_log）
  disable_mkldnn     是否禁用 oneDNN（默认 True，规避 Paddle#77340 CPU 推理崩溃；详见 §十三·7）
  tesseract_cmd      Tesseract 可执行文件路径
  tessdata_prefix     tessdata 目录（含 chi_sim / eng）
  tesseract_langs    Tesseract 语言包组合，默认 "chi_sim+eng"
  dpi                PDF 渲染 DPI，默认 220
  enable_correction  是否启用 ocr_correction 后处理（字形校正/词典校验/噪声过滤），默认 True
  correction_*       ocr_correction 参数（混淆映射 / 自定义词典 / 高频词）
  min_cjk_for_text   文本层判定为"足够中文"的 CJK 字数阈值，默认 10

依赖：本模块**顶部不导入**任何重型 OCR 依赖（paddleocr / pytesseract / fitz 均为
懒加载），确保即便某些引擎未安装，模块仍可 import，由 availability 探测决定启用。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("scraper_std.ocr_engine")

# ---------------------------------------------------------------------------
# 路径常量（与 supplementary/scripts/ocr_pdf.py 保持一致的寻址规则）
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))  # std_lib/scraper_std/
_REPO = os.path.dirname(os.path.dirname(_HERE))  # orchestrator 仓库根（dirname×2）
# P1 去硬编码（R17）：Tesseract 路径不再默认写死安装目录。
# 取值优先级：环境变量 OCR_TESSERACT_BIN > config/ocr.yaml（由 orchestrator cli 注入环境）> 空。
# 空路径时 TesseractEngine.available() 判定不存在并降级到 paddle/文本层，不影响无 Tesseract 环境。
_TESSDATA_DEFAULT = os.environ.get("OCR_TESSDATA_DIR") or os.path.join(_REPO, "tessdata")
_TESSERACT_DEFAULT = os.environ.get("OCR_TESSERACT_BIN", "")

# OCR 引擎优先级（索引越小优先级越高）
ENGINE_PADDLE = "paddle"
ENGINE_TESSERACT = "tesseract"
ENGINE_TEXT_LAYER = "text_layer"
ENGINE_NONE = "none"
OCR_ENGINE_PRIORITY: list[str] = [ENGINE_PADDLE, ENGINE_TESSERACT]

# 相邻中文间误插空格清理（OCR 常见噪声，Paddle/Tesseract 均可能引入）
_CJK_SPACE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class OCRConfig:
    """统一 OCR 模块的可配置项。"""

    # PaddleOCR 构造参数（详见 paddleocr.PaddleOCR.__init__；注意 3.x 已移除 show_log）
    paddle_init: dict = field(
        default_factory=lambda: dict(
            lang="ch",
            ocr_version="PP-OCRv6",
            use_textline_orientation=True,
            # 2026-09-12 速度优化：制度扫描件均为正立文档——关闭整页方向分类（doc_ori）与
            # 去畸变（UVDoc）子管线；检测输入长边限 1280（200DPI 渲染下实测 23s/页，
            # 由 144s 降 6 倍且识别质量不变——"PP-OCRv6 mobile"档不存在，勿指定）。
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            text_det_limit_side_len=1280,
            text_det_limit_type="max",
        )
    )
    # Tesseract 配置
    tesseract_cmd: str = _TESSERACT_DEFAULT
    tessdata_prefix: str = _TESSDATA_DEFAULT
    # PaddleOCR 源码根（2026-09-12：R17 布局预期 `external/PaddleOCR-3.7.0`；存在时注入
    # sys.path 供 `import paddleocr` 从源码加载——后端 paddlepaddle/paddlex 由 pip 提供）
    paddleocr_root: str = ""
    tesseract_langs: str = "chi_sim+eng"
    tesseract_config: str = "--psm 6 -c preserve_interword_spaces=0"
    # PDF 渲染
    dpi: int = 220
    # 文本层充分性判定
    min_text_len: int = 50
    min_cjk_for_text: int = 10
    # 后处理（ocr_correction）
    enable_correction: bool = True
    correction_confusion_map: dict | None = None
    correction_dict_path: str | None = None
    correction_high_freq: dict | None = None
    correction_uncertain_dir: str | None = None
    # 是否允许 PaddleOCR 在初始化/首次推理时联网下载模型（设为 False 可禁用首调用下载）
    allow_model_download: bool = True
    # 是否禁用 oneDNN/MKLDNN。PaddlePaddle 3.3.x 在 CPU 上默认启用 oneDNN 时，PIR 执行器
    # 会抛 NotImplementedError: ConvertPirAttribute2RuntimeAttribute ... (onednn_instruction.cc)，
    # 此乃 Paddle#77340 已知 bug。默认关闭 oneDNN（识别精度不变，仅放弃 MKLDNN 加速），
    # 以保证 CPU 推理可用；若后续升级到已修复的 paddlepaddle 版本，可设为 False 恢复加速。
    disable_mkldnn: bool = True

    @classmethod
    def from_config(cls) -> OCRConfig:
        """从 config/ocr.yaml（经 config.loader，唯一读取口）构造配置（2026-09-12 接通）。

        原实现：`OCRConfig()` 仅取环境变量默认值（_TESSERACT_DEFAULT 等），与
        config/ocr.yaml 脱节 → R17 声称的"yaml 化"实际未接通（tesseract_bin 永不生效）。
        取值优先级（保持 R17 语义）：环境变量 > ocr.yaml > 内置默认。
        """
        kwargs: dict = {}
        try:
            import sys as _sys  # noqa: PLC0415

            _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from config.loader import load_ocr  # noqa: PLC0415

            ocr = (load_ocr() or {}).get("ocr") or {}
            if ocr.get("tesseract_bin"):
                kwargs["tesseract_cmd"] = str(ocr["tesseract_bin"])
            if ocr.get("tessdata_dir"):
                kwargs["tessdata_prefix"] = str(ocr["tessdata_dir"])
            if ocr.get("paddleocr_root"):
                kwargs["paddleocr_root"] = str(ocr["paddleocr_root"])
            if ocr.get("render_dpi"):
                kwargs["dpi"] = int(ocr["render_dpi"])
        except Exception:  # noqa: BLE001  配置不可用（独立调用/离线场景）→ 内置默认
            pass
        if os.environ.get("OCR_TESSERACT_BIN"):
            kwargs["tesseract_cmd"] = os.environ["OCR_TESSERACT_BIN"]
        if os.environ.get("OCR_TESSDATA_DIR"):
            kwargs["tessdata_prefix"] = os.environ["OCR_TESSDATA_DIR"]
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------
@dataclass
class OCRResult:
    """单张图片 / 单页 OCR 结果。

    engine: 实际生效引擎名（paddle / tesseract / none）
    success: 是否成功识别到文本
    mode: "ocr"（走了 OCR 引擎）/ "failed"（全部引擎失败）
    attempts: 各引擎尝试记录 [(engine, ok, error_or_None), ...]
    """

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
    """PDF 文本提取结果。

    source: "text_layer"（文本层优先命中，未走 OCR）/ "ocr"（走了 OCR 引擎链）
    engine: 实际使用的 OCR 引擎（source=="ocr" 时有效；否则 "text_layer"/"none"）
    """

    text: str
    source: str
    engine: str
    success: bool
    page_count: int = 0
    ocr_results: list[OCRResult] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# 引擎抽象
# ---------------------------------------------------------------------------
class BaseOCREngine:
    name: str = "base"

    def available(self) -> bool:
        """引擎依赖是否可导入 / 可执行文件是否存在。"""
        raise NotImplementedError

    def recognize(self, img: Any) -> str:
        """对单张图片（PIL.Image / np.ndarray / 路径 str / bytes）做 OCR，返回文本。"""
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
            # 必须在首次 import paddle（paddleocr 内部导入）之前设置 oneDNN 开关，
            # 否则 paddlex.utils.flags 会在 import 时就把 ENABLE_MKLDNN_BYDEFAULT
            # 缓存为默认值 True，后续设置无效。
            self._apply_mkldnn_env()
            import paddleocr  # noqa: F401

            return True
        except Exception as e:  # pragma: no cover - 依赖缺失
            self._import_error = f"import paddleocr failed: {e}"
            LOG.warning("PaddleOCR 不可用：%s", self._import_error)
            return False

    @staticmethod
    def _apply_mkldnn_env():
        """关闭 oneDNN/MKLDNN：规避 PaddlePaddle 3.3.x CPU 上 PIR × oneDNN 已知 bug
        （Paddle#77340，报 NotImplementedError: ConvertPirAttribute2RuntimeAttribute ...）。
        须在 import paddle 之前调用，使 PaddleX 在构造管线时读到 run_mode='paddle' 默认。"""
        os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
        os.environ["FLAGS_use_mkldnn"] = "0"

    def _ensure_instance(self):
        if self._instance is not None:
            return self._instance
        if not self._allow_download:
            # 禁止联网下载时，若本地无模型会导致首次推理失败；交由调用方降级
            pass
        # 关闭 oneDNN/MKLDNN（必须在 import paddle 之前设置，理由见 _apply_mkldnn_env）
        if self._disable_mkldnn:
            self._apply_mkldnn_env()
        import paddleocr

        self._instance = paddleocr.PaddleOCR(**self._init_params)
        return self._instance

    @staticmethod
    def _to_text(result: Any) -> str:
        """将 PaddleOCR predict 结果（list[dict]）折叠为纯文本。"""
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
        """将输入归一化为 PaddleOCR 接受的 np.ndarray（RGB）。"""
        import numpy as np
        from PIL import Image

        if isinstance(img, str) or isinstance(img, (bytes, bytearray)):
            return img
        if isinstance(img, Image.Image):
            arr = np.asarray(img.convert("RGB"))
            return arr
        if isinstance(img, np.ndarray):
            return img
        # 兜底：尝试转 PIL
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
        except Exception as e:  # pragma: no cover
            self._import_error = f"import pytesseract failed: {e}"
            LOG.warning("pytesseract 不可用：%s", self._import_error)
            return False

    @staticmethod
    def _preprocess(pil_img):
        """灰度 + 自动对比度 + 中值滤波（提升扫描件识别率）。"""
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


# ---------------------------------------------------------------------------
# 统一门面
# ---------------------------------------------------------------------------
class UnifiedOCR:
    """统一 OCR 门面：按优先级串联引擎，提供图片 / PDF 识别与降级。"""

    def __init__(self, config: OCRConfig | None = None):
        self.config = config or OCRConfig.from_config()
        # PaddleOCR 源码布局接入（2026-09-12）：R17 预期布局 external/PaddleOCR-3.7.0
        # 注入 sys.path（绕过 editable 安装；paddlepaddle/paddlex 等依赖由 pip 提供）。
        _root = (self.config.paddleocr_root or "").strip()
        if _root and os.path.isdir(os.path.join(_root, "paddleocr")):
            if _root not in sys.path:
                sys.path.insert(0, _root)
        self._engines: list[BaseOCREngine] = [
            PaddleOCREngine(
                self.config.paddle_init,
                allow_download=self.config.allow_model_download,
                disable_mkldnn=self.config.disable_mkldnn,
            ),
            TesseractEngine(
                self.config.tesseract_cmd,
                self.config.tessdata_prefix,
                self.config.tesseract_langs,
                self.config.tesseract_config,
            ),
        ]
        # 仅保留 available 的引擎进入优先级链（available 在调用时再探测，这里不提前过滤，
        # 以便运行时依赖就绪后可自动启用）；提供 health() 供探活。
        self._correction_cache = None

    # -- 引擎可用性 --------------------------------------------------------
    def health(self) -> dict:
        """返回各引擎可用性快照。"""
        return {e.name: e.available() for e in self._engines}

    def warmup(self) -> OCRResult:
        """预加载默认（最高优先级）引擎；用 1x1 空白图触发模型下载/加载。"""
        import numpy as np

        blank = np.zeros((8, 8, 3), dtype=np.uint8)
        return self.recognize_image(blank)

    # -- 图片 OCR -----------------------------------------------------------
    def recognize_image(self, img: Any) -> OCRResult:
        """对单张图片按优先级做 OCR，返回 OCRResult（含降级记录）。"""
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
                    return OCRResult(
                        text=text,
                        engine=engine.name,
                        success=True,
                        mode="ocr",
                        attempts=attempts,
                    )
                # 引擎返回空：视作该引擎未识别到，尝试下一引擎
                attempts.append((engine.name, False, "empty_result"))
                last_err = f"{engine.name}: empty_result"
            except Exception as e:  # 异常 → 降级下一引擎
                attempts.append((engine.name, False, str(e)))
                last_err = f"{engine.name}: {e}"
                LOG.warning("OCR 引擎 %s 失败，尝试降级：%s", engine.name, e)
        # 全部失败 → 失败标记
        return OCRResult(
            text="",
            engine=ENGINE_NONE,
            success=False,
            mode="failed",
            error=last_err,
            attempts=attempts,
        )

    # -- 后处理 -------------------------------------------------------------
    def _post_process(self, text: str) -> str:
        if not text:
            return ""
        t = _CJK_SPACE.sub("", text)
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        if self.config.enable_correction:
            t = self._correct(t)
        return t

    def _correct(self, text: str) -> str:
        try:
            from .ocr_correction import correct_ocr_text
        except Exception as e:  # pragma: no cover
            LOG.warning("ocr_correction 不可用：%s", e)
            return text
        try:
            r = correct_ocr_text(
                text,
                confusion_map=self.config.correction_confusion_map,
                dict_path=self.config.correction_dict_path,
                high_freq=self.config.correction_high_freq,
                uncertain_export_dir=self.config.correction_uncertain_dir,
            )
            return r.get("text", text)
        except Exception as e:  # pragma: no cover
            LOG.warning("OCR 后处理异常，返回原文：%s", e)
            return text

    # -- PDF 提取 -----------------------------------------------------------
    def extract_pdf(
        self, path: str, *, force_ocr: bool = False, dpi: int | None = None
    ) -> PDFExtractResult:
        """PDF 文本提取：文本层优先 → 扫描页走 OCR 引擎链。

        返回 PDFExtractResult；source="text_layer" 表示直接采用文本层（未走 OCR），
        source="ocr" 表示走了 OCR 引擎链（engine 记录实际生效引擎或 none）。
        dpi: 可选覆盖渲染 DPI（不影响已缓存的 PaddleOCR 实例）。
        """
        render_dpi = dpi or self.config.dpi
        if not os.path.exists(path):
            return PDFExtractResult(
                text="",
                source="ocr",
                engine=ENGINE_NONE,
                success=False,
                page_count=0,
                error=f"file not found: {path}",
            )

        # 1) 文本层优先
        if not force_ocr:
            layer_text = self._extract_text_layer(path)
            cjk = sum(1 for c in layer_text if "\u4e00" <= c <= "\u9fff")
            if len(layer_text) >= self.config.min_text_len and cjk >= self.config.min_cjk_for_text:
                t = self._post_process(layer_text)
                return PDFExtractResult(
                    text=t,
                    source=ENGINE_TEXT_LAYER,
                    engine=ENGINE_TEXT_LAYER,
                    success=True,
                    page_count=self._page_count(path),
                )

        # 2) 扫描件 → 逐页 OCR（引擎链 + 降级）
        try:
            import pymupdf as fitz
        except Exception as e:
            return PDFExtractResult(
                text="",
                source="ocr",
                engine=ENGINE_NONE,
                success=False,
                page_count=0,
                error=f"PyMuPDF unavailable: {e}",
            )
        doc = fitz.open(path)
        page_texts: list[str] = []
        ocr_results: list[OCRResult] = []
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=render_dpi)
                import numpy as np
                from PIL import Image

                pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                arr = np.asarray(pil)
                res = self.recognize_image(arr)
                ocr_results.append(res)
                page_texts.append(res.text)
        finally:
            doc.close()

        full = "\n".join(t for t in page_texts if t).strip()
        ok = any(r.success for r in ocr_results)
        # 取首个成功引擎名（或全部失败时 none）
        used_engine = ENGINE_NONE
        for r in ocr_results:
            if r.success:
                used_engine = r.engine
                break
        return PDFExtractResult(
            text=full,
            source="ocr",
            engine=used_engine,
            success=ok,
            page_count=len(ocr_results),
            ocr_results=ocr_results,
            error=None if ok else (ocr_results[0].error if ocr_results else "no pages"),
        )

    # -- PDF 辅助 -----------------------------------------------------------
    @staticmethod
    def _page_count(path: str) -> int:
        try:
            import pymupdf as fitz

            with fitz.open(path) as d:
                return d.page_count
        except Exception:
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
                except Exception:
                    parts.append("")
            return "\n".join(parts).strip()
        except Exception as e:
            LOG.warning("pypdf 文本层提取失败：%s", e)
            return ""


# ---------------------------------------------------------------------------
# 便捷单例
# ---------------------------------------------------------------------------
_default_ocr: UnifiedOCR | None = None


def get_ocr(config: OCRConfig | None = None) -> UnifiedOCR:
    """获取（惰性创建）进程内共享的 UnifiedOCR 单例，复用 PaddleOCR 实例。"""
    global _default_ocr
    if _default_ocr is None or config is not None:
        _default_ocr = UnifiedOCR(config)
    return _default_ocr


def recognize_image(img: Any, config: OCRConfig | None = None) -> OCRResult:
    """模块级便捷函数：对单张图片做 OCR。"""
    return get_ocr(config).recognize_image(img)


def extract_pdf(
    path: str, config: OCRConfig | None = None, *, force_ocr: bool = False
) -> PDFExtractResult:
    """模块级便捷函数：PDF 文本提取（文本层优先 + OCR 引擎链降级）。"""
    return get_ocr(config).extract_pdf(path, force_ocr=force_ocr)


if __name__ == "__main__":  # 离线自检
    cfg = OCRConfig()
    ocr = UnifiedOCR(cfg)
    print("[health]", ocr.health())
    # 用极小空白图触发引擎构造（PaddleOCR 可能联网下载模型；失败则降级）
    r = ocr.recognize_image(__import__("numpy").zeros((8, 8, 3), dtype="uint8"))
    print(f"[recognize_image] engine={r.engine} success={r.success} attempts={r.attempts}")
    print("[ocr_engine] 自检完成")
