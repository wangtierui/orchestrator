# -*- coding: utf-8 -*-
"""tools.docreader_parse_driver — **docreader 解析驱动**（N-180，2026-09-30）

为什么有这么一个"只做一件事"的小脚本
------------------------------------
`docreader`（Tencent/WeKnora）**不能**在主环境（3.13）里导入：
  · 它需要 **Python >=3.10.18** 且依赖里 `textract==1.5.0` **只有 sdist**（2016 年包）→ 隔离在
    **独立 venv**（本仓用 3.11.9，贴近其官方 Docker 基线 `python:3.10.18-bookworm`）；
  · 它**不可安装**：`pyproject.toml` **无 `[build-system]`/`[tool.setuptools]`**，而包目录是
    **flat-layout 多顶层包**（`proto/client/models/parser/splitter/testdata`）→
    `pip install -e .` 在 *Getting requirements to build editable* 阶段**必然失败**
    （实测报 `Multiple top-level packages discovered in a flat-layout`）。
    → 上游的正规用法是 **`PYTHONPATH=<WeKnora 根>` 直接 `python -m docreader.*`**（其 Dockerfile 亦如此）。

故本文件是**跨解释器边界**的驱动：由 **venv 的 python** 执行，**只用标准库**（不 import 本仓任何模块），
把解析结果以 JSON 落到指定路径。跨解释器因此**不需要 gRPC 常驻服务**（无端口、无依赖泄漏、
一次性进程天然隔离，异常即非零退出，**降级可观测**）。

用法（由 `std_lib/common_lib/docreader_bridge.py` 调用，一般不手工执行）
----------------------------------------------------------------------
    <venv>/Scripts/python.exe tools/docreader_parse_driver.py \
        --file <输入> --out <结果.json> [--engine builtin|markitdown|opendataloader] [--timeout 300]

输出 JSON 结构（稳定契约）：
    {"ok": bool, "engine": str, "markdown": str, "source_blocks": [...],
     "image_count": int, "metadata": {...}, "error": str, "elapsed_s": float}
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="docreader 解析驱动（venv 侧；仅标准库）")
    ap.add_argument("--file", required=True, help="输入文件路径（本地文件）")
    ap.add_argument("--out", required=True, help="结果 JSON 输出路径")
    ap.add_argument("--engine", default="", help="解析引擎：builtin（默认）/markitdown/opendataloader")
    ap.add_argument("--max-chars", type=int, default=200000, help="markdown 截断上限（防超大结果）")
    a = ap.parse_args(argv)

    res: dict = {
        "ok": False,
        "engine": a.engine or "builtin",
        "markdown": "",
        "source_blocks": [],
        "image_count": 0,
        "metadata": {},
        "error": "",
        "elapsed_s": 0.0,
    }
    t0 = time.time()
    try:
        from docreader.parser import Parser

        doc = Parser().parse_file(
            file_name=os.path.basename(a.file),
            file_type=os.path.splitext(a.file)[1].lstrip(".").lower(),
            content=open(a.file, "rb").read(),
            parser_engine=(a.engine or None),
            engine_overrides={},
        )
        md = getattr(doc, "content", "") or ""
        res["markdown"] = md[: a.max_chars]
        res["truncated"] = len(md) > a.max_chars
        blocks = getattr(doc, "source_blocks", None) or []
        res["source_blocks"] = [
            {
                "start": getattr(b, "start", None),
                "end": getattr(b, "end", None),
                "locator_json": (getattr(b, "locator_json", "") or "")[:400],
            }
            for b in blocks[:200]
        ]
        images = getattr(doc, "images", None) or {}
        res["image_count"] = len(images)
        meta = getattr(doc, "metadata", None) or {}
        try:
            res["metadata"] = json.loads(json.dumps(meta, default=str)) if not isinstance(meta, dict) else meta
        except Exception:  # noqa: BLE001
            res["metadata"] = {"_repr": str(meta)[:500]}
        res["ok"] = bool(res["markdown"].strip())
        if not res["ok"]:
            res["error"] = "解析成功但正文为空（可能是纯扫描件：docreader **不做 OCR**，扫描页仅渲染为图）"
    except Exception as e:  # noqa: BLE001  驱动侧**必须**把异常转成结构化结果（跨进程不吞错）
        res["error"] = f"{type(e).__name__}: {e}"
        res["traceback_tail"] = traceback.format_exc()[-1200:]
    res["elapsed_s"] = round(time.time() - t0, 2)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, ensure_ascii=False)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
