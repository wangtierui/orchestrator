# -*- coding: utf-8 -*-
"""tests/test_governance_store.py — 治理库（阶段 1）单元测试

覆盖（不依赖本机数据 ⇒ 无 `data` marker，可在无数据环境/CI 执行）：
  - 建库建表幂等、快照概览；
  - 水位登记/覆盖、依赖边展开（ok / stale / unregistered 三态）；
  - `check_dependencies` 判据（stale 阻断、unregistered 不阻断）；
  - 治理库未启用时的**全链路 no-op 降级**（不得抛、不得阻断）；
  - 审计追加与查询；原件 path_keys 集合语义（跨层硬链接 → 同 sha 多路径合并为一行）；
  - `gates.gate_watermark` 三态行为（未启用 PASS / 一致 PASS / 陈旧 FAIL）。

隔离纪律：全部用例经 `REG_ORCH_GOVERNANCE_DB` 指向 pytest 临时目录，
**绝不触碰仓根 data/governance.db**（生产治理库）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from std_lib.common_lib import governance_store as gs  # noqa: E402


@pytest.fixture
def gdb(tmp_path, monkeypatch):
    """临时治理库（未创建；由用例显式 init）。"""
    p = tmp_path / "governance.db"
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(p))
    return p


# ---------------- 未启用：全链路 no-op 降级 ----------------
def test_disabled_db_degrades_silently(gdb):
    assert not gs.enabled()
    assert gs.snapshot()["enabled"] is False
    assert gs.list_watermarks() == []
    assert gs.get_watermark("anything") is None
    assert gs.list_audit() == []
    assert gs.list_artifacts() == []
    assert gs.list_gate_results() == []
    assert gs.list_runs() == []
    # 未启用时一致性判据视为通过（阶段 1 = 只增不减的新增判据）
    ok, det = gs.check_dependencies()
    assert ok and det["enabled"] is False
    # 写接口在未启用时不应被调用（调用方须先 enabled() 判断）；此处只验证 read 面不抛


# ---------------- 建库 ----------------
def test_init_db_is_idempotent(gdb):
    p1 = gs.init_db()
    p2 = gs.init_db()
    assert p1 == p2 == str(gdb)
    assert gdb.exists()
    snap = gs.snapshot()
    assert snap["enabled"] is True
    assert set(snap["counts"]) == set(gs.TABLES)
    assert all(v == 0 for v in snap["counts"].values())


# ---------------- 水位与依赖边 ----------------
def test_watermark_edge_ok_then_stale(gdb):
    gs.init_db()
    gs.record_watermark("cleaned:gov", "clean.run_clean_pipeline", "v1", record_count=10)
    gs.record_watermark("relations_index", "tools.extract_relations", "r1",
                        inputs={"cleaned:gov": "v1"}, record_count=5)

    edges = gs.dependency_edges()
    assert len(edges) == 1
    assert edges[0]["status"] == "ok"
    assert gs.check_dependencies()[0] is True

    # 上游推进（cleaned 变了）→ 下游产物未重跑 ⇒ stale ⇒ 判据 FAIL
    gs.record_watermark("cleaned:gov", "clean.run_clean_pipeline", "v2", record_count=11)
    edges = gs.dependency_edges()
    assert edges[0]["status"] == "stale"
    assert edges[0]["declared_version"] == "v1"
    assert edges[0]["current_version"] == "v2"
    ok, det = gs.check_dependencies()
    assert ok is False and det["stale_edges"] == 1
    assert "水位陈旧" in " ".join(det["problems"])

    # 下游重跑（重新声明新上游版本）⇒ 恢复一致
    gs.record_watermark("relations_index", "tools.extract_relations", "r2",
                        inputs={"cleaned:gov": "v2"}, record_count=6)
    assert gs.check_dependencies()[0] is True


def test_unregistered_dep_not_blocking(gdb):
    gs.init_db()
    gs.record_watermark("merged_view", "ipb.merged", "m1",
                        inputs={"internal_index": "i1", "rfn_attr": "a1"})
    ok, det = gs.check_dependencies()
    assert ok is True, "上游未登记水位不得阻断（过渡期覆盖度不足属预期）"
    assert det["stale_edges"] == 0
    assert set(det["unregistered_deps"]) == {"internal_index", "rfn_attr"}


def test_watermark_requires_version(gdb):
    gs.init_db()
    assert gs.record_watermark("x", "pb", "") is None, "空版本（产物缺失）不得登记"
    assert gs.list_watermarks() == []


def test_watermark_upsert_is_single_row(gdb):
    gs.init_db()
    for v in ("v1", "v2", "v3"):
        gs.record_watermark("k", "pb", v)
    rows = gs.list_watermarks()
    assert len(rows) == 1 and rows[0]["version"] == "v3"


def test_bad_watermark_row_blocks(gdb):
    gs.init_db()
    gs.record_watermark("k", "pb", "v1")
    # 人为制造缺 version 的坏行（模拟手工写库/迁移残缺）
    with gs.connect() as c:
        c.execute("UPDATE watermark SET version='' WHERE artifact_key='k'")
        c.commit()
    ok, det = gs.check_dependencies()
    assert ok is False
    assert "缺 produced_at/version" in " ".join(det["problems"])


# ---------------- 审计 ----------------
def test_audit_append_and_query(gdb):
    gs.init_db()
    gs.log_audit("field_update", "隶属表", target_key="RFN-1", field="发文字号",
                 old="", new="银保监发〔2020〕11号", basis="正文首部取证", actor="manual")
    gs.log_audit("field_update", "隶属表", target_key="RFN-2", field="发文字号",
                 old="", new="X", basis="b")
    gs.log_audit("note", "关系产物", target_key="REL-1")
    assert len(gs.list_audit()) == 3
    assert len(gs.list_audit(target="隶属表")) == 2
    one = gs.list_audit(target="隶属表", target_key="RFN-1")
    assert len(one) == 1 and one[0]["new_value"] == "银保监发〔2020〕11号"
    assert one[0]["basis"] == "正文首部取证"


# ---------------- 原件注册（path_keys 集合语义） ----------------
def test_artifacts_merge_same_sha_paths(gdb, tmp_path):
    gs.init_db()
    # 同一内容写两处（模拟 originals/ ↔ corpus/ 硬链接共享 inode）
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.4 same-content")
    b.write_bytes(b"%PDF-1.4 same-content")
    rows = [{"sha256": gs.version_of_file(str(a), short=64), "path": str(a),
             "bytes": a.stat().st_size, "kind": "pdf"},
            {"sha256": gs.version_of_file(str(b), short=64), "path": str(b),
             "bytes": b.stat().st_size, "kind": "pdf"}]
    gs.upsert_artifacts(rows)
    items = gs.list_artifacts()
    assert len(items) == 1, "同 sha 必须聚合为一行（否则失去硬链接去重语义）"
    assert len(items[0]["paths"]) == 2
    # 幂等：重复登记不产生重复路径
    gs.upsert_artifacts(rows)
    assert len(gs.list_artifacts()[0]["paths"]) == 2


def test_version_of_file_missing_is_empty(gdb, tmp_path):
    assert gs.version_of_file(str(tmp_path / "nope.bin")) == ""
    assert gs.version_of_files([str(tmp_path / "nope1"), str(tmp_path / "nope2")]) == ""
    f = tmp_path / "f.txt"
    f.write_text("hello", encoding="utf-8")
    assert gs.version_of_file(str(f)) != ""
    assert gs.version_of_files([str(f)]) != ""


# ---------------- 门禁三态 ----------------
def test_gate_watermark_disabled_passes(gdb):
    from gates import gate_watermark
    ok, detail = gate_watermark.run()
    assert ok is True
    assert detail["enabled"] is False
    assert "治理库未启用" in detail["note"]


def test_gate_watermark_pass_and_fail(gdb):
    from gates import gate_watermark
    gs.init_db()
    gs.record_watermark("cleaned:gov", "clean", "v1")
    gs.record_watermark("relations_index", "extract_relations", "r1",
                        inputs={"cleaned:gov": "v1"})
    ok, detail = gate_watermark.run()
    assert ok is True and detail["enabled"] is True
    assert detail["edges"] == 1 and detail["stale_edges"] == 0

    gs.record_watermark("cleaned:gov", "clean", "v2")
    ok, detail = gate_watermark.run()
    assert ok is False and detail["stale_edges"] == 1


# ---------------- run_log / gate_result ----------------
def test_run_log_and_gate_results(gdb):
    gs.init_db()
    run_id = gs.run_start(["python", "cli.py", "gates"], note="测试")
    assert run_id.startswith("RUN-")
    assert gs.current_run_id() == run_id
    gs.run_finish(run_id, True, note="rc=0")
    runs = gs.list_runs()
    assert runs[0]["run_id"] == run_id and runs[0]["ok"] == 1

    n = gs.record_gate_results(run_id, [
        {"module": "gates.gate_a", "desc": "A", "passed": True, "detail": {"x": 1}},
        {"module": "gates.gate_b", "desc": "B", "passed": False, "detail": {"x": 2}},
    ])
    assert n == 2
    rows = gs.list_gate_results(run_id=run_id)
    assert len(rows) == 2
    got = {r["gate"]: r["passed"] for r in rows}
    assert got["gates.gate_a"] == 1 and got["gates.gate_b"] == 0
    # 幂等覆盖：同 run 重跑不产生重复行
    gs.record_gate_results(run_id, [{"module": "gates.gate_a", "desc": "A",
                                     "passed": False, "detail": {}}])
    assert len(gs.list_gate_results(run_id=run_id)) == 2


def test_governance_api_read_face(gdb):
    from interfaces import governance_api as api
    assert api.enabled() is False
    assert api.status()["enabled"] is False
    gs.init_db()
    gs.record_watermark("k", "pb", "v1")
    assert api.enabled() is True
    assert [w["artifact_key"] for w in api.watermarks()] == ["k"]
    assert api.watermark("k")["version"] == "v1"
    assert api.watermark("nope") is None
    assert api.edges() == []
    assert api.check()[0] is True
    assert api.db_path().endswith("governance.db")


def test_tags_never_hold_secrets(gdb):
    """守卫：水位/审计表只存版本与字段值，不得出现 token 字面量（防未来误用）。"""
    gs.init_db()
    gs.record_watermark("k", "pb", "v1")
    dump = json.dumps(gs.list_watermarks(), ensure_ascii=False).lower()
    for bad in ("token", "password", "secret", "api_key"):
        assert bad not in dump


# =========================================================================== #
# 阶段 2（2026-09-18）：元数据四表投影 + 比对断言
# =========================================================================== #
_DOCS = [
    {"doc_ref": "RFN-a", "kind": "regulatory", "title": "文件甲", "docno": "X〔2020〕1号",
     "docno_norm": "x20201", "source": "nfra", "timeliness_status": "valid",
     "row_json": "{}"},
    {"doc_ref": "IPN-b", "kind": "internal", "title": "制度乙", "docno": "",
     "source": "internal", "timeliness_status": "active", "row_json": "{}"},
]
_THEMES = [{"doc_ref": "RFN-a", "theme": "T1销售行为与消费者保护", "basis": "人工"}]
_RELS = [
    {"row_key": "k1", "relation_id": "REL-1", "relation": "basis", "src_kind": "regulatory",
     "src_ref": "RFN-a", "dst_ref": "RFN-z", "dst_class": "entity", "matched_by": "strict",
     "row_json": json.dumps({"relation_id": "REL-1", "confidence": 0.9})},
    {"row_key": "k2", "relation_id": "REL-2", "relation": "repeal", "src_kind": "internal",
     "src_ref": "IPN-b", "dst_kind": "internal", "dst_ref": "", "dst_key": "dk-2",
     "dst_class": "corpus", "matched_by": "title_contains",
     "row_json": json.dumps({"relation_id": "REL-2"})},
]
_TL = [{"state_key": "doc:x20201", "status": "valid", "prev_status": "pending",
        "replacement": "", "verification_source": "北大法宝",
        "last_checked_at": "2026-09-17 10:00:00", "changed_at": "2026-09-17 10:00:00"}]


def _project(**over):
    payload = {"documents": list(_DOCS), "theme_assigns": list(_THEMES),
               "relations": list(_RELS), "timeliness_rows": list(_TL)}
    payload.update(over)
    return gs.project_metadata(**payload)


def test_project_metadata_and_verify(gdb):
    gs.init_db()
    counts = _project()
    assert counts["document"] == 2 and counts["theme_assign"] == 1
    assert counts["relation"] == 2 and counts["timeliness_history"] == 1
    res = gs.verify_projection(documents=_DOCS, theme_assigns=_THEMES,
                               relations=_RELS, timeliness_rows=_TL)
    assert all(v["ok"] for v in res.values()), res
    assert gs.table_count("relation") == 2


def test_projection_is_full_replace_no_orphan(gdb):
    """投影语义：源删行 → 库不得残留（原增量 upsert 的静默分叉失败模式）。"""
    gs.init_db()
    _project()
    _project(documents=_DOCS[:1], theme_assigns=[], relations=_RELS[:1], timeliness_rows=[])
    assert gs.table_count("document") == 1
    assert gs.table_count("theme_assign") == 0
    assert gs.table_count("relation") == 1
    res = gs.verify_projection(documents=_DOCS[:1], theme_assigns=[],
                               relations=_RELS[:1], timeliness_rows=[])
    assert all(v["ok"] for v in res.values()), res
    # timeliness_history 为 append 模式：库保留历史（源为空 → 仍判 ok，无缺失）
    assert res["timeliness_history"]["mode"] == "append"
    assert res["timeliness_history"]["db_rows"] == 1


def test_verify_detects_missing_history_row(gdb):
    """append 模式断言：源有而库无 → 必须判分叉（防"历史缺行"被静默放过）。"""
    gs.init_db()
    _project()
    extra = [_TL[0], {"state_key": "doc:zzz", "status": "valid", "prev_status": "",
                      "replacement": "", "verification_source": "北大法宝",
                      "last_checked_at": "2026-09-18 09:00:00", "changed_at": ""}]
    res = gs.verify_projection(documents=_DOCS, theme_assigns=_THEMES,
                               relations=_RELS, timeliness_rows=extra)
    assert res["timeliness_history"]["ok"] is False
    assert res["timeliness_history"]["missing"] == 1


def test_timeliness_is_append_only_dedup(gdb):
    """时效表保留历史观测：同键重复投影不追加，新增观测追加。"""
    gs.init_db()
    _project()
    _project()                                   # 重复投影
    assert gs.table_count("timeliness_history") == 1
    nxt = [dict(_TL[0], status="repealed", last_checked_at="2026-09-18 10:00:00")]
    _project(timeliness_rows=nxt)
    assert gs.table_count("timeliness_history") == 2


def test_row_json_roundtrip_fidelity(gdb):
    """`row_json` 保证消费方零损耗取回原行（类型不失真）。"""
    gs.init_db()
    _project()
    rows = {r["relation_id"]: r for r in gs.table_rows("relation")}
    assert rows["REL-1"]["_src"]["confidence"] == 0.9
    assert rows["REL-1"]["_src"]["relation_id"] == "REL-1"


def test_table_digest_detects_change(gdb):
    gs.init_db()
    _project()
    d1 = gs.table_digest("document")
    assert d1 == gs.table_digest("document", _DOCS)
    changed = [dict(_DOCS[0], timeliness_status="repealed"), _DOCS[1]]
    assert gs.table_digest("document", changed) != d1


def test_export_snapshot(gdb, tmp_path):
    gs.init_db()
    _project()
    out = gs.export_snapshot(str(tmp_path / "exports"))
    names = {i["name"] for i in out["items"]}
    assert names == {"document", "theme_assign", "timeliness_history"}, "relation 不导出（已有事实源）"
    assert os.path.exists(out["manifest"])
    man = json.load(open(out["manifest"], encoding="utf-8"))
    assert man["schema_version"] == gs.SCHEMA_VERSION and man["items"]
    # 路径一律仓库相对（防盘符字面量门禁命中）
    assert all(not i["path"].startswith(("d:", "D:", "C:")) for i in man["items"])


# =========================================================================== #
# 阶段 3（2026-09-18）：relations_api 优先查库 + 跨模块直连门禁
# =========================================================================== #
def test_relations_api_prefers_db(gdb):
    gs.init_db()
    _project()
    from interfaces import relations_api as ra
    rows = ra.load("all")
    assert len(rows) == 2 and {r["relation_id"] for r in rows} == {"REL-1", "REL-2"}
    assert [r["relation_id"] for r in ra.load("regulatory")] == ["REL-1"]
    assert [r["relation_id"] for r in ra.load("internal")] == ["REL-2"]
    assert ra.load("cross") == []
    assert [r["relation_id"] for r in ra.by_src("RFN-a")] == ["REL-1"]
    assert [r["relation_id"] for r in ra.by_dst("dk-2")] == ["REL-2"]


def test_relations_api_degrades_when_db_absent(gdb):
    """库未启用 → 回退读事实源文件（不得抛、不得阻断分析路径）。"""
    from interfaces import relations_api as ra
    assert isinstance(ra.load("all"), list)      # 真实仓文件存在则返回行，不存在则 []


def test_gate_no_cross_module_import_passes_on_repo():
    """modules/ 零跨模块直连（阶段 3 收口成果的守卫）。"""
    from gates import gate_no_cross_module_import as g
    ok, detail = g.run()
    assert ok, f"存在跨模块直连: {detail.get('new')}"
    assert detail["findings"] == 0


def test_clean_index_api_is_live(gdb):
    """clean_index_api 不再是 NotImplementedError 空壳（阶段 3 实装）。"""
    from interfaces import clean_index_api as ci
    assert callable(ci.get_clean_index) and callable(ci.latest_csv_path)
    assert hasattr(ci.get_clean_index_api(), "latest_csv_path")


# =========================================================================== #
# 阶段 4（2026-09-18）：判据切换共用入口 wm_status
# =========================================================================== #
def test_wm_status_three_states(gdb, monkeypatch):
    from interfaces import governance_api as api
    # 显式控制轮次：本用例验证**跨轮**陈旧（v2 §3.3.1 后同轮推进不再判 stale →
    # 必须由测试自己指定 round，避免受环境遗留的 REG_ORCH_RUN_ID 影响）
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    assert api.wm_status("relations_index")[0] == "unknown"      # 库未启用
    gs.init_db()
    assert api.wm_status("relations_index")[0] == "unknown"      # 未登记
    gs.record_watermark("cleaned:gov", "clean", "v1", round_id="R1")
    gs.record_watermark("relations_index", "extract", "r1",
                        inputs={"cleaned:gov": "v1"}, round_id="R1")
    state, detail = api.wm_status("relations_index")
    assert state == "ok" and detail["edges"] == 1
    assert detail["within_round"] == 0
    gs.record_watermark("cleaned:gov", "clean", "v2", round_id="R2")   # 更晚轮次推进
    state, detail = api.wm_status("relations_index")
    assert state == "stale"
    assert detail["stale"][0]["dep"] == "cleaned:gov"
    assert detail["stale"][0]["declared"] == "v1" and detail["stale"][0]["current"] == "v2"


def test_wm_status_ok_within_round(gdb):
    """v2 §3.3.1 新增第 4 态：**同一轮**内上游被再次推进 → 不判 stale（链内顺序特性）。

    真实场景：`clauses:{src}` 在阶段 1 登记（读阶段 1 的 cleaned 版本），
    阶段 2 时效回写后 `cleaned:{src}` 重新登记 → 版本变了但同属一轮。
    """
    from interfaces import governance_api as api
    gs.init_db()
    gs.record_watermark("cleaned:gov", "clean", "v1", round_id="R9")
    gs.record_watermark("clauses:gov", "clause", "c1",
                        inputs={"cleaned:gov": "v1"}, round_id="R9")
    gs.record_watermark("cleaned:gov", "clean", "v2", round_id="R9")   # 同轮推进
    state, detail = api.wm_status("clauses:gov")
    assert state == "ok", "同轮顺序特性不得阻断"
    assert detail["within_round"] == 1
    assert detail["within_round_deps"] == ["cleaned:gov"]
