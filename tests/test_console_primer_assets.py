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


def fn(src: str, name: str) -> str:
    """取出一个函数体（与其它契约测试里的同名工具一致，避免跨文件 import）。"""
    i = src.index(f"function {name}(")
    depth, k = 0, src.index("{", i)
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    raise AssertionError(name)


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

class TestFullOfflineAssets:
    """第 1 问：三个官方包是否**全量**且**全部本地**。"""

    def test_primitives_css_present(self) -> None:
        p = os.path.join(ASSETS, "primer-primitives.css")
        assert os.path.exists(p), "缺 primer-primitives.css"
        assert os.path.getsize(p) > 300_000
        css = open(p, encoding="utf-8", errors="replace").read()
        # 基础令牌 + 主题（light/dark 及其无障碍变体都在同一个文件里）
        for token in ("--base-size-4", "--borderRadius-medium",
                      "--control-medium-size", "--text-body-size-medium"):
            assert token in css, token
        assert "[data-color-mode=dark]" in css or "data-dark-theme" in css

    def test_primitives_linked_before_primer(self, h: str) -> None:
        i_prim = h.index('href="primer-primitives.css"')
        i_primer = h.index('href="primer.css"')
        i_style = h.index("\n<style>")
        assert i_prim < i_primer < i_style, "令牌必须最先、组件次之、自有样式最后"

    def test_all_octicons(self, oct_js: str) -> None:
        payload = oct_js[oct_js.index("var ICONS = ") + len("var ICONS = "):]
        payload = payload[:payload.index(";\n  function oct")]
        icons = json.loads(payload)
        assert len(icons) >= 320, f"图标不全：只有 {len(icons)} 个"
        for name in ("zap", "beaker", "law", "mark-github", "sidebar-collapse"):
            assert name in icons, name

    def test_all_assets_local(self, h: str) -> None:
        for f in ("primer-primitives.css", "primer.css", "octicons.js", "favicon.svg"):
            assert os.path.exists(os.path.join(ASSETS, f)), f
            assert f in h, f"{f} 未被页面引用"
        assert not re.findall(r'(?:href|src)="https?://', h)


class TestLoggedInHeader:
    """第 2-3 条：登录态顶栏浅灰、无下边框；Logo 左、控件右。"""

    def test_light_header(self, h: str) -> None:
        assert "--header-bg:#f6f8fa" in h
        gh = h[h.index(".global-header{"):]
        gh = gh[:gh.index("}")]
        assert "background:var(--header-bg)" in gh and "border-bottom:0" in gh

    def test_search_box_is_light(self, h: str) -> None:
        seg = h[h.index(".gsearch input{"):]
        seg = seg[:seg.index("}")]
        assert "background:var(--bg)" in seg and "color:var(--text)" in seg
        assert "rgba(255,255,255" not in seg, "搜索框仍是深色头专用样式"

    def test_controls_right_logo_left(self, h: str) -> None:
        head = h[h.index('<header class="global-header"'):]
        head = head[:head.index("</header>")]
        # 品牌在控件组之前；搜索框在控件组内部（右）
        assert head.index('class="brand"') < head.index('class="hstat"')
        assert head.index('class="hstat"') < head.index('id="gsearchWrap"')
        assert head.index('id="bellBtn"') < head.index('id="themeBtn"') < head.index('id="avatarBtn"')

    def test_hstat_right_aligned(self, h: str) -> None:
        seg = h[h.index(".hstat{"):]
        seg = seg[:seg.index("}")]
        assert "margin-left:auto" in seg


class TestLayoutDetails:
    """第 4-6 条：KPI 白底 / 折叠在左下 / 密度用图标。"""

    def test_grid_children_have_surface(self, h: str) -> None:
        assert ".grid>*{background:var(--bg)}" in h

    def test_collapse_at_sidebar_bottom(self, h: str) -> None:
        rail = h[h.index(".rail{"):]
        rail = rail[:rail.index("}")]
        assert "flex-direction:column" in rail
        foot = h[h.index(".rail-foot{"):]
        foot = foot[:foot.index("}")]
        assert "margin-top:auto" in foot

    def test_density_is_icon(self, h: str) -> None:
        body = fn(h, "lvRender")
        seg = body[body.index('class="density"'):]
        seg = seg[:seg.index("</span>")]
        assert 'ico("rows"' in seg and 'ico("list-unordered"' in seg
        assert "宽松</button>" not in seg and "紧凑</button>" not in seg
        assert "aria-pressed" in seg
