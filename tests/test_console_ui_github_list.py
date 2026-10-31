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
        """批 128：表头只有一行工具条；**没有列名**、没有搜索框、没有"筛选/字段"按钮。"""
        seg = h             # fn() 会被模板字面量里的 } 截断；这类判据直接查整页
        assert 'class="lv-bar lv-headbar"' in seg
        assert '<th colspan="3"><div class="lv-bar lv-headbar">' in seg, \
            "表头必须是**一个**跨 3 列的单元格（工具条），不再有列名格"
        assert 'data-lvtab' in seg              # 状态页签在表头里
        assert 'data-lvf=' in seg               # 字段级下拉
        assert 'data-lvdensity="wide"' in seg
        assert 'data-lvsavecur' in seg          # 保存视图内嵌
        assert 'id="lvq"' not in seg
        assert 'id="lvFilterBtn"' not in seg and 'id="lvFieldsBtn"' not in seg

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
        compact = h[h.index("#view.lv-compact .lv-primary{"):]
        compact = compact[:compact.index("}")]
        assert "display:flex" in compact, "紧凑模式必须一行排布"
        assert "#view.lv-compact .lv-primary .lv-meta" in h

    def test_header_cells_aligned(self, h: str) -> None:
        """表头与单元格同一套内边距 ⇒ 文字左边缘对齐。"""
        th = h[h.index(".lv-headrow .lv-th{"):]
        th = th[:th.index("}")]
        td = h[h.index(".lv-table td{"):]
        td = td[:td.index("}")]
        assert "padding:6px 8px" in th and "padding:6px 8px" in td

    def test_density_buttons_in_header(self, h: str) -> None:
        head = h
        assert 'data-lvdensity="wide"' in head and 'ico("rows"' in head
        assert "保存视图" in head


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
        # 批 128：图标按钮被 flex 压成 20px（真 bug），现在必须 flex:none
        assert ".iconbtn{width:32px;height:32px;flex:none" in h
        assert ".avatar{width:32px;height:32px" in h
        assert 'setIco("#brandIco", "mark-github", 32)' in h, "logo 应用 32px 图形"

    def test_badge_radius_matches_search(self, h: str) -> None:
        badge = h[h.index(".hstat .badge{"):]
        badge = badge[:badge.index("}")]
        assert "border-radius:6px" in badge

    def test_list_heights(self, h: str) -> None:
        assert ".lv-headrow th{height:48px" in h
        # 批 145/156：列表行高由 tr 的 min-height 控制（td 不再定高）
        assert "min-height:65px" in h
        assert "#view.lv-compact .lv-table tr.lv-row{min-height:38px" in h

    def test_sidebar_item_height(self, h: str) -> None:
        # 注意 `body.rail-off .rail .sideitem{...}` 也含该子串，必须锚到行首
        i = h.index("\n.rail .sideitem{")
        seg = h[i:i + 260]
        assert "height:32px" in seg

    def test_four_px_lines_unified(self, h: str) -> None:
        # 批 156：面板（.fpop）按 GitHub 无左侧竖线，只剩侧栏与搜索下拉两处
        for sel in (".rail .sideitem.on::before", ".spop .it:hover::before"):
            seg = h[h.index(sel):]
            seg = seg[:seg.index("}")]
            assert "width:4px" in seg, sel

    def test_radius_unified_6px(self, h: str) -> None:
        # 批 137：pill/tag 自研已删，圆角规则收敛到 .lv-badge/.Counter/.Label
        assert ".lv-badge,.Counter,.Label{border-radius:6px}" in h
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

class TestSingleTableImplementation:
    """第 1、2 条：全局只有一套表结构，且不再有运行时转换器。"""

    def test_no_bare_table(self, h: str) -> None:
        """除 `.lv-table` 外不允许出现任何 <table>（裸表即"第二套实现"）。"""
        total = h.count("<table")
        lv = h.count('<table class="lv-table"')
        assert total == lv, f"还有 {total - lv} 处裸 <table>"

    def test_converter_removed(self, h: str) -> None:
        """运行时转换器已删除，不允许以任何名字复活。"""
        assert "enhanceLegacyTables" not in h
        assert "openTableFilter" not in h
        assert "applyTableFilters" not in h

    def test_grid_rows_used_everywhere(self, h: str) -> None:
        """各页列表都用 .lv-gtable + .lv-row + .lv-cell。"""
        assert h.count("lv-gtable") >= 8
        assert "function openGridFilter(" in h
        assert "function applyGridFilters(" in h

class TestListControlsContract:
    """批 156/157：列表工具的交互契约（禁用态 / token 语法 / Truncate 结构）。"""

    def test_token_grammar_extended(self, h: str) -> None:
        """筛选框支持 主题域/状态/发文字号/编号/排序 与 升序·降序。"""
        for key in ("\"主题域\": \"theme\"", "\"状态\": \"timeliness\"",
                    "\"发文字号\": \"q_docno\"", "\"编号\": \"q_rfn\"",
                    "\"排序\": \"sort\""):
            assert key in h, f"缺少 token 键 {key}"

        assert "const LV_SORT_ALIAS" in h, "缺少排序别名表"
        assert '"文号": "document_number"' in h
        assert 'if (key === "sort") st.sort = LV_SORT_ALIAS[val] || val' in h, \
            "排序 token 未映射"
        assert 'if (w === "降序" || w === "倒序") st.desc = true' in h, "缺少降序 token"
        assert 'else if (w === "升序" || w === "正序") st.desc = false' in h, "缺少升序 token"

    def test_save_view_disabled_when_clean(self, h: str) -> None:
        """没有筛选/非默认排序时，保存视图按钮应禁用。"""
        assert "const dirty = act.length > 0" in h
        assert 'String(st.sort || "") !== String(primaryFd.sort || "title")' in h
        assert "|| !!st.desc" in h, "降序也应算可保存状态"
        assert 'data-lvsavecur="1"' in h
        assert '${dirty ? "" : "disabled aria-disabled=\\"true\\""}' in h, \
            "保存按钮缺少条件禁用"
        assert ".lv-headbar .btn:disabled" in h, "缺少禁用态样式"

    def test_density_disabled_when_empty(self, h: str) -> None:
        """列表为空时，密度切换两个按钮都应禁用。"""
        guard = '${items.length ? "" : "disabled aria-disabled=\\"true\\""}'
        assert h.count(guard) == 2, "密度按钮未按空列表禁用（应为 2 个）"

    def test_truncate_structure(self, h: str) -> None:
        """标题 / 选项 / 详情统一用 Primer .Truncate 结构，不再手写截断。"""
        assert '<a class="lv-title Truncate"' in h, "列表标题未用 Truncate"
        assert '<span class="Truncate-text">' in h
        assert ".Truncate{display:inline-flex;max-width:100%;min-width:0;overflow:hidden}" in h
        assert ".Truncate-text" in h and "text-overflow:ellipsis" in h
        # 标题规则里不应再有手写 text-overflow（交给 Truncate-text）
        seg = h[h.index(".lv-primary .lv-title{"):]
        seg = seg[:seg.index("}")]
        assert "text-overflow" not in seg, "标题仍在手写截断"

    def test_dirty_defined_after_primaryfd(self, h: str) -> None:
        """dirty 依赖 primaryFd —— 顺序错了会 TDZ 报错、整页不渲染（批 157 踩过）。"""
        i_primary = h.index("const primaryFd = primary")
        i_dirty = h.index("const dirty = act.length > 0")
        assert i_primary < i_dirty, "dirty 必须在 primaryFd 之后定义"

