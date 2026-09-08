# -*- coding: utf-8 -*-
"""
downloader.py —— 下载模块（交付标准第三节：scripts/ 按模块拆分）

职责：封装 HTTP 下载与附件/正文文档落盘。与四源（gov/mof/nfra/pbc）的
scripts/downloader.py 结构一致（职责拆分 + 再导出，保证行为可审计）；
底层下载复用 utils/scraper_std 的成熟实现。

统一收敛（2026-09-04，共享库对齐审计 阶段 1）
------------------------------------------------
  * `safe_filename` / `sha256_of` 已改为复用 `scraper_std.crawler_common`，删除内联实现。
    经实测：二者在本项目内**无任何调用点**（仅定义 + `__all__` 导出），属死代码；
    共享库版本即四源同款，收敛后口径统一、零行为差异。
  * ⚠️ `sniff_kind` **保留内联，不可替换**：本项目版返回**扩展名**
    （`".pdf"` / `".zip"` / `".bin"`，带点），而 `crawler_common.sniff_kind` 返回
    **类型 token**（`"pdf"` / `"docx"` / `"xlsx"` / `"ole2"`，不带点）——**同名不同义**，
    直接替换会改变调用方行为，故维持现状并在此显式标注。

用法示例：
    from downloader import AdaptiveHttpClient, download_file
    client = AdaptiveHttpClient()
    ok, sha, size = download_file(client, url, "tmp.bin")
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import hashlib
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "utils")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from std_lib.scraper_std.attachments import (  # noqa: E402
    download_and_extract,
    is_attachment_url,
    pick_body_doc,
)

# 阶段 1 收敛：文件名安全化 / 摘要 复用共享库（与四源 downloader.py 同款）
from std_lib.scraper_std.crawler_common import safe_filename, sha256_of  # noqa: E402
from std_lib.scraper_std.http import AdaptiveHttpClient  # noqa: E402

# 二进制附件缓存委托通用 BlobCache（与四源统一抽象层；落盘于 cache/supp/attachments/）
try:
    from std_lib.scraper_std.cache_store import BlobCache
except ImportError:
    from std_lib.scraper_std.cache_store import BlobCache

_BLOB = None       # BlobCache 实例；None 表示禁用缓存
_OFFLINE = False   # 离线模式开关（仅影响 download_file 缺失时的返回值）

def set_cache_dir(path):
    """设置二进制附件缓存根目录（启用/禁用缓存）。

    ``path`` 与本仓库其他源一致，取**源根目录** ``cache/<source>``
    （如 ``cache/supp``）；内部自动路由到 ``<path>/attachments/``，
    与 ``get_source_cache("<source>").blobs`` 命名空间严格对齐。
    ``path=None`` 表示禁用缓存。
    """
    global _BLOB
    _BLOB = BlobCache(os.path.join(path, "attachments")) if path else None

def set_offline(flag):
    """设置离线模式开关。True 时 download_file 优先复用缓存、缺失返回 (False, ...)。"""
    global _OFFLINE
    _OFFLINE = bool(flag)

_MAGIC = {
    b"%PDF-": ".pdf", b"\x89PNG\r\n\x1a\n": ".png", b"GIF87a": ".gif",
    b"GIF89a": ".gif", b"\xff\xd8\xff": ".jpg", b"PK\x03\x04": ".zip",
}

def sniff_kind(data: bytes, name: str = "") -> str:
    """按 magic bytes + 文件名后缀推断**扩展名**（返回 ".pdf"/".zip"/".bin"，带点）。

    ⚠️ 与 ``crawler_common.sniff_kind`` **同名不同义**：后者返回类型 token
    （"pdf"/"docx"/"xlsx"/"ole2"，不带点）。二者不可互换，本项目保留内联实现。
    """
    for magic, ext in _MAGIC.items():
        if data[: len(magic)] == magic:
            return ext
    base = (name or "").lower()
    m = re.search(r"\.([a-z0-9]{1,6})$", base)
    return "." + m.group(1) if m else ".bin"

def download_file(client: AdaptiveHttpClient, url: str, dest_path: str, **kw) -> tuple:
    """流式下载单文件到 dest_path，返回 (ok, sha_or_msg, size)。

    委托通用 BlobCache 层（regulatory_scrapers/cache/supp/attachments/）：
      * 成功下载后，以 **URL-sha1** 作为 owner 镜像原始字节（content-sha256 命名 +
        manifest 追踪），供断点续跑 / 离线复现复用；
      * 离线模式（``_OFFLINE``）下若缓存命中则直接落盘 dest_path、跳过网络；
        缺失则返回 ``(False, "offline: 缓存缺失", 0)``（与二进制下载器元组语义一致，
        不抛异常）。
    """
    owner = hashlib.sha1(url.encode("utf-8")).hexdigest()
    d = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(d, exist_ok=True)

    # 离线：优先从 BlobCache 复用原始字节
    if _OFFLINE and _BLOB is not None:
        m = _BLOB.manifest(owner)
        atts = m.get("attachments") or []
        if atts:
            entry = atts[0]
            data = _BLOB.load(owner, entry["attachment_name"])
            with open(dest_path, "wb") as fh:
                fh.write(data)
            return True, entry.get("sha256", ""), len(data)
        return False, "offline: 缓存缺失", 0

    ok, msg, size = client.download_file(url, dest_path, **kw)
    # 成功：镜像到 BlobCache（content-sha256 命名，扩展名取 dest_path 原名）
    if ok and _BLOB is not None:
        try:
            with open(dest_path, "rb") as fh:
                data = fh.read()
            _BLOB.save(owner, data, os.path.basename(dest_path), url=url, source_saved=True)
        except Exception:
            pass  # 缓存失败不影响主流程落盘
    return ok, msg, size

__all__ = [
    "AdaptiveHttpClient", "download_file", "download_and_extract",
    "pick_body_doc", "is_attachment_url", "sniff_kind", "safe_filename", "sha256_of",
    "set_cache_dir", "set_offline",
]
# 注：sniff_kind 为本项目内联版（返回**扩展名**，带点），与 crawler_common.sniff_kind
# （返回类型 token）不同；safe_filename / sha256_of 已由 crawler_common 提供。
