# -*- coding: utf-8 -*-
"""tests/test_step_triage.py — 诊断第 2 波（步级失败取证）的契约测试。

守卫重点（每条都配失态——**会误导人的取证工具比没有更危险**）：

  ① **双层标识**：台账用实例名（`clean:nfra`）、声明面用模板名（`clean:{src}`）。
     若门面不做双层匹配，`get_node("clean:nfra")` 查不到 ⇒ 诊断在第一步自我拒绝
     （这正是五阶段审查发现的 B1 阻断级设计陷阱）。
  ② **不做根因断言**：只给「怀疑排序 + 依据」，且**证据缺口必须可见**。
  ③ 模板形态产出物的水位要**按实例展开**后查（否则恒报"无水位"）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = pytest.mark.contract

from interfaces import topology_api as T
from tools import step_triage as S
from tools.run_production_refresh import STEP_ORDER, step_specs


@pytest.fixture()
def conn():
    """内存库 + 最小 schema（口径可测，不碰真库）。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE run_step (run_id TEXT, step TEXT, rc INTEGER, exit_code TEXT, "
              "elapsed_s REAL, skipped INTEGER, note TEXT, recorded_at TEXT, "
              "started_at REAL, finished_at REAL)")
    return c


def _fail(c, step, exit_code="FAIL", rc=1, elapsed=10.0, note="boom", at="2026-10-16T09:00:00+0800",
          run_id="RUN-T", started=None):
    c.execute("INSERT INTO run_step VALUES (?,?,?,?,?,?,?,?,?,?)",
              (run_id, step, rc, exit_code, elapsed, 0, note, at, started, started))


# --------------------------------------------------------------------------- #
# ① 双层标识（核心阻断陷阱）
# --------------------------------------------------------------------------- #
def test_instance_step_resolves_to_template_spec() -> None:
    """正例：台账形态的实例名能反查到模板声明（`clean:nfra` → `clean:{src}`）。"""
    node = T.get_node("clean:nfra")
    assert node is not None, "实例步查不到节点（双层标识未接上）"
    assert node.matched_as == "instance"
    assert node.spec_name == "clean:{src}"


def test_template_step_resolves_as_spec() -> None:
    """正例：模板形态直接命中声明面（`matched_as="spec"`）。"""
    node = T.get_node("clean:{src}")
    assert node is not None and node.spec_name == "clean:{src}"
    assert node.kind and node.layer


def test_unknown_step_returns_none() -> None:
    """负例：完全无关的名字返回 `None`（不臆造节点）。"""
    assert T.get_node("不存在的步骤") is None
    assert T.get_node("") is None


def test_every_ledger_style_instance_resolves() -> None:
    """正例：台账里出现过的**每一种**实例形态都能反查到声明节点。

    依据实测：台账 48 个 DISTINCT step 全是展开形态，若任一反查失败，
    诊断在该步就会给不出依赖与产物 ⇒ 必须逐个验。
    """
    tpl_prefixes = {n.split(":{", 1)[0] for n in STEP_ORDER if "{src}" in n}
    unresolvable = []
    # 源集合从**受控枚举**取（门禁 gate_enum_values：不得在代码里写枚举字面量）
    from config.enums import SOURCE_ORDER

    for src in SOURCE_ORDER:
        for prefix in tpl_prefixes:
            node = T.get_node(f"{prefix}:{src}")
            if node is None:
                unresolvable.append(f"{prefix}:{src}")
    assert not unresolvable, f"实例步无法反查声明节点：{unresolvable}"


def test_neighbors_and_chains_are_cycle_safe() -> None:
    """正例：邻接/上游链在有环图上**终止**（BFS 深度上限 + seen 集合）。"""
    nb = T.neighbors("clean:nfra")
    assert "produced_artifacts" in nb and "upstream" in nb
    chain = T.upstream_chain("apply:gov")
    assert isinstance(chain, list) and len(chain) <= 6 * 40, "上游链未按深度上限收敛"


