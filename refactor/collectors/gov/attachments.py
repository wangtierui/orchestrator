# -*- coding: utf-8 -*-
"""
gov 法规详情页附件下载 + 文本抽取（修复：此前 gov 完全不抓附件）。

原 modules/regulatory_scrapers/collectors/gov_fetch_attachments.py 迁出，逻辑逐函数等价。
import 全部收口到 refactor/collectors 内部（自包含，不依赖外部 std_lib/config/modules）。
"""

import logging
import os
import re
import sys
from urllib.parse import urljoin

from ..base import REPO_ROOT as _ORCH_ROOT  # refactor 项目根（自包含，不指向外部）

# local_path 相对基址收口到 refactor/data/raw（自包含）。
SCRAPERS_ROOT = os.path.join(_ORCH_ROOT, "data", "raw")

# 产物目录统一：附件统一落盘 docs_root("gov","attachments")
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRAPERS_ROOT  # 供 local_path 相对计算
from ..lib.cache import docs_root

ATTACHMENTS_DIR = docs_root("gov", "attachments")

from ..lib.http import robust_get
from ..lib.text_utils import (
    build_attachment_record,
    extract_document_text,
    is_attachment_url,
    safe_filename,
    sniff_kind,
)
from ..lib.rich_object import rich_object_fields
from ..lib.table_recovery import structured_table_fields

LOG = logging.getLogger("gov_attachments")

_A_RE = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")

_KIND_EXT = {
    "pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx",
    "ole2": ".doc", "zip": ".zip", "rar": ".rar", "unknown": ".bin",
}

def _link_text(html_fragment: str) -> str:
    return _TAG_RE.sub("", html_fragment).strip()

def scan_attachment_links(html: str, base_url: str):
    """从详情页 HTML 提取指向文档下载的 <a> 链接（绝对化、去重）。"""
    if not html:
        return []
    out = []
    seen = set()
    for m in _A_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        text = _link_text(m.group(2))
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        if is_attachment_url(abs_url, text):
            seen.add(abs_url)
            out.append((abs_url, text))
    return out

def _name_from(url: str, text: str) -> str:
    if text:
        return text
    frag = url.rstrip("/").split("/")[-1].split("?")[0]
    return frag or "attachment"

def _strip_ext(name: str) -> str:
    return _EXT_RE.sub("", name)

# 旧二进制格式（OLE2 系）：表格/富内容轨对其须先经 doc_convert（LibreOffice doc→docx）
# 才能解析，实测每次附件**两次启动 soffice 合计约 6.5 秒**，且多数 .doc 附件并无结构化
# 表格（产出为空）。批量采集时该开销占比极高 → 默认不对旧格式做 table/rich。
LEGACY_KINDS = frozenset({"ole2", "doc", "xls", "wps", "rtf", "ceb", "bin", "unknown"})


def fetch_gov_attachments(detail_html, entry_id, entry_title, out_dir, base_url,
                          *, enable_ocr: bool = False, timeout: int = 60,
                          enrich_legacy: bool = False):
    """扫描详情页附件链接，下载 + 抽取，返回 (records, attachment_text)。"""
    legacy_skipped = 0
    links = scan_attachment_links(detail_html, base_url)
    records: list = []
    if not links:
        return records, ""
    # 防御：entry_id/entry_title 可能为详情页完整 URL（含 : / ? = 等非法字符），
    # 必须统一清洗为合法目录名，否则 Windows 下会触发 WinError 123。
    raw_key = str(entry_id) if entry_id else (str(entry_title) if entry_title else "gov")
    dir_key = re.sub(r"[^\w一-鿿-]+", "_", raw_key).strip("_")[:80] or "gov"
    attach_dir = os.path.join(ATTACHMENTS_DIR, dir_key)
    os.makedirs(attach_dir, exist_ok=True)

    texts = []
    for url, text in links:
        st, data = robust_get(url, binary=True, referer=base_url, timeout=timeout)
        if not (st == 200 and data):
            rec = build_attachment_record(
                _name_from(url, text), url, "", data or b"",
                entry_id=str(entry_id), extract_status="download_failed",
                extracted=False)
            rec["link_text"] = text
            rec["fetch_status"] = "download_failed"
            records.append(rec)
            continue
        kind = sniff_kind(data, url)
        ext = _KIND_EXT.get(kind)
        if not ext:
            ue = os.path.splitext(url.split("?")[0])[1].lower()
            ext = ue if ue in (".pdf", ".doc", ".docx", ".xls", ".xlsx") else ".bin"
        raw_name = _strip_ext(text or _name_from(url, text))
        fname = safe_filename(raw_name, url) + ext
        dest = os.path.join(attach_dir, fname)
        try:
            with open(dest, "wb") as fh:
                fh.write(data)
        except Exception as e:  # noqa: BLE001
            LOG.warning("附件落盘失败 %s：%s", url, e)
            continue
        local_rel = os.path.relpath(dest, REPO_ROOT).replace("\\", "/")
        ext_rec = extract_document_text(data, fname, enable_ocr=enable_ocr)
        rec = build_attachment_record(
            fname, url, local_rel, data, entry_id=str(entry_id),
            text=ext_rec.get("text", ""),
            extracted=ext_rec.get("extracted", False),
            extract_status=ext_rec.get("extract_status", "unsupported"),
            needs_ocr=ext_rec.get("needs_ocr", False))
        rec["link_text"] = text
        rec["attachment_kind"] = ext_rec.get("kind", kind)
        # 表格结构化 + 富内容轨
        _kind = ext_rec.get("kind", kind)
        if enrich_legacy or _kind not in LEGACY_KINDS:
            rec.update(structured_table_fields(data, fname, kind=kind))
            try:
                _rk = re.sub(r"[^\w一-鿿-]+", "_", str(entry_id))[:80] or "gov"
                _rich = rich_object_fields(data, fname,
                                           image_dir=docs_root("gov", "diagrams"),
                                           rec_key=_rk + "_att")
                if _rich:
                    rec["rich_structured"] = _rich["rich_structured"]
                    rec["rich_text"] = _rich["rich_text"]
                    rec["rich_count"] = _rich["rich_count"]
            except Exception:  # noqa: BLE001
                pass
        else:
            legacy_skipped += 1
        records.append(rec)
        if rec["extracted"] and rec.get("text"):
            texts.append(rec["text"])
    if legacy_skipped:
        LOG.info("跳过旧格式附件的表格/富内容轨 %d 个（enrich_legacy=False）", legacy_skipped)
    return records, "\n\n".join(texts)

if __name__ == "__main__":
    # 单元测试：链接识别 + 命名（无需网络/第三方库）
    logging.basicConfig(level=logging.INFO)
    html = ('<a href="/files/通知.pdf">下载通知</a>'
            '<a href="javascript:void(0)">忽略</a>'
            '<a href="http://x/法规.docx">Word版</a>')
    links = scan_attachment_links(html, "http://www.gov.cn/doc/123")
    assert len(links) == 2, links
    assert links[0][0] == "http://www.gov.cn/files/通知.pdf", links[0]
    recs, txt = fetch_gov_attachments("", "id1", "标题", ".", "http://x/")
    assert recs == [] and txt == ""
    print("[fetch_attachments_gov] 链接识别与空输入处理 自检通过")
