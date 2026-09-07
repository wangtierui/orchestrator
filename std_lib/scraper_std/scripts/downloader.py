# -*- coding: utf-8 -*-
"""
downloader —— 下载模块再导出（单一事实源，对应四源 scripts/downloader.py）。

职责：封装 HTTP 下载与附件/正文文档落盘。底层复用 crawler_common 与
scraper_std 的成熟实现，本模块仅做再导出，保证与既有爬虫行为一致、可审计。
相对导入（from ..X）确保本模块随 scraper_std 包一起被解析，避免与标准库同名模块冲突。

用法示例：
    from scraper_std.scripts.downloader import AdaptiveHttpClient, download_file
    client = AdaptiveHttpClient()
    ok, sha, size = download_file(client, url, "tmp.bin")
"""
from __future__ import annotations

from ..attachments import (  # noqa: F401
    download_and_extract,
    is_attachment_url,
    pick_body_doc,
)
from ..crawler_common import safe_filename, sha256_of, sniff_kind  # noqa: F401
from ..http import AdaptiveHttpClient  # noqa: F401


def download_file(client: AdaptiveHttpClient, url: str, dest_path: str, **kw) -> tuple:
    """流式下载单文件到 dest_path，返回 (ok, sha_or_msg, size)。"""
    return client.download_file(url, dest_path, **kw)


__all__ = [
    "AdaptiveHttpClient", "download_file", "download_and_extract",
    "pick_body_doc", "is_attachment_url", "sniff_kind", "safe_filename", "sha256_of",
]
