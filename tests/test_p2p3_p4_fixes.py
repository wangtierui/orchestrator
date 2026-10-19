# -*- coding: utf-8 -*-
"""tests/test_p2p3_p4_fixes.py — 2026-10-19 用户报三处工作台缺陷的回归守卫。

三个根因（都是**字段名/口径不匹配**，不是渲染技术问题）：
  ① **P3 主题分布**：`theme_dist` 曾从 `attr_rows()` 取"主题"列，而归属表
     **没有该列**（实测 8 列：监管文件编号/文件名称/发文字号/发布日期/
     文件来源/时效状态/判定日期/编号备注）⇒ 1162 条全落 `?`，图表只剩一根柱。
     正确来源是 `theme_by_doc()`（主题归属表的 `监管文件编号 → 主题`）。
     **易混点**：`theme_map()` 是 `T0..T10 → 主题名` 的**受控枚举**，拿它查编号 0 命中。
  ② **P4 kind 筛选**：此前只有全量列表（混 6 个 kind），"同 kind 批量处置"无从下手。
     现支持 `?kind=`，且 `kind_counts` 必须保持**全量口径**（否则筛完其它 kind 计数变 0，
     用户会以为"没了"）。
  ③ **P2 契约状态**：`contract` 的字段是**统计计数**（files/articles/...），
     不是 `consistent/stat`（虽然 `consistent` 也存在）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestThemeByDoc:
    def test_theme_map_is_not_doc_mapping(self) -> None:
        """负例（易混点）：`theme_map()` 的键是 **T0..T10**，不是文件编号。

        这条钉住"两个函数的区别"，防止后人再把 `theme_map()` 当编号映射用。
        """
        from interfaces import rfn_api

        tm = rfn_api.theme_map()
        assert tm, "theme_map 不应为空"
        assert all(k.startswith("T") and k[1:].isdigit() for k in tm), (
            f"theme_map 的键应是 T<n>：{list(tm)[:5]}")

    def test_theme_by_doc_maps_docid_to_theme(self) -> None:
        """正例：`theme_by_doc()` 的键是 `RFN-…` 编号、值是主题名。"""
        from interfaces import rfn_api

        m = rfn_api.theme_by_doc()
        assert m, "theme_by_doc 不应为空"
        k, v = next(iter(m.items()))
        assert k.startswith("RFN-"), f"键应是文件编号：{k!r}"
        assert v and not v.startswith("RFN-"), f"值应是主题名：{v!r}"

    def test_attr_rows_have_no_theme_column(self) -> None:
        """**根因守卫**：归属表**没有**"主题"列 ⇒ 任何从 attr_rows 取主题的写法都错。"""
        from interfaces import rfn_api

        rows = rfn_api.attr_rows()
        assert rows, "归属表不应为空"
        cols = list(rows[0])
        assert "主题" not in cols, f"归属表竟有主题列（口径变了，需重新审）: {cols}"
        assert "监管文件编号" in cols

    def test_theme_dist_fully_classified(self) -> None:
        """正例：`theme_dist` 的合计 == 归属表条数，且**无 `?` 桶**。"""
        from web import snapshots as S

        d = S.metrics()
        td = d.get("theme_dist") or {}
        assert td, "theme_dist 不应为空"
        assert "?" not in td, f"仍有 `?` 桶（根因未修）: {td}"
        n = len(__import__("interfaces.rfn_api", fromlist=["attr_rows"]).attr_rows())
        assert sum(td.values()) == n, f"合计 {sum(td.values())} != 归属表 {n}"


class TestWorklistKindFilter:
    def test_kind_filter_narrows_items(self) -> None:
        """正例：给定 kind 时只返回该 kind，且 `total` 等于该 kind 的条数。"""
        from web import snapshots as S

        all_d = S.worklist_items()
        counts = all_d.get("kind_counts") or {}
        assert counts, "kind_counts 不应为空"
        k = max(counts, key=lambda x: counts[x])
        d = S.worklist_items(kind=k)
        assert d["total"] == counts[k], (d["total"], counts[k])
        assert all(x.get("kind") == k for x in d["items"])

    def test_kind_counts_stay_global_when_filtered(self) -> None:
        """**关键口径**：筛选后 `kind_counts` 仍是**全量**计数。

        若跟着筛选变，用户切到某 kind 后其它 kind 显示 0 ⇒ 误以为"条目没了"。
        """
        from web import snapshots as S

        counts = S.worklist_items().get("kind_counts") or {}
        k = max(counts, key=lambda x: counts[x])
        filtered = S.worklist_items(kind=k).get("kind_counts") or {}
        assert filtered == counts, "筛选后 kind_counts 变了（口径应为全量）"

    def test_filter_echoed_for_ui(self) -> None:
        """正例：回填 `filter.kind`（前端据此回显选中项）。"""
        from web import snapshots as S

        d = S.worklist_items(kind="relevance_boundary")
        assert (d.get("filter") or {}).get("kind") == "relevance_boundary"

    def test_unknown_kind_returns_empty_not_error(self) -> None:
        """负例：不存在的 kind ⇒ 空列表而非报错（用户输错不该 500）。"""
        from web import snapshots as S

        d = S.worklist_items(kind="__no_such_kind__")
        assert d.get("available") is True
        assert d["total"] == 0


class TestAssetsContractFields:
    def test_contract_has_statistics(self) -> None:
        """正例：`contract` 含统计计数（前端按此渲染）。"""
        from web import snapshots as S

        c = (S.assets() or {}).get("contract") or {}
        for k in ("files", "articles", "chapters", "structures"):
            assert k in c, f"contract 缺 {k}：{list(c)[:12]}"

    def test_parse_modes_present(self) -> None:
        """正例：`parse_modes` 可用于展示解析方式分布。"""
        from web import snapshots as S

        c = (S.assets() or {}).get("contract") or {}
        pm = c.get("parse_modes")
        assert isinstance(pm, dict) and pm, f"parse_modes 异常：{pm!r}"


class TestGrowthInSchedule:
    def test_schedule_declares_backlog_growth_job(self) -> None:
        """正例：调度里声明了 `backlog_growth` 作业（否则回路不会自动转）。"""
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        t = open(os.path.join(root, "config", "schedule.yaml"),
                    encoding="utf-8").read()
        assert "id: backlog_growth" in t
        assert "backlog" in t and "growth" in t

    def test_argv_is_repo_resolvable(self) -> None:
        """**门禁 S2 同款判据**：作业 `argv[0]` 必须是仓内可解析目标。

        实测坑：写成 `["python", "-m", ...]` 会被解析成 `<-m>` 而报"目标不存在"。
        """
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        t = open(os.path.join(root, "config", "schedule.yaml"),
                    encoding="utf-8").read()
        m = re.search(r"id: backlog_growth(.*?)(?=\n  - id: |\Z)", t, re.S)
        assert m, "未找到 backlog_growth 作业段"
        seg = m.group(1)
        am = re.search(r"argv:\s*\[([^\]]*)\]", seg)
        assert am, "backlog_growth 缺 argv"
        first = am.group(1).split(",")[0].strip().strip('"').strip("'")
        assert first == "cli.py", f"argv[0] 应为 cli.py（实测 python 会被 S2 拒）: {first}"

    def test_event_registered_in_three_places(self) -> None:
        """正例：告警事件在**三处**同步（NOTIFY_EVENTS / notify.on / 策略白名单）。

        任一处漏 ⇒ 门禁 S3 双向守护 FAIL，或告警静默不发。
        """
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def _read(*p: str) -> str:
            return open(os.path.join(root, *p), encoding="utf-8").read()

        assert "backlog_growth" in _read("std_lib", "common_lib", "notify.py")
        assert "backlog_growth" in _read("config", "schedule.yaml")
        assert "backlog_growth" in _read("config", "integrity_policy.yaml")
