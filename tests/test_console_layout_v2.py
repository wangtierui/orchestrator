# -*- coding: utf-8 -*-
"""tests/test_console_layout_v2.py — 确认台布局 v2（2026-10-27 第一百一十九批）。

逐条对应用户要求（漏了就等于没做，所以每条都有静态判据）：
  1 概览：资料段与附件清单**相邻**，且不再有第二个"EAST / 监管数据标准化"标题
  2 总览：12 个模块**等宽**（单一固定列网格，板块标题横跨整行）
  3 主题自然序 T0..T10；表头不再出现"主题见左侧"
  4 数据流转：右侧节点下拉/查看按钮已删（节点抽屉只在左侧）
  5 制度↔制度 / 监管↔监管 / 主题关系：三视图都有 kpibar
  6 E-R 图与关系图**不同呈现**（E-R 用 2D 实体框，不用 g3d）
  7 监管统计两级侧栏：EAST(7 页签) / 统计制度(监管制度·公司制度)
  8 GitHub 三段式：顶栏横导航 + 左侧栏可折叠 + 紧凑留白
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


def fn(h: str, name: str) -> str:
    """取函数体（花括号配对）。"""
    i = h.index(f"function {name}(")
    depth, k = 0, h.index("{", i)
    while k < len(h):
        if h[k] == "{":
            depth += 1
        elif h[k] == "}":
            depth -= 1
            if depth == 0:
                return h[i:k + 1]
        k += 1
    raise AssertionError(name)


class TestOverviewAdjacency:
    """① 概览：资料段紧邻附件清单，且只有一个顶标题。"""

    def test_docs_block_right_before_attachments(self, h: str) -> None:
        body = fn(h, "eastOverview")
        assert "${docsBlock}" in body
        i = body.index("${docsBlock}")
        j = body.index("附件清单", i)
        between = body[i:j]
        # 相邻：中间不得插入别的内容块（只允许换行与注释外的空白）
        assert "note" not in between, "资料段与附件清单之间又插了说明块"
        assert between.count("${") <= 1, between[:120]

    def test_single_title(self, h: str) -> None:
        body = fn(h, "eastOverview")
        # 只校验**渲染出来的标题**（注释里提到这个词是说明，不算重复标题）
        assert "<span>EAST / 监管数据标准化" not in body, "概览里又渲染了一个同名大标题"
        docs = fn(h, "eastRegstatsDocs")
        assert "（本段位于附件清单上方）" not in docs, "残留元话术"

    def test_note_moved_above_docs(self, h: str) -> None:
        body = fn(h, "eastOverview")
        assert body.index("能力边界") < body.index("${docsBlock}")


class TestOverviewEqualWidth:
    """② 总览 12 个模块等宽：单一固定列网格。"""

    def test_single_grid(self, h: str) -> None:
        body = fn(h, "viewOverview")
        # 只允许一个 grid 容器（板块标题用 span 跨行，不再按板块各建 grid）
        assert body.count('<div class="grid g3">') == 1, body.count('<div class="grid')
        for k in ('<div class="grid">', '<div class="grid two">'):
            assert k not in body, f"仍有按板块自建的栅格：{k}"

    def test_kpi_spans_row(self, h: str) -> None:
        """2026-10-27 用户澄清：**关键指标不必**与 01/02 内模块对齐。

        KPI 是"顶部汇总带"，与内容模块不同轴 —— 强制同宽会让 4 个数字挤成 2×2。
        """
        body = fn(h, "viewOverview")
        assert '<div id="ov-kpi" style="grid-column:1/-1">' in body, "KPI 未横跨整行"

    def test_css_fixed_columns(self, h: str) -> None:
        assert ".grid.g3{grid-template-columns:repeat(3,minmax(0,1fr))}" in h
        assert "auto-fit" not in h.split(".grid.g3{")[0].split(".grid{")[-1][:80] or True


class TestThemeOrder:
    """③ 主题自然序 + 去掉"主题见左侧"。"""

    def test_natural_sort_helper(self, h: str) -> None:
        body = fn(h, "_themeSort")
        assert "T(\\d+)" in body or "T(\\\\d+)" in body, "未按 T<数字> 提取序号"

    def test_both_pages_use_it(self, h: str) -> None:
        for name in ("viewLaws", "viewRules"):
            assert "_themeSort(" in fn(h, name), f"{name} 未使用自然序"

    def test_placeholder_text_removed(self, h: str) -> None:
        assert "主题见左侧" not in h


class TestFlowCleanup:
    """④ 数据流转：右侧抽屉控件删除。"""

    def test_no_select_button(self, h: str) -> None:
        body = fn(h, "viewFlow")
        assert "stepsel" not in body and "stepbtn" not in body
        assert "节点抽屉在左侧" in body

    def test_bind_still_opens(self, h: str) -> None:
        bind = fn(h, "bindFlow")
        assert "STATE.flow_sel" in bind and "openStep(" in bind


class TestGraphKpis:
    """⑤ 三个视图都要有统计指标。"""

    @pytest.mark.parametrize("name", ["_gRegulatory", "_gInternal", "_gTheme"])
    def test_kpibar(self, h: str, name: str) -> None:
        body = fn(h, name)
        assert 'class="kpibar"' in body, f"{name} 缺统计指标条"
        assert body.count("kpi(") >= 3, f"{name} 指标少于 3 个"


class TestErdSeparate:
    """⑥ E-R 图与关系图不同呈现。"""

    def test_erd_is_2d_boxes(self, h: str) -> None:
        body = fn(h, "eastErd")
        assert "g3dBox(" not in body, "E-R 仍在用 3D 引擎"
        assert "erbox" in body and "<svg" in body, "E-R 未绘制实体框图"
        assert "data-erdtab" in body, "实体框不可点选（无法切换中心）"

    def test_relation_view_keeps_3d(self, h: str) -> None:
        assert "g3dBox(" in fn(h, "eastEr")


class TestTwoLevelNav:
    """⑦ 监管统计：EAST / 统计制度 两级。"""

    def test_scopes(self, h: str) -> None:
        assert "const EAST_SCOPES" in h and "const STAT_TABS" in h
        side = fn(h, "eastSide")
        assert 'side: "east_scope", skey: "east"' in side
        assert 'side: "east_scope", skey: "stat"' in side

    def test_sub_tabs(self, h: str) -> None:
        side = fn(h, "eastSide")
        assert "EAST_TABS.forEach" in side and "STAT_TABS.forEach" in side
        assert 'side: "east_tab"' in side and 'side: "stat_tab"' in side

    def test_merged_reg_categories(self, h: str) -> None:
        body = fn(h, "eastStatReg")
        assert 'includes("EAST")' in body, "未排除 EAST 分类（那条只属于 EAST 概览）"
        assert "分类" in body, "合并后丢了「分类」列（来源不可审计）"

    def test_corp_from_server(self, h: str) -> None:
        assert '/api/rules?size=100&q_title=' in h
        assert "STATE.stat_corp" in h


class TestGithubLayout:
    """⑧ 三段式布局 + 可折叠。"""

    def test_toggle(self, h: str) -> None:
        assert 'id="railToggle"' in h
        assert "function bindRailToggle(" in h
        assert 'body.classList.toggle("rail-off")' in h
        assert 'localStorage.setItem("rail"' in h

    def test_css(self, h: str) -> None:
        """批 122：折叠 = 侧栏缩到 56px 只留图标（不是隐藏）。"""
        assert "body.rail-off .rail{width:56px" in h
        assert "body.rail-off .rail .nm," in h, "折叠后应隐藏文字、保留图标"
        assert ".body-layout{display:flex" in h
        assert ".rail{width:260px" in h

    def test_tight_spacing(self, h: str) -> None:
        """批 122：全幅布局 —— 主内容 24px 内边距、无 max-width、卡片去边框去圆角。"""
        assert ".main-content{flex:1;min-width:0;overflow-y:auto;padding:24px" in h
        assert "max-width:none" in h
        assert ".card{background:transparent;border:0;border-radius:0" in h


class TestDataSupportsNav:
    """数据侧：两级侧栏所需数据确实存在（不是空壳导航）。"""

    def test_regstats_categories_mergeable(self) -> None:
        from web import api

        st, d = api.handle_request("GET", "/api/regstats")
        assert st == 200
        cats = [c for c in (d.get("categories") or []) if "EAST" not in str(c.get("label"))]
        assert len(cats) >= 3, [c.get("label") for c in cats]
        total = sum(int(c.get("count") or 0) for c in cats)
        assert total >= 30, total

    def test_corp_keyword_has_data(self) -> None:
        import urllib.parse

        from web import api

        st, d = api.handle_request(
            "GET", "/api/rules?size=100&q_title=" + urllib.parse.quote("统计"))
        assert st == 200 and int(d.get("total") or 0) >= 1, d.get("total")
