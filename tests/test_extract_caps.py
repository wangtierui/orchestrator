# -*- coding: utf-8 -*-
"""tests/test_extract_caps.py — 单文件抽取/下载上限守卫（2026-10-26）。

## 为什么需要（三次整链实测）
| 现象 | 根因 |
|---|---|
| 51.6MB《进出口税则》PDF → 进程 **5.8GB** / 40 分钟不结束 | 抽取 SSOT 无任何大小上限 |
| 加了上限仍 **7.8GB** | `structured_table_fields` / `rich_object_fields` 对**同一份字节**各开一次解析器 |
| `r.read()` 全量入内存 | 下载无上限 |

这三个上限都必须是**函数自身**的属性（写在某个调用点，换个采集源就又漏了），
且**只对新文件生效**（`EXTRACT_VERSION` 保持 v1：旧缓存里的完整文本不能被 400 页截断覆盖）。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOF = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "mof")


class TestExtractCaps:
    def test_ssot_bounds(self) -> None:
        from std_lib.scraper_std import crawler_common as C

        assert C.MAX_EXTRACT_BYTES > 0 and C.MAX_PDF_PAGES > 0
        assert C.MAX_EXTRACT_BYTES <= 64 * 1024 * 1024, "上限过大，挡不住巨型附件"
        assert 100 <= C.MAX_PDF_PAGES <= 2000

    def test_too_large_is_evidence_not_crash(self) -> None:
        """超限必须**快速返回 + 留证据**（`too_large` + 实际字节数），而不是抛异常。"""
        from std_lib.scraper_std.crawler_common import MAX_EXTRACT_BYTES, extract_document_text

        blob = b"%PDF-1.4\n" + b"0" * (MAX_EXTRACT_BYTES + 1024)
        r = extract_document_text(blob, "巨型.pdf")
        assert r["extract_status"] == "too_large"
        assert r["extracted"] is False and r["text"] == ""
        assert r["size_bytes"] == len(blob)
        assert r["sha256"], "超限也要留指纹（否则无法核对是哪份文件）"

    def test_heavy_tracks_also_capped(self) -> None:
        """表格轨/富内容轨必须与文字轨**同一上限**（否则等于没加）。"""
        from std_lib.scraper_std.crawler_common import MAX_EXTRACT_BYTES
        from std_lib.scraper_std.rich_object import rich_object_fields
        from std_lib.scraper_std.table_recovery import structured_table_fields

        blob = b"0" * (MAX_EXTRACT_BYTES + 1024)
        t = structured_table_fields(blob, "巨型.pdf")
        assert t.get("table_recovery_method") == "too_large"
        r = rich_object_fields(blob, "巨型.pdf")
        assert r.get("rich_object_method") == "too_large"

    def test_download_cap_and_version_pinned(self) -> None:
        """下载上限存在；`EXTRACT_VERSION` 必须仍是 v1（升版会截断旧缓存）。

        ⚠️ 这里**静态读源码**而不是 import 该模块：`attachments.py` 依赖同包兄弟模块，
        用 `spec_from_file_location` 单独加载会缺 `__package__` 上下文（实测导入失败）。
        判据本身就是"文件里写了什么"，静态读反而更准。
        """
        src = open(os.path.join(MOF, "attachments.py"), encoding="utf-8").read()
        assert "MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024" in src, "下载上限缺失/被改"
        assert 'EXTRACT_VERSION = "v1"' in src, (
            "升版会让全部附件重抽，且用 400 页截断覆盖原本完整的文本（数据退化）")

    def test_download_reads_streaming(self) -> None:
        """下载必须**流式 + 边读边判上限**（`r.read()` 会整包进内存）。

        定位到 `download_attachment` 里那次 `urlopen`（文件里有多次 urlopen：
        `_request` 取列表页、`fetch_attachments` 取目录页），故先切出该函数的源码段。
        """
        src = open(os.path.join(MOF, "attachments.py"), encoding="utf-8").read()
        i = src.index("def download_attachment(")
        j = src.index("\ndef ", i + 10)
        body = src[i:j]
        assert "MAX_DOWNLOAD_BYTES" in body, "下载函数未接上限"
        assert "r.read(1 << 20)" in body, "未改流式读取（整包 read 会打爆内存）"
