# -*- coding: utf-8 -*-
"""tests/test_mcp_diagnosis.py — 诊断能力接入 MCP 门面的契约（第 4 波-B）。

设计约束（**只读，不新增写级**）
--------------------------------
诊断工具经 MCP 暴露时**只暴露读级**：`diagnose_gate` / `diagnose_step` /
`backlog_health` 资源。处置（`backlog_governance apply`）**不进 MCP**——
处置必须由人显式发起，不能由模型代劳（对齐 C1「人工处置须留痕可追」）。

守卫要点：
  ① 两个工具已注册且带 `diagnosis` 标签；
  ② **归因复用同一份判据**（不复制逻辑）：`diagnose_gate` 的结果与直接调
     `gate_triage.triage` 一致；
  ③ **双层标识**如实返回（`matched_as` ∈ {instance, spec, both, ""}）；
  ④ **无证据不出结论**：未知门禁 ⇒ `found=False` 且 `category` 为空；
  ⑤ 资源返回 `contents`（MCP 协议形态）且含 `metrics`/`verdict`；
  ⑥ **写级不可达**：MCP 工具集合里不得出现 `apply`/`resolve` 类处置入口。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from mcp_server import resources as R
from mcp_server import tools as T


class TestToolsRegistered:
    def test_diagnosis_tools_exist(self) -> None:
        """正例：两个诊断工具已注册且可调用。"""
        assert callable(T.diagnose_gate)
        assert callable(T.diagnose_step)

    def test_diagnosis_tag_registered(self) -> None:
        """正例：`diagnosis` 标签下有两个工具（标签是 MCP 的发现面）。

        ⚠️ 注册在 `mcp_server/registry.py:115` 的 `_REGISTRY`（`dict[str, ToolSpec]`），
        **不是**给函数挂属性——我曾按 `__mcp_tags__` 猜，实测不存在。
        """
        from mcp_server.registry import _REGISTRY

        tagged = {n for n, spec in _REGISTRY.items()
                  if "diagnosis" in (spec.tags or ())}
        assert tagged >= {"diagnose_gate", "diagnose_step"}, tagged

    def test_diagnosis_tools_are_read_access(self) -> None:
        """正例：诊断工具必须是 `access="read"`（诊断只读，不写库）。"""
        from mcp_server.registry import _REGISTRY

        for n in ("diagnose_gate", "diagnose_step"):
            assert _REGISTRY[n].access == "read", (
                f"{n} 的 access={_REGISTRY[n].access}（诊断须只读）")


class TestDiagnoseGate:
    def test_reuses_same_judgement(self) -> None:
        """正例：结果与直接调 `gate_triage.triage` **一致**（不复制判据）。"""
        from tools import gate_triage as G

        con = G._open_conn()
        if con is None:
            pytest.skip("治理库不可读")
        try:
            direct = G.triage("gates.gate_secret_scan", con=con)
        finally:
            con.close()
        via_mcp = T.diagnose_gate("gates.gate_secret_scan")
        assert via_mcp["found"] == direct.found
        assert via_mcp["category"] == direct.category
        assert via_mcp["problem_count"] == len(direct.problems)

    def test_unknown_gate_gives_no_category(self) -> None:
        """负例：未知门禁 ⇒ `found=False` 且**不给类别**（不臆测）。"""
        d = T.diagnose_gate("gates.not_a_real_gate_xyz")
        assert d["found"] is False
        assert not d["category"], f"未知门禁不该给类别：{d['category']}"

    def test_listing_returns_structured_rows(self) -> None:
        """正例：无参时返回**结构化**清单（不是 render 出来的字符串）。"""
        d = T.diagnose_gate("")
        assert d["available"] is True
        assert isinstance(d["recent_failures"], list)
        if d["recent_failures"]:
            row = d["recent_failures"][0]
            assert set(row) == {"gate", "run_id", "recorded_at", "descr"}
            assert row["gate"], "失败清单的 gate 不得为空"

    def test_evidence_is_list_of_strings(self) -> None:
        """正例：证据是**原文列表**（便于上层原样引用，不被改写）。"""
        d = T.diagnose_gate("gates.gate_secret_scan")
        assert isinstance(d["evidence"], list)
        assert all(isinstance(x, str) for x in d["evidence"])


class TestDiagnoseStep:
    def test_matched_as_reports_layer(self) -> None:
        """正例：双层标识如实返回（台账实例 vs 声明模板）。"""
        d = T.diagnose_step("collect:mof")
        assert d["available"] is True
        assert d["matched_as"] in ("instance", "spec", "both", ""), d["matched_as"]

    def test_evidence_items_have_shape(self) -> None:
        """正例：证据项含 kind/ok/detail/data 四字段（可序列化）。"""
        d = T.diagnose_step("collect:mof")
        for e in d["evidence"]:
            assert set(e) == {"kind", "ok", "detail", "data"}
            assert isinstance(e["data"], dict)

    def test_suspicions_carry_basis(self) -> None:
        """正例：每条怀疑都**必挂依据**（无依据的怀疑等于臆测）。"""
        d = T.diagnose_step("collect:mof")
        for s in d["suspicions"]:
            assert set(s) == {"claim", "basis"}
            assert s["basis"], f"怀疑项缺依据：{s}"


class TestBacklogResource:
    def test_resource_listed(self) -> None:
        """正例：`backlog_health` 已在资源清单。"""
        uris = [r["uri"] for r in R.list_resources()]
        assert any("backlog_health" in u for u in uris), uris

    def test_resource_returns_contents(self) -> None:
        """正例：返回 MCP 协议形态 `contents`，且含 metrics/verdict。"""
        d = R.read_resource("orch://backlog_health")
        assert "contents" in d, d.keys()
        c = d["contents"]
        assert "metrics" in c and "verdict" in c
        assert "open" in c["metrics"]
        assert "should_alert" in c["verdict"]

    def test_resource_version_is_derived(self) -> None:
        """正例：`version` 由内容派生（内容变则版本变，可作缓存键）。"""
        d = R.read_resource("orch://backlog_health")
        assert d["version"] and len(str(d["version"])) >= 8


class TestNoWriteSurface:
    def test_no_disposal_entrypoint_in_mcp(self) -> None:
        """负例：MCP **不得**暴露处置入口（处置须人显式发起）。

        `apply`/`resolve`/`dismiss` 类若出现在 MCP 工具面，
        模型就能自行关闭待办——绕过 C1 的人工留痕要求。
        """
        names = [n for n, v in vars(T).items() if callable(v)]
        banned = [n for n in names
                  if any(k in n.lower() for k in ("apply", "dismiss", "resolve_"))]
        assert not banned, f"MCP 暴露了处置入口：{banned}"
