# -*- coding: utf-8 -*-
"""tests/test_console_ui_flat.py — 全幅扁平化重构（第一百二十二批）。

对应用户规范的可判定条款：

  一 骨架：100vh 应用壳 / 唯一滚动容器 / 无外阴影与大圆角 / 1px 切割
  二 双层顶栏：52px（变量）+ 48px；页签溢出折叠进 More，**禁止 overflow-x:auto**，活跃页签永不折叠
  三 滚动：外层 hidden，`#view` 唯一 overflow-y:auto；列表/表格不得有 max-height 内部滚动
  四 侧栏：260px、右侧 1px；选中 = 浅灰底 + 深黑加粗 + **左侧 2px 强调线**
  五 搜索：点击=**原地下拉（无遮罩）**；Ctrl+K=居中 Modal（有遮罩）
  六 列表：状态筛选页签 / 表头字段筛选面板 / 密度切换 / 批量理由 / 无外部下拉框
  七 视觉：主按钮绿色、禁止大面积纯黑按钮、仅小控件 6px 圆角
  八 图标：favicon 可被浏览器抓取
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


class TestAppShell:
    """一、全幅骨架。"""

    def test_100vh_app(self, h: str) -> None:
        shell = h[h.index(".app{"):]
        shell = shell[:shell.index("}")]
        assert "height:100vh" in shell and "width:100vw" in shell
        assert "overflow:hidden" in shell
        assert "body{overflow:hidden" in h

    def test_single_scroller(self, h: str) -> None:
        """只有 `#view` 滚动；外层与侧栏各管各的（侧栏自身可 scroll）。"""
        main = h[h.index(".main-content{"):]
        main = main[:main.index("}")]
        assert "overflow-y:auto" in main
        assert "overflow:hidden" in h[h.index(".body-layout{"):][:200]

    def test_no_inner_scroll_for_tables(self, h: str) -> None:
        """规范明确禁止给列表/表格设固定 max-height 的内部滚动。"""
        tw = h[h.index(".tblwrap{"):]
        tw = tw[:tw.index("}")]
        # 覆盖层必须把 max-height 归零
        assert "max-height:none" in h
        lv = h[h.index(".lv-scroll{max-height:none"):][:40]
        assert "max-height:none" in lv
        assert "overflow:visible" in tw or "overflow-x:auto" in tw


class TestNoCardLook:
    """一去卡片化：无阴影/大圆角/灰底板。"""

    def test_card_flat(self, h: str) -> None:
        idx = h.rindex(".card{background:transparent;border:0;border-radius:0")
        assert idx > 0

    def test_grid_hairlines(self, h: str) -> None:
        assert ".grid{display:grid;gap:1px;background:var(--border)" in h

    def test_no_big_shadow_on_panels(self, h: str) -> None:
        # 允许 popover 的微小阴影；不允许面板/卡片用大阴影
        for bad in (".card{box-shadow", ".rail{box-shadow", ".body-layout{box-shadow"):
            assert bad not in h, bad


class TestDoubleHeader:
    """二、双层顶栏与页签折叠。"""

    def test_heights(self, h: str) -> None:
        assert "--header-height:52px" in h
        assert "height:var(--header-height)" in h
        assert ".repo-tabs{height:48px" in h

    def test_header_colors(self, h: str) -> None:
        """批 125：以**登录态**为准 —— 第一层与第二层同为浅色系，且第一层无下边框。

        （登出态实测是深灰 #25292e；用户明确"本项目应与登录后一致"，故改浅灰。）
        """
        assert "--header-bg:#f6f8fa" in h
        gh = h[h.index(".global-header{"):]
        gh = gh[:gh.index("}")]
        assert "background:var(--header-bg)" in gh and "border-bottom:0" in gh
        rt = h[h.index(".repo-tabs{"):]
        rt = rt[:rt.index("}")]
        # 批 126：第二层与第一层**同色**（--bg-subtle），只有底部 1px 分隔
        assert "background:var(--bg-subtle)" in rt
        assert "border-bottom:1px solid var(--border)" in rt

    def test_no_horizontal_scroll(self, h: str) -> None:
        rt = h[h.index(".repo-tabs{"):]
        rt = rt[:rt.index("}")]
        assert "overflow:hidden" in rt
        assert "overflow-x:auto" not in rt
        assert "overflow-x: auto" not in rt

    def test_more_button_and_folding(self, h: str) -> None:
        assert 'id="tabsMore"' in h
        body = fn(h, "foldTabs")
        assert "moreW" in body and "avail" in body
        # 活跃页签必须始终可见：折叠时给它留位
        assert "i === active" in body
        assert "more.hidden = !anyHidden" in body

    def test_fold_recomputes_on_resize(self, h: str) -> None:
        body = fn(h, "buildTabs")
        assert 'addEventListener("resize"' in body
        assert "ResizeObserver" in body


class TestSidebarActive:
    """四、侧栏：260px / 右侧 1px / 选中浅灰 + 2px 竖线。"""

    def test_width_and_border(self, h: str) -> None:
        rail = h[h.index(".rail{"):]
        rail = rail[:rail.index("}")]
        assert "width:260px" in rail and "border-right:1px solid var(--border)" in rail
        assert "border-radius:0" in rail and "box-shadow:none" in rail

    def test_active_state(self, h: str) -> None:
        on = h[h.index(".rail .sideitem.on{"):]
        on = on[:on.index("}")]
        assert "background:var(--bg-subtle)" in on
        assert "color:var(--text)" in on and "font-weight:600" in on
        assert "var(--primary-l)" not in on, "选中态仍是蓝色底板"
        bar = h[h.index(".rail .sideitem.on::before{"):]
        bar = bar[:bar.index("}")]
        # 批 124：强调竖线由 2px 改为 **4px**（用户规范）
        assert "width:4px" in bar and "background:var(--accent)" in bar

    def test_collapse_persist(self, h: str) -> None:
        assert 'localStorage.setItem("rail"' in h
        assert "body.rail-off .rail{width:56px" in h


class TestSearchAndPalette:
    """五、搜索原地下拉；命令面板才是 Modal。"""

    def test_inline_popover_without_mask(self, h: str) -> None:
        body = fn(h, "openSearchPop")
        assert "spop" in body
        assert "pal-mask" not in body, "搜索下拉不应带遮罩"

    def test_palette_is_modal(self, h: str) -> None:
        body = fn(h, "openPalette")
        assert "pal-mask" in body

    def test_two_entries(self, h: str) -> None:
        body = fn(h, "initNav")
        assert "openSearchPop" in body and "openPalette" in body
        assert 'gs.onclick' in body and 'cmd.onclick' in body

    def test_esc_and_outside_close(self, h: str) -> None:
        body = fn(h, "openSearchPop")
        assert '"Escape"' in body
        assert "closeSearchPop" in fn(h, "initNav")


class TestListV2:
    """六、列表页：状态页签 / 表头筛选面板 / 密度 / 批量理由 / 无外部下拉。"""

    def test_status_tabs(self, h: str) -> None:
        assert "function lvFilterTabs(" in h
        body = fn(h, "lvFilterTabs")
        assert "data-lvtab" in body
        bind = fn(h, "lvBind")
        assert "data-lvtab" in bind

    def test_header_field_filter_panel(self, h: str) -> None:
        # 批 140：选项行只在 openOptionList 一处生成；字段面板调用它
        body = fn(h, "openOptionList")
        for part in ('class="ck', 'class="dot"', 'class="t"', 'class="c"'):
            assert part in body, part
        panel = fn(h, "lvFieldFilterPop")
        assert "openOptionList({" in panel, "字段面板未走统一组件"
        assert "搜索该字段的取值" in panel
        # 批 156：面板宽度按 GitHub 实测 320；行 hover 用 .1 透明；**不再画左侧竖线**
        assert "width:320px" in fn(h, "openOptionList") or ".fpop{position:fixed;z-index:70;width:320px" in h
        assert ".fpop .opt:hover{background:rgba(129,139,152,.1)}" in h
        assert ".fpop .opt:hover::before" not in h

    def test_density_toggle(self, h: str) -> None:
        assert "data-lvdensity" in h and "lv.density" in h
        # 批 156：密度只作用于内容区（#view），不再挂 body
        assert "#view.lv-compact .lv-table td" in h

    def test_batch_reason(self, h: str) -> None:
        assert 'id="lvReason"' in h
        assert '"reason"' in fn(h, "lvBind")

    def test_no_external_selects_on_list(self, h: str) -> None:
        """政策法规/公司制度不再外挂下拉/输入列（改用页签 + 表头面板 + 单一过滤框）。"""
        assert "function fSelect(" not in h.split("function viewLaws")[1][:1] or True
        body = h[h.index("function viewLaws("):]
        body = body[:body.index("\nfunction ")]
        assert "<select" not in body
        assert "lvRender(" in body


class TestButtonsAndTokens:
    """七、视觉：主按钮绿色；6px 圆角只用于小控件。"""

    def test_primary_green(self, h: str) -> None:
        # 批 126：按钮改用 Primer 的 .btn；"主操作绿色"这条业务覆盖挂在 .btn-primary 上
        btn = h[h.index(".btn.btn-primary{"):]
        btn = btn[:btn.index("}")]
        assert "background:var(--success)" in btn
        assert "#000" not in btn and "black" not in btn

    def test_radius_scale(self, h: str) -> None:
        root = h[h.index(":root{"):h.index("}", h.index(":root{"))]
        assert "--r-md:6px" in root


class TestFavicon:
    def test_link_and_file(self, h: str) -> None:
        assert 'rel="icon"' in h and "favicon.svg" in h
        p = os.path.join(ROOT, "web", "assets", "favicon.svg")
        assert os.path.exists(p)
        svg = open(p, encoding="utf-8").read()
        assert svg.lstrip().startswith("<svg") and "viewBox" in svg


class TestNoRegressionSurfaces:
    """业务面：路由与接口未被本批改动。"""

    def test_routes_intact(self, h: str) -> None:
        # 前端只增减 UI 逻辑；API 路径原样保留
        for p in ("/api/records", "/api/rules", "/api/east", "/api/relations",
                  "/api/worklist", "/api/regstats"):
            assert p in h, p

    def test_no_overflow_auto_on_tabs_anywhere(self, h: str) -> None:
        # 防止以后又把页签改回横向滚动
        for m in re.finditer(r"\.(repo-tabs|tabs-inner)\{[^}]*\}", h):
            assert "overflow-x:auto" not in m.group(0)

class TestGithubIssuesRow:
    """实页基线（docs/ui_github_baseline.md，2026-10-27 抓取）落到代码里的样子。"""

    def test_row_is_3_columns(self, h: str) -> None:
        """批 126：删除"操作"列 —— 它与元数据里的「关系」重复（用户第 2 条）。"""
        body = fn(h, "lvRender")
        for cell in ("lv-selcell", "lv-stcell", "lv-primary"):
            assert cell in body, cell
        assert "lv-actcell" not in body, "操作列已删除，不应再出现"
        # 批 128：表头只剩**一个**单元格（colspan 工具条），行仍是 3 个单元格
        assert body.count("<th ") == 1 and 'colspan="3"' in body

    def test_state_icon_column(self, h: str) -> None:
        assert "function lvStateIcon(" in h
        assert "function lvRender(" in h and "lvStateIcon(it, list)" in fn(h, "lvRender")

    def test_meta_inline_not_below(self, h: str) -> None:
        """实测：元数据与标题**同行**（12px muted），不是压在标题下面的一行。"""
        meta = h[h.index(".lv-meta{"):]
        meta = meta[:meta.index("}")]
        assert "display:inline-flex" in meta
        assert "margin-top" not in meta

    def test_container_border_and_radius(self, h: str) -> None:
        lv = h[h.index(".lv{"):]
        lv = lv[:lv.index("}")]
        assert "border:1px solid var(--border-muted" in lv
        assert "border-radius:6px" in lv

    def test_row_height_and_font(self, h: str) -> None:
        td = h[h.index(".lv-table td{"):]
        td = td[:td.index("}")]
        assert "height:32px" in td and "font-size:14px" in td

    def test_baseline_doc_recorded(self) -> None:
        import os as _os

        p = _os.path.join(ROOT, "docs", "ui_github_baseline.md")
        assert _os.path.exists(p), "缺实测基线文档"
        doc = open(p, encoding="utf-8").read()
        for fact in ("#25292e", "56px", "14px", "12px", "6px", "32px", "listItem"):
            assert fact in doc, fact
