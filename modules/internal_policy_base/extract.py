# -*- coding: utf-8 -*-
"""
internal_policy_base.extract — 内部制度文本抽取与正文规范化（P6 extract + normalize）

抽取：
  委托 std_lib/scraper_std.crawler_common.extract_document_text 统一多格式抽取
  （pdf 文本层优先→扫描件 OCR 可选；docx/python-docx；xlsx/openpyxl；.doc→WPS COM→olefile 兜底）。
  OCR 是否启用经 config/ocr.yaml + enable_ocr 控制（R17 路径已 yaml 化）。

规范化（normalize_text）：
  - 控制字符/不可见剔除；保留中文全角标点与换行段落
  - 页眉/页脚/水印行剔除（按重复短行规则）
  - 超长空白合并；逐行 trim
"""
from __future__ import annotations

import os
import re
import shutil
import sys

import paths

_ORCH_ROOT = paths.ROOT
for _p in (_ORCH_ROOT, os.path.join(_ORCH_ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from std_lib.scraper_std.crawler_common import extract_document_text  # noqa: E402

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200e\u200f\ufeff]")
_MULTI_BLANK_RE = re.compile(r"[ \t\u3000]{2,}")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def extract_file(path: str, name: str = "", *, enable_ocr: bool = False,
                 ocr_timeout: int = 120) -> dict:
    """抽取单个文件 → 规范化正文 + 状态。返回含 text/status/needs_ocr/kind 的 dict。"""
    with open(path, "rb") as fh:
        data = fh.read()
    r = extract_document_text(data, name=name or os.path.basename(path),
                              enable_ocr=enable_ocr, ocr_timeout=ocr_timeout)
    text = r.get("text") or ""
    if r.get("extract_status") in ("ok", "ok_wps_com") and text:
        text = normalize_text(text)
    return {**r, "text": text}


def normalize_text(text: str) -> str:
    """正文规范化：去控制符/孤立空白/多重空行，行级 trim（保留换行段落）。"""
    t = _CTRL_RE.sub("", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_MULTI_BLANK_RE.sub(" ", ln).strip() for ln in t.split("\n")]
    # 丢弃疑似页眉页脚/水印：很短且不构成句子（无句末标点且 < 12 字符）的重复行
    t = "\n".join(_drop_junk_lines(lines))
    t = _MULTI_NL_RE.sub("\n\n", t).strip()
    return t


def _drop_junk_lines(lines: list[str]) -> list[str]:
    """启发式去页眉/页脚/水印行：长度<12 且无中文字句特征（含 <4 汉字）的行删除。
    保留 目录/编号 等结构化行（含数字/中文≥4 字）。"""
    out = []
    for ln in lines:
        if not ln:
            continue
        han = len(re.findall(r"[\u4e00-\u9fa5]", ln))
        if han == 0 and len(ln) < 40:
            # 纯数字/符号/英文短行：如页码、文件编号行——保留含字母数字的编号行，删纯页码
            if re.fullmatch(r"[\d\s\-—/页第.]+", ln):
                continue
        if 0 < han < 3 and len(ln) < 15:
            # 疑似水印/页眉（如 "阳光人寿" 两个汉字后无内容）
            continue
        out.append(ln)
    return out


def copy_original(src: str, dst_dir: str, rel_path: str) -> str:
    """原始件复制入仓（D-05 数据随仓），返回落盘绝对路径。同名冲突保留（按 rel 目录）。"""
    dst = os.path.join(dst_dir, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return dst
