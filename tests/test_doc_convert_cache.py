# -*- coding: utf-8 -*-
"""转换缓存守卫（批 64 / W-AV+W-AT）：命中缓存时**不得启动 LibreOffice**（⇒ 无剪贴板窗口 ✓）。

背景：LibreOffice 单次转换会清空系统剪贴板 6 次（实测 ✗）；同一 `.doc` 会被表格轨与富对象轨各转一次 ✗。
缓存按**输入字节 sha256** 命中 ⇒ 每次转换的"剪贴板窗口"降为"每份内容一次" ✓。
"""
from __future__ import annotations

import os

from std_lib.scraper_std import doc_convert as dc


def test_cache_key_is_content_based() -> None:
    """键 = 内容 sha256（与路径无关 ⇒ 同内容不同路径共享缓存 ✓）。"""
    assert dc.cache_key(b"abc") == dc.cache_key(b"abc")
    assert dc.cache_key(b"abc") != dc.cache_key(b"abd")
    assert len(dc.cache_key(b"abc")) == 64


def test_cache_hit_skips_libreoffice(monkeypatch, tmp_path) -> None:
    """**核心守卫**：缓存命中 ⇒ 直接返回，且**不得**调用 `find_libreoffice`/`isolated_run`。"""
    data = b"FAKE-DOC-PAYLOAD-" + os.urandom(8)
    key = dc.cache_key(data)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    fake_docx = b"PK\x03\x04fake-docx-bytes"
    (cache_dir / (key + ".docx")).write_bytes(fake_docx)

    monkeypatch.setattr(dc, "_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("RCO_DISABLE_LO_CACHE", raising=False)
    called = {"lo": 0}

    def _boom(*a, **k):
        called["lo"] += 1
        raise AssertionError("缓存命中时不得触碰 LibreOffice ✗")

    monkeypatch.setattr(dc, "find_libreoffice", _boom)
    src = tmp_path / "in.doc"
    src.write_bytes(data)
    out = dc.doc_to_docx(str(src), timeout=5)
    assert out and os.path.exists(out), "应把缓存内容落盘并返回路径"
    assert open(out, "rb").read() == fake_docx
    assert called["lo"] == 0, "缓存命中时不得调用 find_libreoffice"


def test_cache_disabled_by_env(monkeypatch, tmp_path) -> None:
    """`RCO_DISABLE_LO_CACHE=1` ⇒ 缓存失效（`_cache_get` 返回 None ✓）。"""
    data = b"payload"
    cache_dir = tmp_path / "c2"
    cache_dir.mkdir()
    (cache_dir / (dc.cache_key(data) + ".docx")).write_bytes(b"PK\x03\x04x")
    monkeypatch.setattr(dc, "_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("RCO_DISABLE_LO_CACHE", "1")
    assert dc._cache_get(data) is None
    monkeypatch.delenv("RCO_DISABLE_LO_CACHE", raising=False)
    assert dc._cache_get(data) == b"PK\x03\x04x"


def test_cache_helpers_never_raise(tmp_path, monkeypatch) -> None:
    """缓存**永远不是故障源**：不可写/不可读时静默返回 ✓。"""
    monkeypatch.setattr(dc, "_CACHE_DIR", str(tmp_path / "nope" / "\x00bad"))
    assert dc._cache_get(b"x") is None
    dc._cache_put(b"x", str(tmp_path / "missing.docx"))   # 不抛
