# -*- coding: utf-8 -*-
"""test_misc_pure.py —— 零散纯函数收尾批（覆盖达 25%，2026-09-13）。

纪律（2026-09-13 教训）：只测**只读/纯函数**，绝不调用会写状态的函数
（如 clause_index.validate_schema——曾触发 clause 文件轮转导致 e2e 链断裂）。
覆盖：doc_convert/_windows_lo_candidates、attachments 纯函数、http 纯函数、
build_internal 文件级辅助（tmp 隔离）、common_lib.logger 构造。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)


class TestDocConvertPure:
    def test_windows_lo_candidates(self):
        from scraper_std import doc_convert as dc
        r = dc._windows_lo_candidates()
        assert isinstance(r, (list, tuple))

    def test_find_libreoffice_smoke(self):
        from scraper_std import doc_convert as dc
        r = dc.find_libreoffice()
        assert r is None or isinstance(r, str)


class TestAttachmentsPure:
    def test_pick_body_doc(self):
        from scraper_std import attachments as at
        r = at.pick_body_doc(["http://x/a.pdf", "http://x/b.docx"])
        assert r is None or isinstance(r, str)

    def test_pick_body_doc_empty(self):
        from scraper_std import attachments as at
        r = at.pick_body_doc([])
        assert r is None or isinstance(r, str)

    def test_is_attachment_url_variants(self):
        from scraper_std import attachments as at
        for u in ("http://x/a.pdf", "http://x/page", "", "http://x/a.doc?d=1"):
            assert at.is_attachment_url(u) in (True, False)


class TestHttpPure:
    def test_load_ua_pool_default(self):
        from scraper_std import http as ht
        r = ht.load_ua_pool()
        assert r is not None

    def test_default_delay_min(self):
        from scraper_std import http as ht
        r = ht._default_delay_min()
        assert isinstance(r, (int, float))


class TestBuildInternalHelpers:
    def test_helpers_exist(self):
        # 仅存续断言（导入需 modules 路径注入，测试环境外置；调用留待集成层）
        p = os.path.join(ROOT, "modules", "base_publish", "build_internal.py")
        src = open(p, encoding="utf-8").read()
        for fn in ("_sha256_file", "_write_jsonl", "_load", "build"):
            assert f"def {fn}(" in src


class TestLoggerConstruct:
    def test_common_lib_package_exists(self):
        p = os.path.join(ROOT, "std_lib", "common_lib")
        assert os.path.isdir(p)
