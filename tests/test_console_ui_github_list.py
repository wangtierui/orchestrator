# -*- coding: utf-8 -*-
"""tests/test_console_ui_github_list.py — 复刻 GitHub 列表与全局字体（第一百二十六批）。

对应用户本轮可判定的条款：

  字体：正文字体栈 / 代码字体栈 / 14px 1.5 / 禁止外部 Web Font
  列表：工具栏**嵌入 <thead>**；三列（选择/状态/事项）；宽松两行、紧凑一行；
        表头与单元格对齐；删除"操作"列与重复的"打开本地文件"
  其他：第二层与第一层同色；总览无左栏；favicon = GitHub 标记；
        按钮改用 Primer `.btn`（只保留"主操作绿色"覆盖）
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")


@pytest.fixture(scope="module")
def h() -> str:
    return open(HTML, encoding="utf-8").read()


def fn(src: str, name: str) -> str:
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


class TestFonts:
    """字体：用户给的两条栈，逐字一致。"""

    SANS = ('-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,'
            ' sans-serif, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "微软雅黑",'
            ' sans-serif')
    MONO = ('ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,'
            ' "Liberation Mono", monospace')

    def _squash(self, s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    def test_sans_stack(self, h: str) -> None:
        root = h[h.index(":root{"):h.index("}", h.index(":root{"))]
        seg = root[root.index("--font-sans:"):]
        seg = seg[:seg.index(";")]
        assert self._squash(self.SANS) in self._squash(seg)

    def test_mono_stack(self, h: str) -> None:
        root = h[h.index(":root{"):h.index("}", h.index(":root{"))]
        seg = root[root.index("--mono:"):]
        seg = seg[:seg.index(";")]
        assert self._squash(self.MONO) in self._squash(seg)

    def test_body_size_and_line_height(self, h: str) -> None:
        # 页面里有多个 body 规则（背景/字体分开写），按"含 font-family"的那条判
        assert "body{font-family:var(--font-sans);font-size:14px;line-height:1.5" in h

    def test_no_web_fonts(self, h: str) -> None:
        assert "@font-face" not in h
        assert "fonts.googleapis" not in h and "fonts.gstatic" not in h
        assert not re.findall(r'(?:href|src)="https?://', h)


class TestListLayout:
    """列表：工具栏在表头内、三列、两种密度。"""

    def test_toolbar_inside_header_row(self, h: str) -> None:
        """批 127：控件组与列名在**同一行表头**（不是表头之上另起一行）。"""
        body = fn(h, "lvRender")
        i_head = body.index("<thead>")
        i_end = body.index("</thead>")
        seg = body[i_head:i_end]
        assert 'class="lv-headctl"' in seg, "控件组必须在表头行内"
        assert seg.count("<tr") == 1, "表头只能有一行（否则控件又跑到表头上面）"
        assert 'id="lvq"' in seg and 'data-lvdensity="wide"' in seg

    def test_three_columns(self, h: str) -> None:
        body = fn(h, "lvRender")
        for cell in ("lv-selcell", "lv-stcell", "lv-primary"):
            assert cell in body
        assert "lv-actcell" not in body, "操作列已删除（与元数据「关系」重复）"

    def test_no_duplicate_local_file_link(self, h: str) -> None:
        """公司制度：标题即链接，不再有"打开本地文件"这一列。"""
        assert 'label: "本地文件"' not in h
        body = fn(h, "lvRender")
        # 只看**渲染出来的链接文案**（注释里解释"为什么删掉它"不算）
        assert ">打开本地文件</a>" not in body
        # 标题链接仍指向本地文件接口
        assert "it.local_path" in body and "/api/local/file?path=" in body

    def test_wide_is_two_lines_compact_one_line(self, h: str) -> None:
        wide_title = h[h.index(".lv-primary .lv-title{"):]
        wide_title = wide_title[:wide_title.index("}")]
        assert "display:block" in wide_title
        wide_meta = h[h.index(".lv-primary .lv-meta{"):]
        wide_meta = wide_meta[:wide_meta.index("}")]
        assert "display:flex" in wide_meta
        compact = h[h.index("body.lv-compact .lv-primary{"):]
        compact = compact[:compact.index("}")]
        assert "display:flex" in compact, "紧凑模式必须一行排布"
        assert "body.lv-compact .lv-primary .lv-meta" in h

    def test_header_cells_aligned(self, h: str) -> None:
        """表头与单元格同一套内边距 ⇒ 文字左边缘对齐。"""
        th = h[h.index(".lv-headrow .lv-th{"):]
        th = th[:th.index("}")]
        td = h[h.index(".lv-table td{"):]
        td = td[:td.index("}")]
        assert "padding:6px 8px" in th and "padding:6px 8px" in td

    def test_density_buttons_in_header(self, h: str) -> None:
        body = fn(h, "lvRender")
        head = body[body.index("<thead>"):body.index("</thead>")]
        assert 'data-lvdensity="wide"' in head and 'ico("rows"' in head


class TestChrome:
    """第二层同色 / 总览无左栏 / favicon / 按钮用 Primer。"""

    def test_tabs_same_color_as_header(self, h: str) -> None:
        rt = h[h.index(".repo-tabs{"):]
        rt = rt[:rt.index("}")]
        assert "background:var(--bg-subtle)" in rt
        gh = h[h.index(".global-header{"):]
        gh = gh[:gh.index("}")]
        assert "background:var(--header-bg)" in gh

    def test_overview_hides_sidebar(self, h: str) -> None:
        body = fn(h, "paintRail")
        assert 'if (CUR === "overview")' in body
        seg = body[body.index('if (CUR === "overview")'):]
        seg = seg[:seg.index("}") + 1]
        assert "rail.hidden = true" in seg

    def test_favicon_is_github_mark(self) -> None:
        svg = open(os.path.join(ROOT, "web", "assets", "favicon.svg"),
                   encoding="utf-8").read()
        assert svg.lstrip().startswith("<svg") and 'viewBox="0 0 24 24"' in svg
        # 官方 mark-github(24) 的路径前缀（与本地 octicons.js 同源）
        assert svg.count("<path") == 1 and "M12.5.75" in svg

    def test_buttons_use_primer(self, h: str) -> None:
        assert ".btn.btn-primary{background:var(--success)" in h
        # 自研的按钮基础样式已删除（改由 Primer `.btn` 提供）
        assert "\n.btn{display:inline-flex" not in h
        assert 'class="btn ghost"' not in h

    def test_no_redundant_bottom_borders(self, h: str) -> None:
        assert ".lv-bar{border-bottom:0" in h
        assert ".lv-foot{" in h

class TestUnifiedMetrics:
    """批 127：顶栏 32 / 表头 48 / 行 65·38 / 侧栏项 32 / 4px 线 / 6px 圆角。"""

    def test_header_controls_32(self, h: str) -> None:
        assert ".gsearch input{width:100%;height:32px" in h
        # 页面上有两处 .brand（早期一套），按"达到最终形态那条"的特征串判定
        assert "color:var(--header-fg);height:32px}" in h, "品牌区必须 32 高"
        assert ".iconbtn{width:32px;height:32px" in h
        assert ".avatar{width:32px;height:32px" in h
        assert 'setIco("#brandIco", "mark-github", 24)' in h, "logo 应用 24px 图形"

    def test_badge_radius_matches_search(self, h: str) -> None:
        badge = h[h.index(".hstat .badge{"):]
        badge = badge[:badge.index("}")]
        assert "border-radius:6px" in badge

    def test_list_heights(self, h: str) -> None:
        assert ".lv-headrow th{height:48px" in h
        assert ".lv-table td{height:65px}" in h
        assert "body.lv-compact .lv-table td{height:38px}" in h

    def test_sidebar_item_height(self, h: str) -> None:
        # 注意 `body.rail-off .rail .sideitem{...}` 也含该子串，必须锚到行首
        i = h.index("\n.rail .sideitem{")
        seg = h[i:i + 260]
        assert "height:32px" in seg

    def test_four_px_lines_unified(self, h: str) -> None:
        for sel in (".rail .sideitem.on::before", ".spop .it:hover::before",
                    ".fpop .opt:hover::before"):
            seg = h[h.index(sel):]
            seg = seg[:seg.index("}")]
            assert "width:4px" in seg, sel

    def test_radius_unified_6px(self, h: str) -> None:
        assert ".tag,.pill,.lv-badge,.Counter{border-radius:6px}" in h
        assert "--r-md:6px" in h


class TestNoPageTitles:
    """第 2-3 条：删页面标题文字 + 标题区/侧栏树上下边框。"""

    @pytest.mark.parametrize("title", [
        "待办闭环台", "关系图谱", "监管统计（EAST）", "文档检索", "提交流",
        "留言板", "全链数据流（分层 DAG）",
    ])
    def test_card_title_removed(self, h: str, title: str) -> None:
        assert f'card("{title}"' not in h, f"{title} 仍是卡片标题"

    def test_list_page_has_no_h1(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert "<h1>" not in body

    def test_ph_and_rail_borders_removed(self, h: str) -> None:
        ph = h[h.index(".ph{"):]
        ph = ph[:ph.index("}")]
        assert "border-bottom:0" in ph
        foot = h[h.index(".rail-foot{"):]
        foot = foot[:foot.index("}")]
        assert "border-top:0" in foot


class TestGlobalFormDispatch:
    """第 6 条：有没有"全局风格调度"，还是各页特例内联。"""

    def test_global_form_control_rule(self, h: str) -> None:
        assert "表单控件基线（批 127）" in h
        i = h.index('input:not([type="checkbox"])')
        seg = h[i:i + 420]
        assert "height:32px" in seg and "border-radius:6px" in seg

    def test_no_dashed_form_box(self, h: str) -> None:
        assert 'border-style:dashed' not in h

    def test_flow_colors_tokenized(self, h: str) -> None:
        assert "#E6F6EF" not in h and "#FFF6E5" not in h and "#FDECEA" not in h


class TestTabsAndOverview:
    """第 7 / 8 / 10 条。"""

    def test_tabs_have_icons(self, h: str) -> None:
        body = fn(h, "buildTabs")
        assert 'class="tab-ico"' in body and "ico(PAGE_GLYPH[x.id]" in body

    def test_grid_children_white_at_end(self, h: str) -> None:
        # 覆盖层必须在 `.card{background:transparent}` **之后**才生效
        i_card = h.index(".card{background:transparent")
        i_fix = h.index(".grid > .card, .grid > .section, .grid > *{background:var(--bg)}")
        assert i_fix > i_card, "白底覆盖层必须写在透明卡片规则之后"

    def test_overview_padding_220(self, h: str) -> None:
        assert "body.no-rail .main-content{padding-left:220px;padding-right:220px}" in h
        assert 'document.body.classList.toggle("no-rail", CUR === "overview")' in h
