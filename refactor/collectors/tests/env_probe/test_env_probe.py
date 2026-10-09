# -*- coding: utf-8 -*-
"""refactor 环境验证探针：探测运行/可选依赖缺口。

语义：
- 核心依赖缺失 → 由导入类用例（test_imports / test_lib_modules_importable）覆盖，
  缺失时直接 ImportError 失败（refactor 必需，缺失即缺陷）。
- OCR 引擎缺失 → 默认 SKIP（非必需，优雅降级为 needs_ocr）；设 REFACTOR_REQUIRE_OCR=1 升级为必检 FAIL。
- 始终打印一份可读环境报告（运行加 -s 可见；skip/fail 时摘要行也会显示原因）。

运行：  cd refactor && python -m pytest collectors/tests/env_probe -s -q
        （自包含：从 refactor/ 或仓库根均可运行，不依赖 refactor. 前缀）
"""
import importlib.util
import logging
import os
import shutil
import threading

import pytest

# 自包含导入前缀：随测试被收集的包名走（collectors.* 或 refactor.collectors.*）
_PKG = ".".join(__name__.split(".")[:-3])  # 去掉 .tests.env_probe.<module>

# ---- 核心运行时依赖（refactor 必需，缺失即视为环境缺陷）----
CORE_DEPS = {
    "requests": "requests",
    "beautifulsoup4": "bs4",
    "lxml": "lxml",
    "pypdf": "pypdf",
    "python-docx": "docx",
    "openpyxl": "openpyxl",
}

# ---- OCR 可选栈（缺失时优雅降级；但缺任一项都会让对应引擎不可用）----
OCR_PY_DEPS = {
    "numpy": "numpy",        # Paddle / Tesseract 图像处理都需要
    "Pillow": "PIL",         # 同上
    "PyMuPDF": "pymupdf",    # OCR 路径 PDF 栅格化（转图片）必需
    "pytesseract": "pytesseract",  # Tesseract 的 Python 封装
    "paddleocr": "paddleocr",      # PaddleOCR 引擎
}


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _probe_ocr_py():
    return {pkg: _have(mod) for pkg, mod in OCR_PY_DEPS.items()}


def _probe_ocr_bin():
    return {"tesseract": shutil.which("tesseract")}


def _format_report(ocr_py, ocr_bin, health):
    out = []
    out.append("=" * 64)
    out.append("refactor 环境验证报告")
    out.append("=" * 64)
    out.append("[核心依赖] 必需（缺失 = 缺陷）")
    for pkg, mod in CORE_DEPS.items():
        out.append(f"  {'OK   ' if _have(mod) else 'MISS '}  {pkg} ({mod})")
    out.append("")
    out.append("[OCR 可选栈] 缺失则附件扫描件只标 needs_ocr，不出 OCR 文本")
    for pkg, mod in OCR_PY_DEPS.items():
        out.append(f"  {'OK   ' if _have(mod) else 'MISS '}  {pkg} ({mod})")
    for bin_name, path in ocr_bin.items():
        out.append(f"  {'OK   ' if path else 'MISS '}  {bin_name} (bin: {path or '—'})")
    out.append("")
    out.append("[OCR 引擎健康] get_ocr().health()")
    for name, ok in (health or {}).items():
        out.append(f"  {'OK   ' if ok else 'DOWN '}  engine={name}")
    out.append("=" * 64)
    return "\n".join(out)


def test_lib_modules_importable():
    """共享 lib 模块均可导入（不依赖外部 std_lib）。"""
    import importlib
    mods = [
        f"{_PKG}.lib.http",
        f"{_PKG}.lib.text_utils",
        f"{_PKG}.lib.ocr",
        f"{_PKG}.lib.doc_number",
        f"{_PKG}.lib.cache",
        f"{_PKG}.lib.lock",
        f"{_PKG}.lib.doc_convert",
    ]
    for m in mods:
        importlib.import_module(m)


def test_pdf_text_layer_capable():
    """非 OCR 的 PDF 文本层抽取能力是否具备（pypdf / PyMuPDF 任一即可）。"""
    _pdf_lib_available = importlib.import_module(f"{_PKG}.lib.text_utils")._pdf_lib_available
    assert _pdf_lib_available(), (
        "PDF 文本层抽取不可用（pypdf / PyMuPDF 均未安装）；"
        "请 pip install pypdf 或 PyMuPDF"
    )


def _ocr_gap(msg):
    """OCR 缺口处理：默认降级为 SKIP（非必需）；设 REFACTOR_REQUIRE_OCR=1 升级为必检 FAIL。"""
    require = os.environ.get("REFACTOR_REQUIRE_OCR", "0") == "1"
    if require:
        pytest.fail(msg)
    pytest.skip(f"[OCR 非必需] {msg}")


