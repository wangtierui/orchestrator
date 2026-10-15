# -*- coding: utf-8 -*-
"""tests/test_gov_attachment_url_encoding.py — gov 附件中文名URL 的回归守卫。

缺陷来源（2026-10-16 夹具实测确认，非推断）：
`scan_attachment_links` 曾把 `urljoin(base_url, href)` 的产物**直接**交给 `robust_get`，
从不 percent-encode。而 gov.cn 附件名普遍含中文（`.../20240105_附件1.xlsx`）⇒
请求层拼 ASCII 请求行时抛 `UnicodeEncodeError` ⇒ **中文名附件 100% 下载失败**
（崩溃而非降级）。本守卫锁定修复，防止回潮。

三条守卫：
  1. 正例：中文名链接被percent-encode（`%E9%99%84...`）；
  2. 负例：**ASCII 名链接逐字不变**（防过度编码引入行为变化）；
  3. 正例：编码**幂等**（已编码再编码不变——否则重复处理会二次编码）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from modules.regulatory_scrapers.collectors.gov.attachments import (
    encode_url_path,
    scan_attachment_links,
)

BASE = "https://www.gov.cn/zhengce/content/202401/t20240105_1001.htm"
HTML = """<div class="appendix">
  <a href="./files/20240105_附件1.xlsx">附件1：登记表</a>
  <a href="./files/20240105_附件2.pdf">附件2：实施计划</a>
  <a href="./files/20240105_ascii3.docx">附件3：ASCII 名</a>
</div>"""


def test_chinese_attachment_url_is_encoded() -> None:
    """正例：中文名附件链接被 percent-encode（否则请求层崩溃）。"""
    links = dict(scan_attachment_links(HTML, BASE))
    cn = [u for u in links if "附件" not in u and "%E9%99%84" in u]
    assert len(cn) == 2, f"两个中文名链接都应被编码：{links}"
    for u in cn:
        assert u.isascii(), f"URL 仍含非 ASCII（未编码）：{u}"


def test_ascii_attachment_url_unchanged() -> None:
    """负例：ASCII 名链接**逐字不变**（防过度编码造成行为变化）。"""
    links = scan_attachment_links(HTML, BASE)
    ascii_urls = [u for u, _ in links if u.isascii()]
    assert ascii_urls, "ASCII 名附件应仍在结果中"
    assert any(u.endswith("20240105_ascii3.docx") for u in ascii_urls), (
        f"ASCII 名链接被改动了：{ascii_urls}")


def test_encoding_is_idempotent() -> None:
    """正例：编码幂等（重复处理不会二次编码）。"""
    once = encode_url_path("https://x.cn/files/附件1.xlsx")
    twice = encode_url_path(once)
    assert once == twice, f"非幂等：{once} → {twice}"
    assert "%E9%99%84" in once


def test_query_and_fragment_preserved() -> None:
    """正例：query/fragment 原样保留（只编码 path）。"""
    url = "https://x.cn/files/附件1.xlsx?v=1&t=2#page3"
    out = encode_url_path(url)
    assert out.endswith("?v=1&t=2#page3"), f"query/fragment 被改动：{out}"
    assert "%E9%99%84" in out


def test_empty_and_plain_urls_are_noop() -> None:
    """负例：空串与纯 ASCII URL 不变（零行为变化）。"""
    assert encode_url_path("") == ""
    plain = "https://x.cn/files/a.xlsx"
    assert encode_url_path(plain) == plain


def test_no_unicodeencodeerror_on_chinese_path() -> None:
    """正例（核心回归）：中文名路径 URL 可被 ASCII 编码（不再触发请求层崩溃）。

    这是缺陷的直接症状：`urljoin` 产物 `.encode("ascii")` 曾抛
    `UnicodeEncodeError`；编码后必须能编码成功。
    """
    raw = "https://www.gov.cn/zhengce/files/20240105_附件1.xlsx"
    encoded = encode_url_path(raw)
    encoded.encode("ascii")  # 不抛即通过
    assert "%E9%99%84" in encoded
