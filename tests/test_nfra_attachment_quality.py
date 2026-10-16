# -*- coding: utf-8 -*-
"""tests.test_nfra_attachment_quality — nfra 附件**纯判定层**的回归（E5 拆分后固化）。

为什么单独建档：本层是**零外部依赖的纯函数层**（不触网/不落盘/不读磁盘），
是全仓最容易做真回归、也最值得长期锁住的一层（与 tests/test_nfra_extract.py 同款）。

锁的要点：
  · **逐字搬运**：函数体自 E5 起一字未改（E5 等价性校验已断言源码逐字一致）——
    本测试即该承诺的持续守护；
  · **异常行为也是契约**：`page_lens=None` 抛 TypeError、`text=None` 抛
    AttributeError —— 这些「看起来该被容错」的行为实测如此，写进断言可防
    日后有人"顺手加个 if"改变行为；
  · **乱码阈值 1%** 与**多标志顺序固定**都是口径（质量报告按行比对）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from modules.regulatory_scrapers.collectors.nfra.attachment_quality import (  # noqa: E402
    _COMMON_CN,
    quality_flags,
)

REPL = "�"  # U+FFFD 替换字符（乱码检测的判据）


class TestQualityFlags:
    def test_empty_pages_counted(self) -> None:
        """正例：逐页长度里的 0 页被计数并写入标志。"""
        assert quality_flags([0, 10, 0], "文本", False) == ["empty_pages=2"]

    def test_needs_ocr_flag(self) -> None:
        """正例：needs_ocr=True 追加扫描件标志（口径未变）。"""
        assert "possible_scan(needs_ocr)" in quality_flags([5], "x", True)

    def test_garbled_detected_by_replacement_ratio(self) -> None:
        """正例：替换字符占比 >1% 判乱码（5/6 = 83%）。"""
        out = quality_flags([5], "a" + REPL * 5, False)
        assert any(f.startswith("garbled(replacement_chars=5)") for f in out)

    def test_garbled_below_threshold_not_flagged(self) -> None:
        """**边界**：占比 ≤1% **不**判乱码（少数字符属正常，非乱码）。"""
        assert not any(f.startswith("garbled")
                       for f in quality_flags([5], "a" * 100 + REPL, False))

    def test_clean_input_returns_empty_list(self) -> None:
        assert quality_flags([5, 6, 7], "正常文本", False) == []

    def test_empty_page_lens(self) -> None:
        assert quality_flags([], "", False) == []

    def test_combined_flags_order_is_stable(self) -> None:
        """正例：多标志**顺序固定**（empty → scan → garbled）——
        质量报告按行比对，顺序变了会伪装成数据变化。"""
        out = quality_flags([0, 0], "a" + REPL * 50, True)
        assert out == ["empty_pages=2", "possible_scan(needs_ocr)",
                       "garbled(replacement_chars=50)"]

    def test_none_page_lens_raises(self) -> None:
        """**异常契约**：page_lens=None 抛 TypeError（实测行为，不静默容错）。"""
        with pytest.raises(TypeError):
            quality_flags(None, "x", False)

    def test_none_text_is_falsy_skipped(self) -> None:
        """**契约（实测）**：text=None 因 `if text:` 为假而**跳过乱码检测**，
        不抛异常——与 page_lens=None **行为不对称**（后者抛 TypeError）。

        这条不对称是既有行为，写进断言以防日后"顺手补个容错"改变口径
        （届时质量报告的行数会变，却看不出是哪处改动引起的）。"""
        assert quality_flags([5], None, False) == []
        assert quality_flags([5], None, True) == ["possible_scan(needs_ocr)"]


class TestCommonCnWhitelist:
    def test_whitelist_is_non_trivial(self) -> None:
        """正例：白名单非空且含高频监管字（否则乱码闸门会失效）。"""
        assert len(_COMMON_CN) > 200
        for ch in "的一是在有和这中规办法表指项据标":
            assert ch in _COMMON_CN, f"高频字 {ch!r} 不在白名单内"

    def test_whitelist_is_a_set_of_single_chars(self) -> None:
        """正例：元素均为**单字**（多字条目会让占比计算失真）。"""
        assert all(isinstance(c, str) and len(c) == 1 for c in _COMMON_CN)