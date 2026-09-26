# -*- coding: utf-8 -*-
"""
attachments.py —— 附件下载与全文提取（第六节 6.2/6.3）

能力：
  - 流式下载（复用 http.AdaptiveHttpClient.download_file），连接 10s / 读取 30s；
  - 全文提取：PDF(pdfplumber/pypdf 表格优先) / docx(python-docx) / xlsx(openpyxl) /
    xls(xlrd) / doc(OLE2) —— 复用 crawler_common.extract_document_text；
  - 正文文档优先下载：Word > PDF > OFD/CEB（6.2 优先级）；
  - 附件元数据：文件名/URL/MD5/下载状态/提取文本字数/提取状态；
  - 解析失败 → attachment_content="[附件解析失败: 原因]"，严禁留空；
  - 超大附件折中：全文写同名 .txt，主数据填写 attachment_content_path + MD5；
  - 标准重命名（6.4）+ 映射记录（原始文件名/原始 URL 溯源）。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from .http import AdaptiveHttpClient
from .naming import standard_filename

LOG = logging.getLogger("scraper_std.attachments")

# 正文文档优先级（6.2 ①）
_DOC_PRIORITY = [".docx", ".doc", ".pdf", ".ofd", ".ceb", ".wps", ".rtf"]
_ATTACH_EXTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".ofd",
    ".ceb",
    ".wps",
    ".rtf",
    ".zip",
    ".rar",
    ".txt",
}


def pick_body_doc(urls: list[str]) -> str | None:
    """
    6.2 ① 正文文档下载优先级：Word > PDF > OFD/CEB。
    从候选链接列表中选出应下载的正文文档 URL（无匹配返回 None）。
    """
    if not urls:
        return None

    def _rank(u: str) -> int:
        path = (u or "").split("?")[0].lower()
        for i, ext in enumerate(_DOC_PRIORITY):
            if path.endswith(ext):
                return i
        return 99

    best = min(urls, key=_rank)
    return best if _rank(best) < 99 else None


def is_attachment_url(url: str) -> bool:
    """附件链接判定（扩展名命中）。"""
    path = (url or "").split("?")[0].lower()
    return any(path.endswith(e) for e in _ATTACH_EXTS)


def download_and_extract(
    url: str,
    *,
    dest_dir: str,
    client: AdaptiveHttpClient,
    file_name_hint: str = "",
    index_no: str = "",
    title: str = "",
    pub_date: str = "",
    file_type: str = "附件",
    seq: int | None = None,
    max_bytes: int | None = None,
    text_extract: bool = True,
) -> dict[str, Any]:
    """
    下载 + 提取附件全文，返回标准化附件记录：
      {
        file_name(重命名后), original_name, file_url, local_path, size_bytes,
        sha256, fetch_status(ok/fail), extract_status, text(全文),
        text_length, attachment_content, needs_ocr, garble_ratio, extension
      }
    下载/解析失败均返回结构化记录（不抛异常）。
    """
    from .crawler_common import sniff_kind

    rec: dict[str, Any] = {
        "file_url": url,
        "original_name": file_name_hint or os.path.basename(url.split("?")[0]) or url,
        "fetch_status": "fail",
        "extract_status": "unsupported",
        "text": "",
        "text_length": 0,
        "attachment_content": "",
        "sha256": "",
        "size_bytes": 0,
        "needs_ocr": False,
        "garble_ratio": 0.0,
    }
    os.makedirs(dest_dir, exist_ok=True)

    # 1) 下载（流式 + 超时）
    tmp_name = f"_dl_{hashlib.md5(url.encode('utf-8')).hexdigest()[:8]}.bin"
    tmp_path = os.path.join(dest_dir, tmp_name)
    ok, sha_or_msg, size = client.download_file(url, tmp_path, max_bytes=max_bytes)
    if not ok:
        rec["fetch_status"] = "fail"
        rec["attachment_content"] = f"[附件下载失败: {sha_or_msg}]"
        LOG.error("附件下载失败 %s → %s", url, sha_or_msg)
        return rec
    # 2) 魔数纠正真实格式
    with open(tmp_path, "rb") as f:
        data = f.read()
    kind = sniff_kind(data, file_name_hint or tmp_name)
    ext = {
        "pdf": ".pdf",
        "docx": ".docx",
        "xlsx": ".xlsx",
        "ole2": ".xls",
        "zip": ".zip",
        "rar": ".rar",
    }.get(kind, os.path.splitext(file_name_hint)[1].lower() or ".bin")

    # 3) 标准重命名（6.4）
    final_name = standard_filename(
        index_no=index_no,
        title=title or file_name_hint,
        pub_date=pub_date,
        ext=ext,
        file_type=file_type,
        seq=seq,
        url=url,
    )
    final_path = os.path.join(dest_dir, final_name)
    try:
        if os.path.abspath(tmp_path) != os.path.abspath(final_path):
            os.replace(tmp_path, final_path)
    except OSError as e:
        LOG.warning("重命名失败，保留临时名 %s：%s", tmp_path, e)
        final_path = tmp_path
        final_name = os.path.basename(tmp_path)

    # 4) 全文提取（6.3 硬性要求）
    from .crawler_common import extract_document_text

    rec.update(
        {
            "file_name": final_name,
            "local_path": final_path,
            "sha256": sha_or_msg,
            "size_bytes": size,
            "fetch_status": "ok",
            "extension": ext,
        }
    )
    if not text_extract:
        rec["extract_status"] = "skipped"
        rec["attachment_content"] = "[附件文本提取已跳过]"
        return rec
    try:
        ex = extract_document_text(data, final_name, enable_ocr=False)
        text = (ex.get("text") or "").strip()
        rec["extract_status"] = ex.get("extract_status", "unsupported")
        rec["needs_ocr"] = bool(ex.get("needs_ocr"))
        rec["garble_ratio"] = float(ex.get("garble_ratio") or 0.0)
        rec["text"] = text
        rec["text_length"] = len(text)
        if text:
            rec["attachment_content"] = text
            LOG.info(
                "附件文本提取成功 %s（%d 字，%s）", final_name, len(text), rec["extract_status"]
            )
        else:
            status = rec["extract_status"]
            rec["attachment_content"] = f"[附件解析失败: {status}]"
            LOG.warning("附件文本提取为空 %s（%s）", final_name, status)
    except Exception as e:
        rec["extract_status"] = "error"
        rec["attachment_content"] = f"[附件解析失败: {type(e).__name__}: {e}]"
        LOG.error("附件解析异常 %s：%s", final_name, e)
    return rec


def write_large_content_sidecar(
    attachment: dict[str, Any],
    content_dir: str,
    *,
    threshold: int = 200_000,
) -> dict[str, Any]:
    """
    超大附件折中策略（6.3 运维备注）：全文 > threshold 字时压缩到 content_dir
    下同名 .txt，主数据字段填 attachment_content_path + attachment_content_md5。
    返回更新后的附件记录。
    """
    text = attachment.get("text") or attachment.get("attachment_content") or ""
    if len(text) <= threshold:
        return attachment
    os.makedirs(content_dir, exist_ok=True)
    base = os.path.splitext(attachment.get("file_name", "attach"))[0]
    txt_path = os.path.join(content_dir, f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    attachment["attachment_content"] = ""
    attachment["attachment_content_path"] = txt_path
    attachment["attachment_content_md5"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return attachment


if __name__ == "__main__":  # 离线自检（不联网）
    assert pick_body_doc(["http://x/a.pdf", "http://x/a.docx"]) == "http://x/a.docx"
    assert pick_body_doc(["http://x/a.pdf"]) == "http://x/a.pdf"
    assert pick_body_doc(["http://x/a.ofd"]) == "http://x/a.ofd"
    assert pick_body_doc(["http://x/a.html"]) is None
    assert is_attachment_url("http://x/1.xlsx")
    assert not is_attachment_url("http://x/detail.html")
    print("[scraper_std.attachments] 离线自检通过")