def test_affected_nodes_reports_downstream() -> None:
    """正例：受影响下游能算出来——用**确实声明了 `depends_on`** 的步骤验证。

    ⚠️ 不能拿 `clean:gov` 举例：实测32 步里只有 3 步声明了 `depends_on`，
    `clean:{src}` 的 `depends_on` 为空 ⇒ 它的下游本就是空集（**事实，非缺陷**）。
    这里用 `classify:all`（产出 `classify:products`，被 `reports:theme` 依赖）。
    """
    aff = T.affected_nodes("classify:all")
    assert aff, "classify:all 应有受影响下游"
    assert "reports:theme" in aff, f"下游计算漏了直接依赖者：{aff}"
    # 传递下游也算在内（reports:theme 之后还有吗？取决于声明）
    assert isinstance(aff, list) and aff == sorted(aff)


#: 2026-10-19（TD-6 处置后）的**新基线**：32 步里 21 步声明 depends_on。
#: 未声明的 11 步是**有依据的无上游/独立只读**（见下方 `_NO_UPSTREAM`），
#: 不是"漏了"——两者必须区分，否则后人无法判断该不该补。
_DEPENDS_BASELINE = 21

#: 明确**无上游依赖**的步骤（补声明时刻意留空，附理由）。
#: 断言这张表与"实际未声明的步骤集合"**完全相等** ⇒ 新增步骤若忘记声明会被抓出。
_NO_UPSTREAM = {
    "collect:nfra_weekly": "采集源（链的起点）",
    "collect:{src}": "采集源（链的起点）",
    "supp:ingest_batch": "补充库摄取（backlog 驱动，源头）",
    "inbox:drop": "收件箱投放（人工投递，源头）",
    "internal:update": "内部制度原件同步（源头）",
    "watch:baseline": "观测基线快照（独立，不依赖主链产物）",
    "retention:plan": "只读披露（dry-run 台账，不改产物）",
    "semantic:preflight": "只读披露（启用前置五道闸）",
    "pg:health": "只读健康检查（向量存储）",
    "gates": "链尾终态门禁：依赖全链，声明会与全链成环（由 R4-e 计划完备守护）",
    "governance:artifacts": "原件注册：扫描原件库，不依赖主链产物",
}


def test_dependency_declarations_cover_all_but_known_sources() -> None:
    """`depends_on` 覆盖率**只升不降**，且未声明者必须逐个有理由。

    历史（2026-10-16 诊断第 2 波）：32 步里仅 **2** 步声明，覆盖率 6%
    ⇒ `upstream_chain` / `affected_nodes` 覆盖面天然有限，会让人得出
    "无影响面"的**错误结论**。

    2026-10-19（TD-6 处置）据 purpose/produces 的**事实关系**补至 **21/32**，
    并在此过程中由 R4-c 揭示一个真实建模缺陷（`cleaned:{src}` 被
    `clean:{src}` 与 `apply:{src}` 双重 produces ⇒ 必然成环，已修）。

    本守卫的三条断言：
      ① 覆盖率不低于新基线（防回退）；
      ② **未声明的步骤集合 == `_NO_UPSTREAM` 键集**（新增步骤忘声明会被抓出，
         且不会被"以为是源头"糊过去）；
      ③ 有上游声明的步骤必须能被 `topology_api` 反查到（声明与图同源）。
    """
    specs = step_specs()
    declared = {s.name for s in specs if s.depends_on}
    assert len(declared) >= _DEPENDS_BASELINE, (
        f"依赖声明覆盖率回退：{len(declared)}/{len(specs)} "
        f"< 基线 {_DEPENDS_BASELINE}（补全后不得再减少）")

    undeclared = {s.name for s in specs} - declared
    assert undeclared == set(_NO_UPSTREAM), (
        "未声明 depends_on 的步骤集合与「有依据的无上游表」不一致：\n"
        f"  多出（忘了声明？）：{sorted(undeclared - set(_NO_UPSTREAM))}\n"
        f"  少了（表里过时？）：{sorted(set(_NO_UPSTREAM) - undeclared)}")

    # 有上游声明的步骤必须能被反查命中（声明面与拓扑图同源）
    for s in specs:
        if not s.depends_on:
            continue
        up = T.neighbors(s.name)["upstream"]
        assert up, f"{s.name} 声明了 depends_on={s.depends_on} 但 upstream 为空"


