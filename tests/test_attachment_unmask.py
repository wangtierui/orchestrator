# -*- coding: utf-8 -*-
"""批 54/W-W：附件**content-type/sniff 二次判定**回归守卫。

背景：实测 mof 附件 `83896.doc` 内容以 `MIME-Ver…` 开头（前导 MIME 头 + 载荷）⇒ 按扩展名走 OLE2/xlrd
解析全败（"Expected BOF record; found b'MIME-Ver'"），内容静默丢失。本批在**共享抽取器**单点增加
"拆封 → 重新嗅探 → 留证据"三步，使伪装附件可被正确抽到且**可审计**（`sniff` 字段）。
"""
from __future__ import annotations

import io


def test_header_prefixed_doc_unmasked() -> None:
    """前导 MIME/HTTP 头：拆封后按内层魔数判定（`83896.doc` 同类）。"""
    from std_lib.scraper_std.crawler_common import extract_document_text

    payload = b"Content-Type: application/pdf\r\nContent-Length: 12\r\n\r\n%PDF-1.4\n%%EOF\n"
    rec = extract_document_text(payload, "83896.doc", enable_ocr=False)
    sn = rec.get("sniff") or {}
    assert sn.get("declared_ext") == ".doc"
    assert sn.get("unmasked") == "headers", "前导头未被剥离（W-W 回归）"
    assert sn.get("inner_kind") == "pdf", "内层魔数未生效：%s" % sn


def test_mime_multipart_unmasked_and_extracted() -> None:
    """MIME multipart：取最大部分并抽出其文本。"""
    from email.message import EmailMessage

    from std_lib.scraper_std.crawler_common import extract_document_text

    msg = EmailMessage()
    msg["MIME-Version"] = "1.0"
    msg.set_content("外壳说明")
    msg.add_attachment("附件正文：保险销售行为可回溯管理要求（续保表述）".encode(),
                       maintype="text", subtype="plain", filename="a.txt")
    data = msg.as_bytes()
    rec = extract_document_text(data, "伪装.doc", enable_ocr=False)
    sn = rec.get("sniff") or {}
    assert sn.get("unmasked") == "mime_multipart", "MIME 未被拆封：%s" % sn
    assert "续保表述" in (rec.get("text") or ""), "拆封后未抽出内层文本"


def test_html_disguised_as_doc() -> None:
    """HTML 冒充 .doc：按 HTML 取可见文本（去标签/脚本）。"""
    from std_lib.scraper_std.crawler_common import extract_document_text

    html = ("<html><head><title>t</title><script>var a=1;</script></head>"
            "<body><table><tr><td>药品儿童专用</td><td>限6岁以下</td></tr></table></body></html>")
    rec = extract_document_text(html.encode("utf-8"), "规则.doc", enable_ocr=False)
    assert rec.get("kind") == "html"
    assert "药品儿童专用" in (rec.get("text") or "")
    assert "<td>" not in (rec.get("text") or "") and "var a" not in (rec.get("text") or "")
    assert (rec.get("sniff") or {}).get("inner_kind") == "html"


def test_clean_payload_not_unmasked() -> None:
    """负例：正常载荷不得被"拆封"（避免误伤）。"""
    import openpyxl

    from std_lib.scraper_std.crawler_common import extract_document_text

    wb = openpyxl.Workbook()
    wb.active["A1"] = "正常表"
    buf = io.BytesIO()
    wb.save(buf)
    rec = extract_document_text(buf.getvalue(), "normal.xlsx", enable_ocr=False)
    sn = rec.get("sniff") or {}
    assert not sn.get("unmasked"), "正常载荷被误拆封：%s" % sn
    assert rec.get("kind") == "xlsx" and "正常表" in (rec.get("text") or "")
