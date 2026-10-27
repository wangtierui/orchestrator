# -*- coding: utf-8 -*-
"""tests/test_east_v3.py — EAST 六页签 / 数据元 / 代码表编号 / 3D 图（2026-10-26）。

本批用户逐条要求（对应用例）：
  ① 数据表要显示数据元编码/名称/说明/取值范围，点取值范围跳业务代码表
  ② 代码表要剔除「返回目录」，要显示 `CDxxxxxx` 编号
  ③ 概览不再列正文类附件/表单类附件/数据元字典
  ④ 概览附件清单按 1, 1-1, 1-2… 自然序
  ⑤ 「EAST / 监管数据标准化」在附件清单上方
  ⑥ 新增「数据元」页签，位于 数据表 与 代码表 之间
  ⑦ 关系图谱与 E-R 改 3D 可拖拽
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "assets", "index.html")


def _html() -> str:
    return open(HTML, encoding="utf-8").read()


class TestDataElements:
    """①+ 扩展：数据项带数据元四要素，且取值范围可解析成代码表引用。"""

    def test_items_carry_element_fields(self) -> None:
        from interfaces import east_api

        tbl = east_api.structure()["tables"][0]
        items = east_api.table_items(tbl["table_no"])["items"]
        assert items
        for k in ("element_code", "element_name", "element_desc", "value_range",
                  "code_ref"):
            assert k in items[0], k
        assert any(i["element_code"] for i in items), "数据元编码整列为空"

    def test_code_ref_points_to_real_table(self) -> None:
        """`code_ref.id` 必须能在代码表里找到 —— 否则页面给出的是死链接。"""
        from interfaces import east_api

        idx = {c["id"] for c in east_api.structure()["code_index"] if c.get("id")}
        refs = set()
        for t in east_api.structure()["tables"][:20]:
            for it in east_api.table_items(t["table_no"])["items"]:
                cid = (it.get("code_ref") or {}).get("id")
                if cid:
                    refs.add(cid)
        assert refs, "没有任何代码表引用，解析规则失效"
        assert refs <= idx, f"引用了不存在的代码表：{sorted(refs - idx)[:5]}"

    def test_enum_is_not_a_code_ref(self) -> None:
        """`枚举值：…` 是内联枚举，**不能**给出跳转（给了会点进不相干的表）。"""
        from tools.build_east_structure import _code_ref

        assert _code_ref("枚举值：1；0。“1”代表是。") is None
        got = _code_ref("参照《业务代码表》“保险机构代码”，编号CD000002。")
        assert got == {"id": "CD000002", "name": "保险机构代码"}, got

    def test_elements_have_definitions(self) -> None:
        from interfaces import east_api

        els = east_api.structure()["elements"]
        assert len(els) >= 300
        assert sum(1 for e in els if e.get("element_desc")) >= 290
        # 取值来自代码表的 62 个；其余是内联枚举（`枚举值：…`）或自由文本 —— 不强制有引用
        assert sum(1 for e in els if (e.get("code_ref") or {}).get("id")) >= 50


class TestCodeTables:
    """② 代码表：剔除返回目录 + 有 CD 编号 + 多级表按层级。"""

    def test_no_return_to_toc_column(self) -> None:
        from interfaces import east_api

        for c in east_api.codes()["tables"]:
            cols = c.get("columns") or []
            assert not any("返回目录" in x for x in cols), (c["sheet"], cols)
            rows = east_api.code_table(c["sheet"])["rows"]
            for r in rows[:5]:
                assert "返回目录" not in " ".join(str(x) for x in r)

    def test_ids_present(self) -> None:
        from interfaces import east_api

        tables = east_api.codes()["tables"]
        with_id = [c for c in tables if c.get("id")]
        assert len(with_id) >= 60, f"只有 {len(with_id)} 张表有编号"
        assert all(re.fullmatch(r"CD\d{6}", c["id"]) for c in with_id)
        # 附件1-4「目录」未列出的 3 张工作表：允许空，但必须**是空**而不是瞎编号
        missing = [c["sheet"] for c in tables if not c.get("id")]
        assert len(missing) <= 5, missing

    def test_view_shape_and_hierarchy(self) -> None:
        from interfaces import east_api

        one = east_api.code_table("保险机构代码")
        assert one["id"] == "CD000002", one["id"]
        assert one["view"] and {"code", "name", "desc", "level"} <= set(one["view"][0])
        hier = [c["sheet"] for c in east_api.codes()["tables"] if c.get("hierarchy")]
        assert "销售渠道类型代码" in hier, hier
        sc = east_api.code_table("销售渠道类型代码")["view"]
        assert max(v["level"] for v in sc) > 1, "多级表没有层级"


class TestFrontendContract:
    """③~⑦ 页面契约（静态断言：漏了就等于没做）。"""

    @staticmethod
    def _fn(src: str, name: str) -> str:
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

    def test_six_tabs_in_order(self) -> None:
        h = _html()
        block = h[h.index("const EAST_TABS = ["):h.index("function eastTabBar(")]
        order = re.findall(r'k: "(\w+)"', block)
        # 2026-10-27：`er` 改名「关系图」（字段级），另加 `erd`「E-R 图」（表级）
        assert order == ["overview", "tables", "elements", "codes", "rules", "er", "erd"], order

    def test_table_has_element_columns_and_link(self) -> None:
        h = _html()
        body = self._fn(h, "eastTables")
        for col in ("数据项说明", "数据元编码", "数据元名称", "数据元说明", "取值范围"):
            assert col in body, col
        assert "eastRangeCell" in body
        cell = self._fn(h, "eastRangeCell")
        assert "data-ecoderef" in cell, "取值范围没有做跳转入口"

    def test_overview_is_trimmed(self) -> None:
        h = _html()
        body = self._fn(h, "eastOverview")
        for gone in ("正文类附件", "表单类附件", "数据元字典"):
            assert gone not in body, f"概览仍在列示「{gone}」"
        # ⑤ EAST 卡片在附件清单之前
        assert body.index("EAST / 监管数据标准化") < body.index("附件清单")
        # ④ 自然序
        assert "_natNo" in body

    def test_natural_sort_helper(self) -> None:
        h = _html()
        fn = self._fn(h, "_natNo")
        assert "split(/[-.]/)" in fn, "未按 -/. 分段，1-10 会排到 1-2 前面"

    def test_3d_engine_used_in_both_graphs(self) -> None:
        h = _html()
        assert "function g3dBox(" in h and "function _g3dInner(" in h
        assert "pointermove" in h and "wheel" in h and "requestAnimationFrame" in h
        for name in ("_gBipartite", "_gRegulatory", "_gInternal", "_gTheme", "_gEgo"):
            assert "g3dBox(" in self._fn(h, name), f"{name} 未接 3D"
        assert "g3dBox(" in self._fn(h, "eastEr"), "E-R 未接 3D"
        # 拖拽重绘不能替换 svg 元素本身（否则监听丢失）
        assert "svg.innerHTML = _g3dInner(" in h
        assert "svg.outerHTML" not in h

class TestNoTruncationAndSidebar:
    """2026-10-27 用户要求：说明类文本**禁止省略号缩减**；关键页改左侧栏。"""

    def test_no_gcut_in_east_text_cells(self) -> None:
        """表格里的说明/定义/规则**必须全文**；只允许**图形标签**截断。

        2026-10-27 变更：E-R 实体框的标签要截断（300px 框放不下长表名），
        故判据从"整段禁止 `_gCut`"收窄为"表格函数禁止"。
        """
        h = _html()
        for fn_name in ("eastTables", "eastElements", "eastCodes", "eastRules"):
            i = h.index(f"function {fn_name}(")
            j = h.index("\nfunction ", i)
            body = h[i:j]
            assert "_gCut(" not in body, f"{fn_name} 仍在截断表格文本"

    def test_sidebar_shell_used_by_key_pages(self) -> None:
        h = _html()
        assert "function sideShell(" in h
        # 2026-10-27 第一百二十一批：左栏统一到**页面级 #rail**（视图只 `railSet` 登记），
        # 监管统计的两级导航仍由 `eastSide()` 生成。
        for key in ("graph", "worklist", "flow", "release"):
            assert f'sideShell("{key}"' in h, key
        assert 'railSet("主题"' in h, "政策法规/公司制度 的主题未进左栏"
        assert "function eastSide(" in h and "EAST_SCOPES" in h

    def test_release_evidence_moved_to_overview(self) -> None:
        h = _html()
        i = h.index("function viewRelease(")
        j = h.index("function ", i + 10)
        # 判据要看**卡片调用**，不能看字面词：函数里有一句注释解释"已移入总览"
        assert 'card("发布物证据"' not in h[i:j], "发布事件仍带发布物证据卡"
        ov = h[h.index("function viewOverview("):]
        ov = ov[:ov.index("\n}")]
        assert "发布物证据" in ov, "总览未加发布物证据"

    def test_er_tab_splits_relation_and_er(self) -> None:
        h = _html()
        assert "function eastErd(" in h, "缺表级 E-R 图"
        body = h[h.index("function eastErd("):]
        body = body[:body.index("\nfunction ")]
        # 2D 实体框：点框/点表名都走 `data-erdtab` 委托（不再是 3D 的 pick 回调）
        assert "data-erdtab" in body, "E-R 实体框不可点选"
        assert "上游" in body and "下游" in body, "缺上下游链路说明"
        assert "g3dBox(" not in body, "E-R 又用回 3D（与关系图重复呈现）"
