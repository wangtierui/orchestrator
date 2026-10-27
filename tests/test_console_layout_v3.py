# -*- coding: utf-8 -*-
"""tests/test_console_layout_v3.py — 布局 v3（2026-10-27 第一百二十批）。

本批四条 UI 缺陷的**根因级**判据（不是"页面里有没有这句话"）：

  ① EAST 二级页签点了没反应 —— 根因是 `data-side` 发了动作键、`SIDE_ACT` 里却没有
     对应处理函数（委托里 `act` 为 undefined 就静默跳过）⇒ 用"**所有动作键必须有处理
     函数**"这条不变式兜住，同类 bug 不会再出现；
  ② 关键指标不必与 01/02 内模块等宽 —— KPI 横跨整行；
  ③ EAST 概览只显示 EAST 分类（三个统计制度分类归到「统计制度 → 监管制度」）；
  ④ 3D 点节点聚焦 —— 根因是 `setPointerCapture` 把 click 的 target 改成 svg 本身，
     节点上的 click 监听永不触发 ⇒ 判据改为"按坐标命中 + moved 阈值"。
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


class TestNoDeadClicks:
    """① 不变式：每个动作键都必须有处理函数（这就是"点了没反应"的根因）。"""

    @staticmethod
    def _act_keys(src: str) -> set[str]:
        body = fn(src, "SIDE_ACT") if "function SIDE_ACT" in src else None
        if body is None:                      # const SIDE_ACT = {...}
            i = src.index("const SIDE_ACT = {")
            j = src.index("\n};", i)
            body = src[i:j]
        # 只判"有没有这个键"，**不限定参数表**：`lvsave: () => …` 也是合法处理函数
        # （旧正则要求 `(k)` ⇒ 误报，见第 121 批）。
        return set(re.findall(r"(?m)^\s{2}([A-Za-z_]\w*)\s*:\s*\(", body))

    @staticmethod
    def _used_keys(src: str) -> set[str]:
        used = set(re.findall(r'data-side="([A-Za-z_]\w*)"', src))
        used |= set(re.findall(r'side:\s*"([A-Za-z_]\w*)"', src))
        used |= set(re.findall(r'sideShell\("([A-Za-z_]\w*)"', src))
        return used

    def test_every_side_key_has_handler(self, h: str) -> None:
        used, acts = self._used_keys(h), self._act_keys(h)
        missing = sorted(used - acts)
        assert not missing, f"这些动作键没有处理函数（点了会静默无反应）：{missing}"

    def test_east_tab_registered(self, h: str) -> None:
        """EAST 二级页签发的是 `east_tab` —— 必须显式注册（本批 bug 就出在这里）。"""
        assert "east_tab" in self._act_keys(h)
        assert 'side: "east_tab"' in fn(h, "eastSide")

    def test_stat_tab_registered(self, h: str) -> None:
        assert "stat_tab" in self._act_keys(h)
        assert 'side: "stat_tab"' in fn(h, "eastSide")


class TestGlobalRail:
    """⑤ 三段式：`#rail` 在 main 之外，可折叠，视图不再自带 aside。"""

    def test_rail_outside_main(self, h: str) -> None:
        assert 'id="rail"' in h
        # `.shell` 里还有抽屉遮罩（自闭合 div），不能"遇到第一个 </div> 就截断"
        start = h.index('<div class="shell"')
        shell = h[start:h.index("</main>", start)]
        assert 'id="rail"' in shell and 'id="view"' in shell
        assert shell.index('id="rail"') < shell.index('id="view"')

    def test_views_do_not_wrap_side(self, h: str) -> None:
        assert h.count('<div class="sidewrap">') == 0, "仍有视图自带 aside 包裹"
        assert "sideShell(" in h and "railSet(" in h

    def test_paint_on_every_branch(self, h: str) -> None:
        body = fn(h, "render")
        assert "RAILCFG = null" in body, "渲染前未重置导航（会残留上一页的项）"
        assert "paintRail()" in body, "未统一绘制左栏"
        # 必须在 finally 里（登录/异常分支也要清）
        fin = body[body.index("}finally{"):]
        assert "paintRail()" in fin[:200]

    def test_collapse(self, h: str) -> None:
        """折叠 = 56px 图标列（保留可点），只有"本页无第二节"时才整列隐藏。"""
        assert "body.rail-off .shell{grid-template-columns:var(--rail-w-min)" in h
        assert "body.rail-empty .shell{grid-template-columns:minmax(0,1fr)}" in h


class TestKpiDecoupled:
    """② KPI 横跨整行，不与内容模块同宽。"""

    def test_kpi_spans(self, h: str) -> None:
        assert '<div id="ov-kpi" style="grid-column:1/-1">' in fn(h, "viewOverview")


class TestOverviewEastOnly:
    """③ EAST 概览只显示 EAST 分类；三个统计分类归到「统计制度 → 监管制度」。"""

    def test_filter(self, h: str) -> None:
        body = fn(h, "eastRegstatsDocs")
        assert '.includes("EAST")' in body, "未按 EAST 过滤 ⇒ 概览会重复显示三个统计分类"

    def test_merged_side_keeps_rest(self, h: str) -> None:
        body = fn(h, "eastStatReg")
        assert 'includes("EAST")' in body, "监管制度页未排除 EAST 分类"
        assert "分类" in body, "合并后丢了「分类」列（来源不可审计）"

    def test_overview_still_has_attachment_list(self, h: str) -> None:
        body = fn(h, "eastOverview")
        assert "附件清单" in body and "${docsBlock}" in body


class TestPointerPick:
    """④ 3D 点节点聚焦：坐标命中（capture 后 click target 会变 svg）。"""

    def test_no_per_node_click(self, h: str) -> None:
        assert "bindG3dNodes" not in h, "仍在用逐节点绑定（capture 后收不到 click）"

    def test_element_from_point(self, h: str) -> None:
        body = fn(h, "bindG3d")
        assert "document.elementFromPoint(" in body
        assert 'closest(".g3n")' in body
        assert "d.moved <= 6" in body, "未设拖拽阈值 ⇒ 拖一下就会误触发点选"

    def test_still_renders_selection(self, h: str) -> None:
        body = fn(h, "_g3dInner")
        assert "st.sel" in body and "sel ===" in body.replace("st.sel", "sel")


class TestRailContentStillData:
    """左栏不是空壳：各页登记的项确实来自数据（条数/状态）。"""

    def test_graph_counts(self, h: str) -> None:
        body = fn(h, "viewGraphGlobal")
        assert "sideShell(\"graph\"" in body and "count:" in body

    def test_flow_status_dots(self, h: str) -> None:
        body = fn(h, "viewFlow")
        assert "sideShell(\"flow\"" in body and "dot:" in body

    def test_release_dates(self, h: str) -> None:
        body = fn(h, "viewRelease")
        assert 'sideShell("release"' in body and "dayCount" in body
