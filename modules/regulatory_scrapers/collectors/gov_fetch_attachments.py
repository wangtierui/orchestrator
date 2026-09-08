# -*- coding: utf-8 -*-
"""
gov 法规详情页附件下载 + 文本抽取（修复：此前 gov 完全不抓附件）。

扫描详情页 HTML 中所有 <a href> 下载链接，识别 Word/PDF/Excel 等文档，
下载二进制（复用 crawler_common.robust_get 的反爬策略：UA 轮换、富请求头、
随机延时、指数退避、尊重 Retry-After），按魔数纠正扩展名，抽取内部文本
（crawler_common.extract_document_text，支持 PDF/docx/xlsx/旧.xls/旧.doc），
产出与 nfra 质量基线一致的标准化附件记录。

设计（对齐 DAMA / ISO 8000）：
  - 源文件始终留存（data/docs/gov_regulations_scraper/attachments/<条目id>/），绝不静默丢弃；
  - 下载失败 / 依赖缺失 / 扫描件 / 损坏等情形均透明标记 fetch_status / extract_status；
  - 单条附件异常不影响整体运行。
"""


# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import logging
import os
import re
import sys
from urllib.parse import urljoin

# 确保项目根（含 std_lib 包）在 sys.path，使 `from std_lib.scraper_std.crawler_common import` 可达
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 产物目录统一（Plan B 阶段 2b + 5b 收敛）：附件统一落盘 data/docs/gov_regulations_scraper/attachments/
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SRC_DIR)  # regulatory_scrapers（统一数据根）
ATTACHMENTS_DIR = os.path.join(REPO_ROOT, "data", "docs", "gov_regulations_scraper", "attachments")

from std_lib.scraper_std.crawler_common import (
    build_attachment_record,
    extract_document_text,
    is_attachment_url,
    robust_get,
    safe_filename,
    sniff_kind,
)
from std_lib.scraper_std.table_recovery import structured_table_fields  # noqa: E402

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

def fetch_gov_attachments(detail_html, entry_id, entry_title, out_dir, base_url,
                          *, enable_ocr: bool = False, timeout: int = 60):
    """扫描详情页附件链接，下载 + 抽取，返回 (records, attachment_text)。

    records 每项为标准化的附件记录（与 nfra 质量基线一致），可直接并入结构化输出。
    """
    links = scan_attachment_links(detail_html, base_url)
    records = []
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
        except Exception as e:
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
        # 表格结构化（2026-09-08 仿 supp 打通）：xlsx/docx/doc 附件解析结构化表 →
        # 回填 raw 附件记录表键（map_gov 已透传至 cleaned 39 列表格列）
        rec.update(structured_table_fields(data, fname, kind=kind))
        records.append(rec)
        if rec["extracted"] and rec.get("text"):
            texts.append(rec["text"])
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
