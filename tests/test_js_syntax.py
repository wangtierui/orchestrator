# -*- coding: utf-8 -*-
"""tests/test_js_syntax.py — 内联 JS 语法守卫（防"全站静默瘫痪"）。

背景（2026-10-19 真实事故）
---------------------------
在 `web/assets/index.html` 的**模板字符串内部**插入了一段 `//` 注释，
而注释里用反引号标注字段名（`` `c.consistent` ``）⇒ **第一个反引号提前闭合了
模板字符串** ⇒ 后面的内容被当成代码 ⇒ `Unexpected identifier` ⇒
**整段 `<script>` 不执行**。

症状极具误导性（这是本测试存在的最强理由）：
  · 页面停在 HTML 初始态：`加载中…` + `SSE 连接中…`
  · 而服务端一切正常：HTTP 200、所有 API 返回正常、`/api/events` 有 `hello`/`ping`
  ⇒ 极易被判成"后端 / SSE 故障"，实际是**纯前端语法错误**。

守卫三层
--------
1. **当前文件必须无语法错误**（真解析器 esprima）；
2. **失态对照**：构造"模板内注释含反引号"的片段，断言解析器**能抓到**
   ——没有这条，"检查通过"可能只是因为检查器不工作；
3. **后端可辨**：esprima 不可用时报告必须标明 `backend="lexical"`
   并**告警不支持嵌套模板**（不能静默降级成"看起来通过"）。
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 无需注入 `sys.path`：`tests/conftest.py` 已 `bootstrap("all", include_tools=True)`
# （唯一引导点；`gate_import_bootstrap` 的基线**只减不增**，测试内自注入会顶破基线）。

from tools import js_syntax_lint as L

INDEX = os.path.join(ROOT, "web", "assets", "index.html")


def _esprima_ok() -> bool:
    try:
        import esprima  # noqa: F401
    except ImportError:
        return False
    return True


class TestCurrentFile:
    def test_index_has_no_syntax_error(self) -> None:
        """**核心断言**：当前页面内联 JS 必须能通过真解析器。

        这条失败意味着用户会看到"白屏 + SSE 连接中"，
        而所有后端检查都是绿的 —— 所以必须在这里拦住。
        """
        r = L.lint_file(INDEX)
        assert r["ok"], (
            "内联 JS 有语法错误（页面会停在初始态，服务端却全绿）：\n"
            + "\n".join(f"  L{i['line']}:C{i['col']} {i['detail']}"
                        for i in r["issues"]))

    def test_uses_real_parser_when_available(self) -> None:
        """后端可辨：esprima 可用时必须用它（词法兜底会误报嵌套模板）。"""
        r = L.lint_file(INDEX)
        if _esprima_ok():
            assert r["backend"] == "esprima", (
                f"esprima 可用却用了 {r['backend']} —— 词法兜底不支持嵌套模板，"
                "会产生误报（实测曾把 3 个历史版本全判 FAIL）")

    def test_reports_at_least_one_script(self) -> None:
        """正例：必须真的找到内联脚本（找不到＝检查空转）。"""
        r = L.lint_file(INDEX)
        assert r["scripts"] >= 1, "未提取到任何内联 <script>（提取逻辑失效）"


class TestDetectsRealFailures:
    """**失态对照**：这些片段必须被判定为有问题。"""

    def test_backtick_in_template_comment_closes_template(self) -> None:
        """复现 2026-10-19 事故：模板字符串内的注释含反引号。

        `` return `<div>
             // 读 `x`
           </div>`; ``
        第一个反引号提前闭合 ⇒ 后面变成代码 ⇒ 语法错误。
        """
        bad = (
            "function f(){\n"
            "  return `<div>\n"
            "    // 读 `x` 字段\n"
            "  </div>`;\n"
            "}\n"
        )
        issues = L._parse_with_esprima(bad, 1)
        assert issues is not None, "esprima 不可用（本用例需真解析器）"
        assert issues, "**未能检出**模板内注释反引号导致的语法错误——守卫失效"

    def test_broken_bracket_detected(self) -> None:
        """正例：括号不配对可检出。"""
        issues = L._parse_with_esprima("function f(){ return 1;\n", 1)
        assert issues, "未检出未闭合花括号"

    def test_valid_nested_template_passes(self) -> None:
        """**反向**：合法的三层嵌套模板**不应**被误报。

        本仓真实存在（`viewPublish` 的 `` `${cond?`<span>${x}</span>`:...}` ``），
        词法兜底会误报 ⇒ 这条钉住"必须用真解析器"。
        """
        code = (
            "function f(a){\n"
            "  return `<td>${a===1?`<b>${a}</b>`:'x'}</td>`;\n"
            "}\n"
        )
        issues = L._parse_with_esprima(code, 1)
        assert issues is not None
        assert issues == [], f"合法嵌套模板被误报：{issues}"

    def test_es2020_syntax_is_downgraded_not_flagged(self) -> None:
        """**反向**：ES2020 语法（`??` / `?.`）不应被误报为语法错误。

        esprima 4.0.1 只到 ES2017 ⇒ 工具做**等长降级**后再解析；
        等长保证行列号不变（定位不失真）。
        """
        code = "function f(a){ return (a??1)?.b; }\n"
        issues = L._parse_with_esprima(code, 1)
        assert issues is not None
        assert issues == [], f"ES2020 语法被误报：{issues}"

    def test_downgrade_is_length_preserving(self) -> None:
        """**关键性质**：降级替换必须**等长**（否则行列号错位、定位失真）。"""
        src = "a??b?.c?.[0]?.(1)"
        out, n = L._downgrade_es2020(src)
        assert len(out) == len(src), f"降级改变了长度：{len(src)} -> {len(out)}"
        assert n >= 3, f"替换次数异常：{n}"


class TestLexicalFallback:
    def test_lexical_detects_unterminated_string(self) -> None:
        """兜底后端至少能抓未闭合字符串（esprima 不可用时的最低保障）。"""
        issues = L._scan_lexical("var a = 'x;\nvar b = 1;\n", 1)
        assert any(i["kind"] == "unterminated_string" for i in issues)

    def test_lexical_detects_unclosed_bracket(self) -> None:
        issues = L._scan_lexical("function f(){\n  return 1;\n", 1)
        assert any(i["kind"] == "unclosed_bracket" for i in issues)


class TestScheduleIntegration:
    def test_lint_runs_in_ci_scope(self) -> None:
        """正例：工具已登记进 `tools/_manifest.json`（J1：新增脚本必须登记）。"""
        import json

        m = json.load(open(os.path.join(ROOT, "tools", "_manifest.json"),
                           encoding="utf-8"))
        names = {e.get("file") for e in (m.get("entries") or [])}
        assert "js_syntax_lint.py" in names, f"未登记（当前 {len(names)} 条）"
