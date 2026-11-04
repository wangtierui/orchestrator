# -*- coding: utf-8 -*-
"""fetch_status 状态机：附件失败上卷与内网附件豁免（不触网、不触库）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collectors.lib.fetch_status import (
    ATTACHMENT_NON_FAILURE_MARKERS,
    derive,
    derive_from_record,
    has_attachment_failure,
)
from collectors.mof.attachments import _attachment_meta_record


def _unreachable_attachment():
    """还原 mof.download_attachment 对内网 IP 尝试失败后产出的真实明细形状。"""
    return _attachment_meta_record(
        {"file_id": "f1", "file_name": "x.doc",
         "file_url": "http://10.1.60.36:8888/tmp/x.doc"},
        "L1", "unreachable_private_ip", unreachable=True)


def test_unreachable_private_ip_is_exempt():
    att = _unreachable_attachment()
    assert att["extract_status"] == "unreachable_private_ip"
    assert "unreachable_private_ip" in ATTACHMENT_NON_FAILURE_MARKERS
    assert has_attachment_failure([att]) is False
    # 有正文 → ok；没正文仍严格 non-ok（豁免不等于「无脑标成功」）
    assert derive("法规正文", None, [att]) == "ok"
    assert derive("", None, [att]) == "no_content"
    assert derive_from_record({"content": "法规正文", "attachments": [att]}) == "ok"


def test_real_download_failure_still_escalates():
    failed = {"extract_status": "download_failed"}
    assert has_attachment_failure([failed]) is True
    assert derive("正文", None, [failed]) == "attachment_failed"
    # 同一条记录里既有内网豁免附件、又有真实下载失败 → 仍然上卷
    assert derive("正文", None, [_unreachable_attachment(), failed]) == "attachment_failed"


def test_no_attachments_does_not_fail():
    assert derive("正文", None, []) == "ok"
    assert derive("正文", None, None) == "ok"