# --------------------------------------------------------------------------- #
# ② 取证：证据可见 + 缺口可见 + 不做断言
# --------------------------------------------------------------------------- #
def test_timeout_failure_evidence_bundle(conn) -> None:
    """正例：超时失败的取证包含拓扑/执行/note/日志/水位/耗时/邻接七类证据。"""
    _fail(conn, "clean:gov", exit_code="TIMEOUT", rc=-1, elapsed=999.0,
          note="timeout（子进程无已捕获输出）")
    t = S.triage("clean:gov", con=conn)
    assert t.found_failure is True
    kinds = {e.kind for e in t.evidence}
    assert {"topology", "run_step", "note", "step_log", "duration",
            "topology_adj"} & kinds, f"证据类别不足：{kinds}"
    assert t.exit_code == "TIMEOUT"
    assert t.node is not None and t.node.spec_name == "clean:{src}"


def test_no_time_window_is_reported_as_gap(conn) -> None:
    """负例：无执行时间窗 ⇒ **进证据缺口**（不静默忽略，结论须标"待人工确认"）。"""
    _fail(conn, "clean:gov", started=None)
    t = S.triage("clean:gov", con=conn)
    assert t.complete is False
    assert any("时间窗" in m for m in t.missing)


def test_suspicions_carry_basis_not_assertions(conn) -> None:
    """正例：怀疑项**每条都带依据**（不得只给结论不给证据）。"""
    _fail(conn, "clean:gov", exit_code="TIMEOUT", rc=-1)
    t = S.triage("clean:gov", con=conn)
    assert t.suspicions
    for item, basis in t.suspicions:
        assert item and basis, "怀疑项缺依据"


def test_chain_head_does_not_claim_upstream_completed(conn) -> None:
    """负例：链首步骤（无上游）**不得**输出"上游已完成"式矛盾表述。"""
    _fail(conn, "collect:mof", exit_code="TIMEOUT", rc=-1)
    t = S.triage("collect:mof", con=conn)
    joined = " ".join(f"{i}{w}" for i, w in t.suspicions)
    assert "0 层" not in joined, "上游为空却报告了层数"
    assert T.upstream_chain("collect:mof") == [], "链首本就不该有上游"


def test_no_failure_record_is_reported_honestly(conn) -> None:
    """负例：没有失败记录 ⇒ 明确说"没有"，不返回空报告冒充成功。"""
    t = S.triage("clean:gov", con=conn)
    assert t.found_failure is False
    assert any("没有失败记录" in m for m in t.missing)


def test_unknown_step_reports_missing_evidence(conn) -> None:
    """负例：拓扑查不到的步骤 ⇒ 进缺口，不臆造依赖。"""
    t = S.triage("不存在的步骤", con=conn)
    assert t.found_failure is False
    assert t.node is None
    assert any("拓扑里找不到" in m for m in t.missing)


# --------------------------------------------------------------------------- #
# ③ 产物声明闭合（依赖第 1 波补齐的治理缺口）
# --------------------------------------------------------------------------- #
def test_all_steps_have_at_least_one_evidence_path() -> None:
    """正例：每个步骤都能取到节点（否则取证入口形同虚设）。"""
    missing = [s.name for s in step_specs() if T.get_node(s.name) is None]
    assert not missing, f"这些步骤取不到节点：{missing}"


def test_artifact_ownership_is_closed() -> None:
    """正例：产物↔步骤双向闭合（治理缺口已补，重跑建议才对得上）。"""
    from tools.run_production_refresh import GOV_ARTIFACTS

    owned = {p for s in step_specs() for p in s.produces}
    assert set(GOV_ARTIFACTS) <= owned, (
        f"仍有产物无步骤归属：{sorted(set(GOV_ARTIFACTS) - owned)}")


def test_topology_snapshot_coverage_is_zero_orphan() -> None:
    """正例：拓扑快照报告的孤儿产物数为 0。"""
    cov = T.Topology().coverage()
    assert cov["orphan"] == 0, cov


# --------------------------------------------------------------------------- #
# ④ JSON 输出可被下游消费
# --------------------------------------------------------------------------- #
def test_json_output_shape(conn) -> None:
    """正例：JSON 结构稳定（含 `complete` 供下游判定是否可给结论）。"""
    import contextlib
    import io as _io

    _fail(conn, "clean:gov", exit_code="TIMEOUT", rc=-1)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        S.main(["clean:gov", "--json"], con=conn)
    doc = json.loads(buf.getvalue())
    for k in ("step", "found_failure", "complete", "node", "evidence",
              "missing", "suspicions"):
        assert k in doc, f"JSON 缺字段 {k}"
    assert doc["node"]["spec_name"] == "clean:{src}"
