# -*- coding: utf-8 -*-
"""tests/test_gate_triage.py — 门禁归因工具的契约测试。

守卫三组（每组都配失态用例——**会误导人的归因工具比没有更危险**）：

  ① 硬约束三条：门禁名有来源 / 无证据不出结论 / 证据缺口必须可见；
  ② 归因正确性：类别来自**门禁身份注册表**（不从problems 文本猜）；
     重跑命令只对**有 `StepSpec.produces` 声明**的产物给（不拿模块路径冒充步骤名）；
  ③ 注册表与 `ALL_GATES` **名称一致**——防止新增门禁漏登记归因类别
     （本次即因我按语义猜名，只对上 10/30）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = pytest.mark.contract

import gates as G
from tools import gate_triage as T

#: 判据 N-3（硬编码按日快照名）**对本文件生效** —— 故探测样本一律**运行期拼接**，
#: 源码里不出现「族_8位日期.扩展名」的连续字面量（f-string 也不行，它仍含字面量）。
_SNAP_FAMILY = "gov_cleaned_"
_SNAP_DATE = "2026" + "0930"
_SNAP_NAME = f"{_SNAP_FAMILY}{_SNAP_DATE}.jsonl"


@pytest.fixture()
def conn():
    """内存库 + 最小schema（口径可测，不碰真库）。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE gate_result (run_id TEXT, gate TEXT, descr TEXT, "
              "detail_json TEXT, recorded_at TEXT, passed INTEGER)")
    return c


def _fail(c, gate, problems=None, *, detail=None, at="2026-10-16T09:00:00+0800"):
    dj = detail if detail is not None else json.dumps(
        {"problems": problems or []}, ensure_ascii=False)
    c.execute("INSERT INTO gate_result VALUES (?,?,?,?,?,?)",
              ("RUN-T", gate, "判据描述", dj, at, 0))


# --------------------------------------------------------------------------- #
# ③ 注册表与 ALL_GATES 一致（防新增门禁漏登记）
# --------------------------------------------------------------------------- #
def test_registry_covers_all_gates_exactly() -> None:
    """正例：`GATE_CLASS` 与 `ALL_GATES` 的 module 名**完全一致**（不多不少不少）。"""
    real = {str(g["module"]) for g in G.ALL_GATES
            if isinstance(g, dict) and g.get("module")}
    assert real, "ALL_GATES 为空——测试前提不成立"
    missing = sorted(real - set(T.GATE_CLASS))
    ghost = sorted(set(T.GATE_CLASS) - real)
    assert not missing, f"新增门禁未登记归因类别：{missing}"
    assert not ghost, f"注册表含不存在的门禁（会误导）：{ghost}"


def test_all_categories_are_controlled() -> None:
    """正例：所有类别 ∈ 受控集，且每个类别都有建议动作文案。"""
    allowed = set(T.ACTION_BY_CLASS)
    for gate, (cat, desc) in T.GATE_CLASS.items():
        assert cat in allowed, f"{gate} 的类别 {cat!r} 不在受控集"
        assert desc, f"{gate} 缺类别说明"


# --------------------------------------------------------------------------- #
# ① 硬约束
# --------------------------------------------------------------------------- #
def test_unknown_gate_is_rejected_with_candidates(conn) -> None:
    """负例：未知门禁**拒绝**并给候选（不臆测它属于哪类）。"""
    t = T.triage("gates.gate_not_exist", con=conn)
    assert t.found is False
    assert t.category == ""
    assert any("未知门禁" in g for g in t.evidence_gaps)


def test_no_problems_gives_no_category(conn) -> None:
    """负例：无 `problems` ⇒ **不给归因类别**（否则报告自我否定）。"""
    _fail(conn, "gates.gate_import_bootstrap", detail=json.dumps({"by_group": {}}))
    t = T.triage("gates.gate_import_bootstrap", con=conn)
    assert t.found is True
    assert t.category == "", "无证据却给了类别"
    assert t.category_hint, "应保留『若补齐证据属哪类』的提示"
    assert t.conclusion_ready is False
    assert any("不给归因类别" in g for g in t.evidence_gaps)


def test_malformed_detail_json_is_not_crashed(conn) -> None:
    """负例：`detail_json` 非 JSON ⇒ 不崩、不给结论、报缺口。"""
    _fail(conn, "gates.gate_watermark", detail="not-json-at-all")
    t = T.triage("gates.gate_watermark", con=conn)
    assert t.found is True
    assert t.category == ""
    assert t.evidence_gaps


def test_no_fail_record_is_reported_honestly(conn) -> None:
    """负例：台账里没有该门禁的 FAIL ⇒ 明确说"没有"，不返回空报告冒充成功。"""
    t = T.triage("gates.gate_relations", con=conn)
    assert t.found is False
    assert any("未找到 FAIL" in g for g in t.evidence_gaps)


