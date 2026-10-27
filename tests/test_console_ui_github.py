# -*- coding: utf-8 -*-
"""tests/test_console_ui_github.py — 工作台 GitHub 风格重构（第一百二十一批）。

逐条对应用户验收标准（**只借鉴 GitHub 的布局/视觉/交互，未改路由与业务**）：

  一 三块式：顶栏 52px / 左栏 264px 可折叠到 56px / 主内容自适应
  二 配色：9 个 CSS 变量按用户给定色值，圆角 6–8px，边框优先于阴影
  三 表头：独立菜单按钮（Hide field / Slice by）+ 点字段名排序 + 拖拽换列序
  四 筛选 Popover：排序入口、清除全部、非默认状态圆点
  五 列表行：悬停出勾选 → 勾选后常显 → 底部浮动批量栏；Shift/Cmd/Ctrl+A；Escape 渐进取消
  六 Saved Views：保存/切换/固定；侧栏折叠时固定视图仍可达
  七 无障碍：aria-label / aria-haspopup / aria-multiselectable / 键盘可达
  八 响应式：≤1024 抽屉式左栏；≤1280 主内容自适应

判据尽量落在**不变式**上（如"每个字段菜单必须同时提供 Hide 与 Slice"），
而不是"页面里有没有某个字符串"。
"""

from __future__ import annotations

import os

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


class TestTokens:
    """二、设计 token：用户给定的 9 个色值必须原样存在。"""

    @pytest.mark.parametrize(
        "pair",
        [
            ("--bg", "#ffffff"),
            ("--bg-subtle", "#f6f8fa"),
            ("--border", "#d0d7de"),
            ("--text", "#1f2328"),
            ("--text-muted", "#656d76"),
            ("--accent", "#0969da"),
            ("--success", "#1a7f37"),
            ("--danger", "#cf222e"),
            ("--hover", "#f3f4f6"),
        ],
    )
    def test_palette(self, h: str, pair: tuple[str, str]) -> None:
        name, val = pair
        assert f"{name}:{val}" in h.replace(" ", ""), f"{name} 不是 {val}"

    def test_radius_and_no_heavy_shadow(self, h: str) -> None:
        root = h[h.index(":root{"):h.index("}", h.index(":root{"))]
        assert "--r-md:6px" in root and "--r-lg:8px" in root
        # 卡片改为"边框优先"：不出现大范围阴影变量
        assert "0 16px 48px" not in root

    def test_legacy_tokens_aliased(self, h: str) -> None:
        """老样式用 `--primary/--fg/--line`，必须别名到新色板（改色只改一处）。"""
        root = h[h.index(":root{"):h.index("}", h.index(":root{"))]
        assert "--primary:var(--accent)" in root
        assert "--fg:var(--text)" in root
        assert "--line:var(--border)" in root


class TestThreeBlocks:
    """一、三块式骨架。"""

    def test_header_height(self, h: str) -> None:
        assert "--head-h:52px" in h
        assert "height:var(--head-h)" in h

    def test_shell_grid_and_rail_widths(self, h: str) -> None:
        assert "--rail-w:264px" in h and "--rail-w-min:56px" in h
        shell = h[h.index(".shell{"):]
        shell = shell[:shell.index("}")]
        assert "grid-template-columns:var(--rail-w) minmax(0,1fr)" in shell

    def test_rail_outside_main(self, h: str) -> None:
        start = h.index('<div class="shell"')
        seg = h[start:h.index("</main>", start)]
        assert 'id="rail"' in seg and 'id="view"' in seg
        assert seg.index('id="rail"') < seg.index('id="view"')

    def test_collapse_persists_and_aria(self, h: str) -> None:
        body = fn(h, "bindRailToggle")
        assert 'localStorage.setItem("rail"' in body
        assert 'getItem("rail")' in body
        assert 'setAttribute("aria-expanded"' in fn(h, "syncRailToggle")
        assert 'aria-controls="rail"' in h

    def test_responsive_drawer(self, h: str) -> None:
        assert "@media (max-width:1024px)" in h
        assert "transform:translateX(-100%)" in h and "body.rail-open .rail" in h


class TestHeader:
    """一.1 顶栏：标识 + 面包屑 + 全局搜索 + 账号。"""

    def test_breadcrumb_and_search(self, h: str) -> None:
        assert 'id="crumbName"' in h
        assert 'id="paletteBtn"' in h and "aria-haspopup" in h

    def test_palette_shortcut(self, h: str) -> None:
        assert "function openPalette(" in h
        assert 'String(ev.key).toLowerCase() === "k"' in h
        body = fn(h, "paletteCommands")
        for cmd in ("跳转：", "show ", "hide ", "save view", "clear filters"):
            assert cmd in body, cmd


class TestHeaderMenus:
    """三、表头菜单：Hide field / Slice by / 排序，且与排序点击分离。"""

    def test_menu_has_both_actions(self, h: str) -> None:
        body = fn(h, "lvBind")
        assert "Hide field" in body and "Slice by this field" in body

    def test_sort_click_separate_from_menu(self, h: str) -> None:
        body = fn(h, "lvRender")
        # 字段名是 sortbtn，菜单是独立 button.menu —— 两个元素、两套 data 属性
        assert 'class="sortbtn" data-lvsort' in body
        assert 'class="menu" data-lvmenu' in body

    def test_drag_reorder(self, h: str) -> None:
        body = fn(h, "lvBind")
        for ev in ("dragstart", "dragover", "drop"):
            assert ev in body, ev
        assert 'draggable="true"' in fn(h, "lvRender")

    def test_hidden_field_removable_from_menu(self, h: str) -> None:
        body = fn(h, "lvBind")
        assert "重新显示该字段" in body and "显示全部字段" in body

    def test_slice_only_on_server_backed_fields(self, h: str) -> None:
        """切片不能变成"只过滤当前页"：只允许后端有分面的字段。"""
        bind = fn(h, "lvBind")
        assert "Slice by this field" in bind
        assert "canSlice" in bind or "fd.slice" in bind


