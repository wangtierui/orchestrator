# -*- coding: utf-8 -*-
"""tests/test_console_primer_assets.py — 官方资源本地化接入（第一百二十四批）。

用户规范里**可判定**的部分：

  一 资源：Primer CSS / Octicons 必须**本地**（运行时零外链），且在 `<style>` 之前引入
  二 图标：界面不得再用 emoji / 自制符号（⋮ ⌕ ☰ × ▾ 之类），全部换成 Octicons
  三 骨架：第一层顶栏 52px **无**下边框；第二层 48px 带 1px；主内容唯一滚动
  四 交互：侧栏/筛选面板的强调竖线 **4px**；搜索原地下拉；明暗主题可切
"""

from __future__ import annotations

import json
import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "web", "assets")
HTML = os.path.join(ASSETS, "index.html")


@pytest.fixture(scope="module")
def h() -> str:
    return open(HTML, encoding="utf-8").read()


@pytest.fixture(scope="module")
def oct_js() -> str:
    return open(os.path.join(ASSETS, "octicons.js"), encoding="utf-8").read()


class TestVendorAssets:
    """一、官方资源必须落地为本地文件，且被正确引用。"""

    def test_files_exist(self) -> None:
        css = os.path.join(ASSETS, "primer.css")
        js = os.path.join(ASSETS, "octicons.js")
        assert os.path.exists(css) and os.path.getsize(css) > 300_000, "primer.css 缺失或异常"
        assert os.path.exists(js) and os.path.getsize(js) > 10_000, "octicons.js 缺失或异常"

    def test_primer_is_official_copy(self) -> None:
        css = open(os.path.join(ASSETS, "primer.css"), encoding="utf-8", errors="replace").read(400)
        assert "Primer CSS" in css and "MIT license" in css
        assert "--header-bgColor" in open(
            os.path.join(ASSETS, "primer.css"), encoding="utf-8", errors="replace").read()

    def test_linked_before_inline_style(self, h: str) -> None:
        link = h.index('href="primer.css"')
        style = h.index("\n<style>")
        assert link < style, "primer.css 必须在自有 <style> 之前（否则官方基底会覆盖我们）"

    def test_octicons_loaded_before_main_script(self, h: str) -> None:
        oc = h.index('src="octicons.js"')
        main = h.index('<script>\n"use strict"')
        assert 0 < oc < main

    def test_zero_external_urls(self, h: str) -> None:
        ext = re.findall(r'(?:href|src)="(https?://[^"]+)"', h)
        assert not ext, f"页面仍在请求外网资源：{ext[:3]}"

    def test_server_can_serve_them(self) -> None:
        """静态资源走 `web/server._asset`（按 basename 读盘）——直接验证能读到。"""
        from web import server

        body, ctype = server._asset("primer.css")
        assert len(body) > 300_000 and "text/css" in ctype
        body2, ctype2 = server._asset("octicons.js")
        assert len(body2) > 10_000 and "javascript" in ctype2


class TestOcticons:
    """二、图标必须是官方 Octicons，且界面不再有符号图标。"""

    REQUIRED = ("mark-github", "search", "x", "check", "chevron-down",
                "kebab-horizontal", "issue-opened", "issue-closed",
                "sidebar-collapse", "sidebar-expand", "triangle-up", "triangle-down",
                "copy", "download", "law", "book", "graph", "table")

    def test_required_icons_present(self, oct_js: str) -> None:
        payload = oct_js[oct_js.index("var ICONS = ") + len("var ICONS = "):]
        payload = payload[:payload.index(";\n  function oct")]
        icons = json.loads(payload)
        missing = [n for n in self.REQUIRED if n not in icons]
        assert not missing, f"octicons.js 缺图标：{missing}"

    def test_helper_defined(self, oct_js: str, h: str) -> None:
        assert "function oct(name, size, cls, label)" in oct_js
        assert "function ico(name, size, label)" in h

    def test_page_glyphs_are_octicons(self, h: str, oct_js: str) -> None:
        seg = h[h.index("const PAGE_GLYPH = {"):]
        seg = seg[:seg.index("};")]
        names = re.findall(r'([a-z]+):\s*"([a-z-]+)"', seg)
        assert len(names) >= 10
        for _page, icon in names:
            assert f'"{icon}":' in oct_js, f"页面图标 {icon} 不在 octicons.js 里"

    @pytest.mark.parametrize("glyph", ["⋮", "⌕", "☰", "⇤", "⇥", "▾", "▴", "＋"])
    def test_no_symbol_icons_left(self, h: str, glyph: str) -> None:
        # 只允许出现在注释里（解释历史/规范），不允许出现在渲染字符串里
        bad = [ln.strip()[:80] for ln in h.splitlines()
               if glyph in ln and not ln.strip().startswith(("*", "/*", "//"))]
        assert not bad, f"仍在用符号当图标：{bad[:2]}"

    def test_header_icons_filled_by_js(self, h: str) -> None:
        for sel in ("#brandIco", "#railOpenIco", "#searchIco", "#moreIco", "#themeIco"):
            assert sel in h, sel
        assert "function initChrome(" in h


class TestSkeletonRules:
    """三、双层顶栏与滚动容器。"""

    def test_first_layer_has_no_border(self, h: str) -> None:
        gh = h[h.index(".global-header{"):]
        gh = gh[:gh.index("}")]
        assert "border-bottom:0" in gh, "第一层顶栏不应有下边框"

    def test_second_layer_has_border(self, h: str) -> None:
        rt = h[h.index(".repo-tabs{"):]
        rt = rt[:rt.index("}")]
        assert "border-bottom:1px solid var(--border)" in rt

    def test_unique_scroller(self, h: str) -> None:
        assert "body{overflow:hidden" in h
        mc = h[h.index(".main-content{"):]
        mc = mc[:mc.index("}")]
        assert "overflow-y:auto" in mc


class TestInteractions:
    """四、强调线 4px / 搜索原地下拉 / 明暗主题。"""

    def test_active_bar_is_4px(self, h: str) -> None:
        seg = h[h.index(".rail .sideitem.on::before{"):]
        seg = seg[:seg.index("}")]
        assert "width:4px" in seg

    def test_inline_search_popover(self, h: str) -> None:
        body = h[h.index("function openSearchPop("):]
        body = body[:body.index("function ", 10)]
        assert "spop" in body and "pal-mask" not in body

    def test_theme_toggle(self, h: str) -> None:
        assert 'data-color-mode="light"' in h
        assert "function applyTheme(" in h
        assert 'html[data-color-mode="dark"]{' in h
        assert 'localStorage.setItem("theme"' in h
        assert 'id="themeBtn"' in h

    def test_dark_tokens_cover_surfaces(self, h: str) -> None:
        seg = h[h.index('html[data-color-mode="dark"]{'):]
        seg = seg[:seg.index("}")]
        for k in ("--bg:", "--bg-subtle:", "--border:", "--text:", "--text-muted:"):
            assert k in seg, k


class TestNoRegression:
    """五、后端契约未动：路由与接口仍在，页面仍引用同一批端点。"""

    def test_endpoints_present(self, h: str) -> None:
        for p in ("/api/records", "/api/rules", "/api/east", "/api/relations",
                  "/api/worklist", "/api/regstats", "/api/auth/login"):
            assert p in h, p

    def test_no_new_routes_added(self) -> None:
        """本批只加静态文件，不改路由表：`web/api.py` 里不能出现 v2 之类新前缀。"""
        src = open(os.path.join(ROOT, "web", "api.py"), encoding="utf-8").read()
        assert "/api/v2" not in src