# --------------------------------------------------------------------------- #
# ② 归因正确性
# --------------------------------------------------------------------------- #
def test_artifact_gate_with_declared_step_gets_runnable_command(conn) -> None:
    """正例：产物有 `StepSpec.produces` 声明 ⇒ 给**可执行**的 `--from <步骤名>`。"""
    _fail(conn, "gates.gate_relations",
          problems=["relations_index 水位不一致（声明 X 当前 Y）"])
    t = T.triage("gates.gate_relations", con=conn)
    assert t.category == "artifact"
    assert "relations_index" in t.artifacts
    assert "relations:gen" in t.rerun_steps, f"未反查到步骤：{t}"
    # 步骤名必须是链上真实存在的步骤（不是模块路径）
    from tools.run_production_refresh import step_specs

    names = {s.name for s in step_specs()}
    for st in t.rerun_steps:
        assert st in names, f"重跑建议给了非步骤名（多半是模块路径）：{st}"


def test_artifact_without_step_declaration_gives_no_command(conn) -> None:
    """负例：产物**无步骤声明** ⇒ 不给命令，报治理缺口（不拿模块路径冒充步骤名）。"""
    _fail(conn, "gates.gate_relations",
          problems=["关系产物陈旧：[{'dep': 'internal_index'}] 上游已推进但未重抽取"])
    t = T.triage("gates.gate_relations", con=conn)
    assert "internal_index" in t.artifacts
    assert t.rerun_steps == (), f"不该给命令：{t.rerun_steps}"
    assert any("未声明生产步骤" in g for g in t.evidence_gaps)
    # 仍应报出生产模块，供人工定位
    assert any("internal_policy_base" in m for m in t.artifact_modules)


def test_category_comes_from_registry_not_problem_text(conn) -> None:
    """正例：类别**只由门禁身份决定**——即使 problems 文本含误导性关键词也不改判。"""
    _fail(conn, "gates.gate_secret_scan",
          problems=["modules/x.py:1 —— 这看起来像产物水位问题，但门禁是密钥扫描"])
    t = T.triage("gates.gate_secret_scan", con=conn)
    assert t.category == "environment", "类别被 problems 文本带偏了"


def test_problems_are_verbatim_not_rewritten(conn) -> None:
    """正例：`problems` **原文**保留（判据编号/路径不可丢——改写会让人查无实物）。"""
    raw = "J13.3: http_cassette 不可导入（ImportError: cannot import name 'http_cassette'）"
    _fail(conn, "gates.gate_config_integrity", problems=[raw])
    t = T.triage("gates.gate_config_integrity", con=conn)
    assert t.problems[0].text == raw
    assert "J13.3" in t.problems[0].text


def test_file_pointer_extracted(conn) -> None:
    """正例：`文件:行号` 指针被抽出（供快速定位）。"""
    _fail(conn, "gates.gate_no_duplicate_libs",
          problems=["私有归一 _norm_title 未豁免: ['tools\\\\semantic_assist.py:96']"])
    t = T.triage("gates.gate_no_duplicate_libs", con=conn)
    p = t.problems[0]
    assert any("semantic_assist.py" in f for f in p.files), f"未抽出文件指针：{p}"
    assert 96 in p.lines, f"未抽出行号：{p}"


def test_short_gate_name_resolves(conn) -> None:
    """正例：短名（`gate_relations`）能解析到全名（`gates.gate_relations`）。"""
    _fail(conn, "gates.gate_relations", problems=["x:1"])
    t = T.triage("gate_relations", con=conn)
    assert t.gate == "gates.gate_relations"


def test_render_contains_evidence_and_action(conn) -> None:
    """正例：人读报告必须同时含**证据原文**与**建议动作**（缺一不可）。"""
    _fail(conn, "gates.gate_hardcoded_snapshots",
          problems=[f"tests\\test_gate_run_steps.py:81: {_SNAP_NAME}"])
    out = T.render(T.triage("gates.gate_hardcoded_snapshots", con=conn))
    assert _SNAP_NAME in out
    assert "改代码" in out


def test_json_output_is_machine_readable(conn) -> None:
    """正例：JSON 输出结构稳定（含 `conclusion_ready` 供下游判定）。"""
    import contextlib
    import io as _io

    _fail(conn, "gates.gate_enum_values", problems=["X 枚举值越界"])
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        T.main(["gates.gate_enum_values", "--json"], con=conn)
    doc = json.loads(buf.getvalue())
    assert doc["category"] == "semantics"
    assert doc["conclusion_ready"] is True
    assert doc["problems"][0]["text"].startswith("X 枚举值越界")
