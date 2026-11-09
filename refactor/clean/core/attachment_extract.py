# -*- coding: utf-8 -*-
"""附件解析（clean 阶段职责，通用层层处理）。

标准 PDF（有文本层）→ PyPDF/pymupdf 直接读；扫描件（无文本层）→ OCR。
这是「文档理解」职责，归 clean：不分散到各采集源，也不做成独立命令。

输入：附件记录 dict（含 local_path 指向磁盘原文、sha256、extract_status 等）。
输出：更新后的附件 dict（text / extract_status / needs_ocr / garble_ratio）。
"""
from __future__ import annotations

import logging
import os
from typing import Any

from collectors.base import REPO_ROOT
from collectors.lib.text_utils import extract_document_text

LOG = logging.getLogger(__name__)


def _abs_local(rel: str | None) -> str | None:
    """附件原文相对路径 → 绝对路径。

    历史数据按不同根计算相对路径：gov 以 ``data/raw`` 为根（``../docs/...``），
    mof/nfra 以 ``data`` 为根（``../data/docs/...``）。文件实落
    ``refactor/data/docs/{src}/attachments/...``，逐个候选根命中即用。
    """
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel
    data_root = os.path.join(REPO_ROOT, "data")
    raw_dir = os.path.join(data_root, "raw")
    for base in (raw_dir, data_root, REPO_ROOT):
        p = os.path.normpath(os.path.join(base, rel))
        if os.path.exists(p):
            return p
    return None


def process_attachment(att: dict, *, enable_ocr: bool) -> dict | None:
    """对单个附件重做抽取（通用流程）。返回更新副本；无需变更返回 None（幂等）。

    * 已 ``extract_status='ok'`` 且文本非空 → 跳过（幂等，避免重复抽取/重写）。
    * 原文（local_path）在磁盘不可见 → 返回 None（待采集侧补齐落盘）。
    * 扫描件且引擎缺失 → ``extract_status`` 仍 ``'needs_ocr'``、文本空（降级待补，不报错）。
    """
    if att.get("extract_status") == "ok" and att.get("text"):
        existing = att.get("text") or ""
        # 自修复：① docx OOXML 泄漏（<w: 标签）② 老 doc 裸 UTF-16 硬解乱码（含 \ufffe 残片）
        if "<w:" not in existing and "\ufffe" not in existing:
            return None
    absp = _abs_local(att.get("local_path") or att.get("local_rel") or att.get("path"))
    if not absp or not os.path.exists(absp):
        return None
    try:
        with open(absp, "rb") as fh:
            data = fh.read()
    except Exception as e:  # noqa: BLE001
        LOG.warning("读附件原文失败 %s: %s", absp, e)
        return None
    name = att.get("file_name") or ""
    try:
        res = extract_document_text(data, name, enable_ocr=enable_ocr)
    except Exception as e:  # noqa: BLE001
        LOG.warning("附件抽取异常 %s: %s", name, e)
        return None
    new_att = dict(att)
    new_att["text"] = res.get("text", "") or ""
    new_att["extracted"] = bool(res.get("extracted"))
    new_att["extract_status"] = res.get("extract_status") or "unsupported"
    new_att["needs_ocr"] = bool(res.get("needs_ocr"))
    new_att["ocr_engine"] = res.get("ocr_engine") or ""
    new_att["garble_ratio"] = res.get("garble_ratio", 0.0) or 0.0
    return new_att
