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

# 页眉/页脚块（多行，跨页重复）：
#   [标题行]\n编号 LHQ-D-2011-0008\n页码 第 N 页 共 M 页\n第N页共M页
_PAGE_HEADER_BLOCK_RE = re.compile(
    r"(?:[^\n]{1,40}\n)?"                                # 可选标题行（制度名）
    r"编号\s+[0-9A-Za-z\-]{3,}\n"                        # 编号行（LHQ-D-2011-0008 型，无冒号）
    r"页码\s*第\s*\d+\s*页\s*共\s*\d+\s*页\n"            # 页码行
    r"第\s*\d+\s*页\s*共\s*\d+\s*页\n?"
)
# 无编号的纯页码块（兜底）
_PAGE_NUM_BLOCK_RE = re.compile(r"\n(?:页码\s*)?第\s*\d+\s*页\s*共\s*\d+\s*页\n?")
# 孤立页码行（仅数字+页/共 等）
_PAGE_LINE_RE = re.compile(r"^\s*(?:第\s*\d+\s*页\s*共\s*\d+\s*页|页码[\s:：]*(?:第)?\s*\d+\s*页\s*共\s*\d+\s*页|\d+\s*/\s*\d+)\s*$")
# 独立制度编号行（页眉残留，无冒号，如 编号 LHQ-D-2011-0008）——正文编号字段均带冒号（文件编号：xxx）不受影响
_DOC_ID_LINE_RE = re.compile(r"^\s*编号\s+[0-9A-Za-z\-]{3,}\s*$")


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
    """正文规范化：
    1) 去控制符/隔离空白；
    2) 块级剥离页眉页脚（多行「标题+编号+页码」/ 纯页码块）——2026-09-08 遗留修复；
    3) 行级 trim + 垃圾行启发式；空行收敛。
    """
    t = _CTRL_RE.sub("", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # 块级页眉/页脚剥离（先于行级，防行级拆散块）
    t = _PAGE_HEADER_BLOCK_RE.sub("\n", t)
    t = _PAGE_NUM_BLOCK_RE.sub("\n", t)
    lines = [_MULTI_BLANK_RE.sub(" ", ln).strip() for ln in t.split("\n")]
    lines = [ln for ln in lines if ln and not _PAGE_LINE_RE.match(ln)
             and not _DOC_ID_LINE_RE.match(ln)]
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


def backfill_clauses() -> dict:
    """对存量 processed fulltext 回补条文结构（R21，无需重摄）：写 <ipn>_clauses.json，
    回刷主 json 与 index 的 chapter_count/article_count。返回统计。"""
    import json as _json

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    proc_dir = os.path.join(data_dir, "processed")
    idx_path = os.path.join(data_dir, "internal_policy_index.json")
    if not os.path.isdir(proc_dir):
        return {"error": "no processed dir"}
    sys.path.insert(0, os.path.join(_ORCH_ROOT, "std_lib"))
    from std_lib.scraper_std.document_structure import (  # noqa: PLC0415
        extract_structure,
        render_markdown,
    )
    n = 0
    for fn in sorted(os.listdir(proc_dir)):
        if not fn.endswith("_fulltext.json"):
            continue
        ipn = fn[: -len("_fulltext.json")]
        ft = os.path.join(proc_dir, fn)
        obj = _json.load(open(ft, encoding="utf-8"))
        stru = extract_structure(obj.get("text", ""))
        _json.dump({"ipn": ipn, "chapters": stru["chapters"], "articles": stru["articles"]},
                   open(os.path.join(proc_dir, ipn + "_clauses.json"), "w", encoding="utf-8"),
                   ensure_ascii=False, indent=2)
        # MD 渲染视图（JSON 为规范源；MD 供 drafter 条款对照/人工审阅）
        title = ""
        main_p0 = os.path.join(proc_dir, ipn + ".json")
        if os.path.exists(main_p0):
            try:
                title = _json.load(open(main_p0, encoding="utf-8")).get("title", "")
            except Exception:
                pass
        md = render_markdown(stru, title=title)
        open(os.path.join(proc_dir, ipn + "_clauses.md"), "w", encoding="utf-8").write(md)
        main_p = os.path.join(proc_dir, ipn + ".json")
        if os.path.exists(main_p):
            try:
                m = _json.load(open(main_p, encoding="utf-8"))
                m["chapter_count"] = stru["chapter_count"]
                m["article_count"] = stru["article_count"]
                _json.dump(m, open(main_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            except Exception:
                pass
        n += 1
    if os.path.exists(idx_path):
        try:
            idx = _json.load(open(idx_path, encoding="utf-8"))
            for rec in idx.get("records", []):
                cp = os.path.join(proc_dir, rec.get("ipn", "") + "_clauses.json")
                if os.path.exists(cp):
                    c = _json.load(open(cp, encoding="utf-8"))
                    rec["chapter_count"] = len(c.get("chapters", []))
                    rec["article_count"] = len(c.get("articles", []))
            _json.dump(idx, open(idx_path + ".tmp", "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)
            os.replace(idx_path + ".tmp", idx_path)
        except Exception:
            pass
    return {"backfilled": n}


def renormalize_processed() -> dict:
    """对 data/processed/*_fulltext.json 全量重跑 normalize_text（清洗规则升级后回刷）。

    幂等：仅在清洗后文本变化时回写；同时回刷 index 的 text_chars。返回统计。
    """
    import json as _json

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    proc_dir = os.path.join(data_dir, "processed")
    idx_path = os.path.join(data_dir, "internal_policy_index.json")
    changed = unchanged = 0
    if os.path.isdir(proc_dir):
        for fn in sorted(os.listdir(proc_dir)):
            if not fn.endswith("_fulltext.json"):
                continue
            p = os.path.join(proc_dir, fn)
            try:
                obj = _json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            new_t = normalize_text(obj.get("text", ""))
            if new_t != obj.get("text", ""):
                _json.dump({"ipn": obj.get("ipn", ""), "text": new_t},
                           open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                os.replace(p + ".tmp", p)
                changed += 1
            else:
                unchanged += 1
    # 回刷主索引 text_chars
    if os.path.exists(idx_path) and changed:
        try:
            idx = _json.load(open(idx_path, encoding="utf-8"))
            for rec in idx.get("records", []):
                ipn = rec.get("ipn", "")
                p = os.path.join(proc_dir, ipn + "_fulltext.json")
                if os.path.exists(p):
                    obj = _json.load(open(p, encoding="utf-8"))
                    rec["text_chars"] = len(obj.get("text", ""))
            _json.dump(idx, open(idx_path + ".tmp", "w", encoding="utf-8"),
                       ensure_ascii=False, indent=2)
            os.replace(idx_path + ".tmp", idx_path)
        except Exception:
            pass
    return {"changed": changed, "unchanged": unchanged, "processed_dir": proc_dir}