class TestSlicePanel:
    """三、2 切片面板。"""

    def test_panel_parts(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert 'id="lvSliceField"' in body and "No slicing" in body
        assert "lv-slice" in body

    def test_slice_stacks_with_filters(self, h: str) -> None:
        """切片写回**同一个查询参数**，与其它筛选并集生效（不是覆盖）。"""
        body = fn(h, "lvBind")
        assert 'st[fd.slice] = b.dataset.lvslice' in body
        assert "st.page = 1" in body

    def test_slice_state_visible(self, h: str) -> None:
        body = fn(h, "lvActiveFilters")
        assert '"slice"' in body
        assert 'lv-chip' in fn(h, "lvRender") and "切片" in fn(h, "lvRender")


class TestFilterPopover:
    """四、筛选 Popover。"""

    def test_popover_entries(self, h: str) -> None:
        body = fn(h, "lvBind")
        assert "Newest" in body
        assert "清除搜索、筛选与排序" in body

    def test_active_dot(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert 'class="lv-dot"' in body
        assert "act.length" in body

    def test_escape_closes_without_losing_state(self, h: str) -> None:
        """Escape 只关浮层；筛选状态存 `STATE`，不进 DOM ⇒ 天然不丢。"""
        body = fn(h, "lvBind")
        assert 'if (_lvPop) { lvClosePop(); return; }' in body
        assert "lvClearAll" in body


class TestRowsAndSelection:
    """五、列表行与批量操作。"""

    def test_hover_checkbox_then_permanent(self, h: str) -> None:
        assert ".lv-table tbody tr:hover .lv-ck" in h
        assert "body.lv-selecting .lv-table tbody .lv-ck" in h

    def test_batch_bar(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert 'id="lvBatch"' in body and "已选" in body
        bind = fn(h, "lvBind")
        for act in ("copy", "csv", "clear"):
            assert f'"{act}"' in bind, act

    def test_keyboard_multi_select(self, h: str) -> None:
        body = fn(h, "lvBind")
        assert "ev.shiftKey" in body and "ev.metaKey || ev.ctrlKey" in body
        assert 'String(ev.key).toLowerCase() === "a"' in body

    def test_escape_cascade(self, h: str) -> None:
        body = fn(h, "lvBind")
        i = body.index('if (ev.key === "Escape")')
        seg = body[i:i + 600]
        # 顺序：先清选择 → 再关浮层 → 最后清筛选
        assert seg.index("sel[k] = false") < seg.index("_lvPop") < seg.index("lvClearAll")

    def test_selection_survives_rerender(self, h: str) -> None:
        """选择存 `STATE._lvsel`（不是 DOM）⇒ 切筛选/翻页不丢。"""
        body = fn(h, "lvSel")
        assert "STATE._lvsel" in body

    def test_empty_state(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert "当前筛选无结果" in body and "清除筛选" in body


class TestSavedViews:
    """六、Saved Views。"""

    def test_persist_and_apply(self, h: str) -> None:
        assert 'LV_VIEWS_KEY = "lv.views.v1"' in h
        body = fn(h, "lvApplyView")
        assert "st.sort" in body and "lvSetMeta" in body
        assert "window.scrollTo" in body, "切换视图应重置滚动位置"

    def test_pin(self, h: str) -> None:
        assert "function lvTogglePin(" in h
        assert "lvPinnedViews" in fn(h, "paintRail")

    def test_sidebar_group(self, h: str) -> None:
        body = fn(h, "paintRail")
        assert ">视图<" in body or '"视图"' in body
        assert 'data-side="lvsave"' in body

    def test_restore_fields_and_slice(self, h: str) -> None:
        body = fn(h, "lvSnapshot")
        for key in ("hidden", "order", "sliceField", "sliceValue", "sort", "desc", "filters"):
            assert key in body, key


class TestA11y:
    """七、无障碍。"""

    def test_labels(self, h: str) -> None:
        assert 'aria-label="字段菜单：' in h
        assert 'aria-haspopup="menu"' in h
        assert 'aria-multiselectable="true"' in h
        assert 'aria-sort=' in h

    def test_menu_keyboard(self, h: str) -> None:
        body = fn(h, "lvPop")
        for k in ("ArrowDown", "ArrowUp", "Escape"):
            assert k in body, k

    def test_row_focusable(self, h: str) -> None:
        body = fn(h, "lvRender")
        assert 'tabindex="0"' in body

    def test_focus_visible(self, h: str) -> None:
        assert ":focus-visible" in h


class TestDataStillServerside:
    """不改接口：切片/排序仍走既有参数，分页仍由后端决定。"""

    def test_query_params_unchanged(self, h: str) -> None:
        body = fn(h, "lvBind")
        for k in ("theme", "timeliness", "source", "q_title", "q_docno"):
            assert k in body or k in h, k
        assert "pager(d," in fn(h, "lvRender")

    def test_backend_facets_used_for_slice(self) -> None:
        from web import api

        st, d = api.handle_request("GET", "/api/records/facets")
        assert st == 200
        assert d.get("themes") and d.get("sources") and d.get("timeliness")
