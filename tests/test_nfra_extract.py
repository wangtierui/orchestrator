# -*- coding: utf-8 -*-
"""tests/test_nfra_extract.py — nfra 解析层的**纯函数回归**（E4 拆分后固化）。

为什么单独建档：本层五个函数**零外部依赖**（不触网/不落盘/不依赖全局缓存），
是全仓最容易做真回归、也最值得长期锁住的一层。

关键：其中一条锁的是**已修的真实回归**（2026-10-16）——`lxml.html` 未随迁时，
`html_to_text` 的 try 分支抛 NameError ⇒ **静默落到正则兜底** ⇒ `<script>`
内容不被剥离、正文多出换行。函数**不报错**，只安静地给错结果。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from modules.regulatory_scrapers.collectors.nfra.extract import (
    build_detail_url,
    extract_document_no,
    extract_effective_date,
    html_to_text,
    make_summary,
)

HTML_WITH_SCRIPT = (
    "<div class='content'><p>第一条 为规范管理，制定本办法。</p>"
    "<p>第二条 本办法自2024年3月1日起施行。</p>"
    "<span>中国银行保险监督管理委员会〔2024〕3号</span></div>"
    "<script>var x=1;</script><script>var y=2;</script>"
)


class TestHtmlToText:
    def test_strips_script_and_style(self) -> None:
        """正例：`<script>`/`<style>` 内容被移除（lxml 主路径生效的判据）。"""
        out = html_to_text(HTML_WITH_SCRIPT)
        assert "var x=1" not in out and "var y=2" not in out, (
            f"script 未被剥离 —— lxml 主路径可能已退化为正则兜底：{out!r}")

    def test_output_has_no_blank_lines(self) -> None:
        """正例：空行被折叠（`\\n\\n\\n` 不会出现）。"""
        out = html_to_text("<div>\n\n<p>a</p>\n\n<p>b</p>\n\n</div>")
        assert "\n\n" not in out, f"多余空行未折叠：{out!r}"
        assert "a" in out and "b" in out

    def test_keeps_text_of_nested_table(self) -> None:
        """正例：表格单元格文本保留（nfra 详情页正文含表格）。"""
        out = html_to_text(
            "<table><tr><th>标题</th></tr>"
            "<tr><td>银保监发〔2024〕15号</td><td>2024-05-06</td></tr></table>")
        assert "银保监发〔2024〕15号" in out and "2024-05-06" in out

    @pytest.mark.parametrize("bad", ["", None])
    def test_empty_input(self, bad) -> None:
        """边界：空输入返回空串（不抛）。"""
        assert html_to_text(bad) == ""


class TestExtractDocumentNo:
    @pytest.mark.parametrize("text,expected", [
        ("中国银行保险监督管理委员会〔2024〕3号", "中国银行保险监督管理委员会〔2024〕3号"),
        ("发文机关：银保监发〔2024〕15号 标题", "银保监发〔2024〕15号"),
    ])
    def test_extracts(self, text, expected) -> None:
        assert expected in (extract_document_no(text) or "")

    def test_no_number_returns_none(self) -> None:
        """边界：无关文本**返回 None 而非空串**（实测契约）。

        调用方因此必须 `is None` 判断——写成 `if not docno:`虽也成立，但若
        日后有人改成返回 `""`，`is None` 的判据会立刻失败并暴露契约变更。
        """
        assert extract_document_no("没有任何文号的文本") is None

    def test_normalizes_halfwidth_brackets(self) -> None:
        """实测：半角 `[...]` 被归一为全角 `〔〕`（源站的文号书写不统一）。"""
        assert extract_document_no("银保监发[2024]15号") == "银保监发〔2024〕15号"


class TestExtractEffectiveDate:
    def test_extracts_from_shixi_phrase(self) -> None:
        """正例：「自YYYY年M月D日起施行」→ **ISO 形态** `2024-03-01`（实测）。"""
        assert extract_effective_date("本办法自2024年3月1日起施行") == "2024-03-01"

    def test_already_iso_form_accepted(self) -> None:
        """实测：已是 ISO 形态的原文同样被接受。"""
        assert extract_effective_date("自2024-03-01起施行") == "2024-03-01"

    def test_no_date_returns_none(self) -> None:
        """边界：未命中返回 **None**（实测契约，同文号）。"""
        assert extract_effective_date("无生效日期") is None

    def test_wei_xiao_mao_is_not_shixi(self) -> None:
        """边界：「有效期至…」**不是**施行日期，不得误抽（口径纪律）。"""
        assert extract_effective_date("有效期至 2025-12-31") is None


class TestMakeSummary:
    def test_truncates_to_length(self) -> None:
        """正例：长文本截断到 ≤ length（实测 200 字 → 48 字，按句子边界切）。"""
        out = make_summary("标题。" * 200, 50)
        assert 0 < len(out) <= 50

    def test_short_input_gets_full_stop(self) -> None:
        """实测：短摘要会**补句号**（`'短文本'` → `'短文本。'`）——原样返回的断言是错的。"""
        assert make_summary("短文本", 10) == "短文本。"

    def test_empty_input(self) -> None:
        assert make_summary("", 20) == ""


class TestBuildDetailUrl:
    def test_contains_both_ids(self) -> None:
        """正例：URL 同时含 doc_id 与 item_id（两个参数缺一不可）。"""
        url = build_detail_url("12345", "926")
        assert "12345" in url and "926" in url
        assert url.startswith("https://")