def test_ocr_stack_probe(capsys):
    """探测 OCR 全栈；引擎缺失时默认 SKIP（非必需），可由 REFACTOR_REQUIRE_OCR=1 升级为必检失败。"""
    ocr_py = _probe_ocr_py()
    ocr_bin = _probe_ocr_bin()
    get_ocr = importlib.import_module(f"{_PKG}.lib.ocr").get_ocr
    health = get_ocr().health()

    print(_format_report(ocr_py, ocr_bin, health))

    # 一致性：健康表任一可用 ⟺ 探测到对应引擎依赖齐全
    paddle_ok = ocr_py["paddleocr"] and ocr_py["numpy"] and ocr_py["Pillow"]
    tesseract_ok = bool(ocr_bin["tesseract"]) and ocr_py["pytesseract"] and ocr_py["Pillow"]
    expected_any = paddle_ok or tesseract_ok
    actual_any = any(health.values())
    assert actual_any == expected_any, (
        f"OCR 健康表与探测不一致：health={health}，"
        f"探测 paddle_ok={paddle_ok} tesseract_ok={tesseract_ok}"
    )

    if actual_any:
        # 引擎可用但缺 PyMuPDF：PDF 栅格化不可用，PDF 扫描件 OCR 仍跑不起来
        if not ocr_py["PyMuPDF"]:
            _ocr_gap(
                "OCR 引擎可用，但缺少 PyMuPDF（PDF 栅格化必需），PDF 扫描件 OCR 仍不可用；"
                "pip install PyMuPDF"
            )
        return

    # 无任何可用引擎：默认 SKIP（非必需）
    _ocr_gap(
        "无可用 OCR 引擎（paddleocr 未装，且 tesseract 二进制缺失）："
        "扫描型附件将只标 needs_ocr 不出文本。修复：pip install paddleocr"
        " 或安装系统 tesseract 二进制（保留已装的 pytesseract）；"
        "本检查默认 SKIP（非必需），设 REFACTOR_REQUIRE_OCR=1 可升级为必检失败。"
    )


# OCR 功能验证的超时护栏（秒）：Paddle 首次推理会下载/加载模型，可能很慢。
OCR_FUNC_TIMEOUT = int(os.environ.get("REFACTOR_OCR_FUNC_TIMEOUT", "300"))


def _run_with_timeout(fn, timeout, label):
    """在守护线程里执行 fn；超时则抛 TimeoutError（避免卡死套件）。"""
    box: dict = {}
    exc: dict = {}

    def _t():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            exc["e"] = e

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"{label} 在 {timeout}s 内未完成（可能模型下载/加载过慢）")
    if "e" in exc:
        raise exc["e"]
    return box.get("v")


def test_ocr_functional_if_engine_present():
    """若引擎可用，做一次最小推理冒烟（空白图），验证引擎可加载。

    分阶段进度日志（运行加 -s --log-cli-level=INFO 可见）；首次推理可能下载/加载模型，
    超过 REFACTOR_OCR_FUNC_TIMEOUT 秒则跳过而非卡死。无引擎则跳过。
    """
    log = logging.getLogger("refactor.collectors.tests.env_probe")
    get_ocr = importlib.import_module(f"{_PKG}.lib.ocr").get_ocr

    log.info("[OCR 功能验证] 检查引擎健康（首次 import 引擎包可能较慢）…")
    ocr = get_ocr()
    health = ocr.health()
    log.info("[OCR 功能验证] health=%s", health)
    if not any(health.values()):
        pytest.skip("无可用 OCR 引擎，跳过功能验证")

    import numpy as np
    from PIL import Image

    blank = np.zeros((16, 16, 3), dtype="uint8")
    log.info(
        "[OCR 功能验证] 开始首次推理（Paddle 可能下载/加载模型，较慢，最多等待 %ds）…",
        OCR_FUNC_TIMEOUT,
    )
    try:
        res = _run_with_timeout(lambda: ocr.recognize_image(blank), OCR_FUNC_TIMEOUT, "OCR 首次推理")
    except TimeoutError as e:
        pytest.skip(f"[OCR 功能验证] {e}；跳过（非阻塞）")
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"OCR 引擎加载/推理异常：{e}")
    assert res is not None
    # 空白图预期 success=False 或空文本，只要不崩即视为引擎可用
    assert hasattr(res, "text")
    log.info(
        "[OCR 功能验证] 完成：engine=%s success=%s",
        getattr(res, "engine", ""),
        getattr(res, "success", ""),
    )


def test_ocr_degradation_gating():
    """无引擎时，OCR 分支必须被门控（_ocr_available=False），不会静默伪成功。"""
    get_ocr = importlib.import_module(f"{_PKG}.lib.ocr").get_ocr
    _ocr_available = importlib.import_module(f"{_PKG}.lib.text_utils")._ocr_available

    health = get_ocr().health()
    if not any(health.values()):
        assert not _ocr_available(), "环境无 OCR 引擎，但 _ocr_available 返回 True（门控失效）"
