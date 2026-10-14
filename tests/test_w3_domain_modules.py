# -*- coding: utf-8 -*-
"""tests/test_w3_domain_modules.py — W3 领域层测试（reg_frameworks / implementation_audit）。"""
from __future__ import annotations

import csv
import os

import pytest

import paths


def test_framework_rejects_uncontrolled_values():
    from modules.reg_frameworks.regfw_model import Framework
    with pytest.raises(ValueError, match="FRAMEWORK_ID"):
        Framework(fid="bad", name="x", regulator="y", cadence="monthly")
    with pytest.raises(ValueError, match="REPORTING_CADENCE"):
        Framework(fid="east2", name="x", regulator="y", cadence="每小时")
    with pytest.raises(ValueError, match="deadline_day"):
        Framework(fid="east2", name="x", regulator="y", cadence="monthly", deadline_day=99)


def test_data_item_and_table_validation():
    from modules.reg_frameworks.regfw_model import DataItem, DataTable
    with pytest.raises(ValueError, match="ITEM_TYPE"):
        DataItem(iid="i", tid="t", name="n", item_type="不明")
    with pytest.raises(ValueError, match="theme"):
        DataTable(tid="t", fid="east2", name="表", theme="  ")


def test_clause_item_map_confidence_ceiling():
    from modules.reg_frameworks.regfw_model import ClauseItemMap
    with pytest.raises(ValueError, match="上限"):
        ClauseItemMap(map_id="m", rfn="r", article_no="a", iid="i",
                      method="semantic", confidence=0.95)
    ok = ClauseItemMap(map_id="m", rfn="r", article_no="a", iid="i",
                       method="keyword", confidence=0.8, status="review")
    assert ok.needs_review is True


def test_gap_model_validation():
    from modules.implementation_audit.implaudit_model import Gap
    with pytest.raises(ValueError, match="GAP_CLASS"):
        Gap(gid="g", ipn="IPN-1", gap_class="不明", severity="high")
    with pytest.raises(ValueError, match="GAP_SEVERITY"):
        Gap(gid="g", ipn="IPN-1", gap_class="partial", severity="致命")


def test_contract_anchors_match_counts():
    from modules.implementation_audit import implaudit_model as n
    from modules.reg_frameworks import regfw_model as m
    assert len(m.FRAMEWORK_FIELDS) == 6
    assert len(m.DATA_TABLE_FIELDS) == 7
    assert len(m.DATA_ITEM_FIELDS) == 7
    assert len(m.CLAUSE_ITEM_MAP_FIELDS) == 8
    assert len(n.IMPL_RECORD_FIELDS) == 7
    assert len(n.EVIDENCE_FIELDS) == 7
    assert len(n.GAP_FIELDS) == 10
    assert "METHOD_CEILING" not in m.CLAUSE_ITEM_MAP_FIELDS


def test_extract_csv_builds_table_and_items(tmp_path):
    from modules.reg_frameworks.materialize import extract_file
    p = tmp_path / "east_a.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["表名", "保费收入金额", "保单件数", "综合成本率"])
        w.writerow(["A表", "1", "2", "0.9"])
    r = extract_file(str(p), "east2", theme="寿险")
    assert len(r["tables"]) == 1 and len(r["items"]) == 3
    kinds = {i["name"]: i["item_type"] for i in r["items"]}
    assert kinds["保费收入金额"] == "financial"
    assert kinds["保单件数"] == "statistic"
    assert all(i["keywords"] for i in r["items"])


def test_extract_rejects_unsupported_extension(tmp_path):
    from modules.reg_frameworks.materialize import extract_file
    p = tmp_path / "x.docx"
    p.write_text("x", encoding="utf-8")
    r = extract_file(str(p), "east2")
    assert r["skipped"] == 1 and "不支持" in r["reason"]


def test_store_upsert_is_idempotent(tmp_path, monkeypatch):
    from modules.reg_frameworks import regfw_store as store
    monkeypatch.setattr(store, "data_dir", lambda: str(tmp_path))
    rows = [{"tid": "t1", "name": "A"}, {"tid": "t2", "name": "B"}]
    assert store.write_rows(store.F_TABLES, rows, key="tid")["added"] == 2
    r2 = store.write_rows(store.F_TABLES, rows, key="tid")
    assert r2["added"] == 0 and r2["updated"] == 0
    assert store.write_rows(store.F_TABLES, [{"tid": "t1", "name": "A2"}], key="tid")["updated"] == 1
    assert len(store.list_tables()) == 2


def test_store_bidirectional_lookup(tmp_path, monkeypatch):
    from modules.reg_frameworks import regfw_store as store
    monkeypatch.setattr(store, "data_dir", lambda: str(tmp_path))
    store.write_rows(store.F_MAPS, [
        {"map_id": "m1", "rfn": "R1", "iid": "i1", "status": "confirmed"},
        {"map_id": "m2", "rfn": "R2", "iid": "i1", "status": "review"},
    ], key="map_id")
    assert [m["map_id"] for m in store.list_maps(rfn="R1")] == ["m1"]
    assert {m["map_id"] for m in store.list_maps(iid="i1")} == {"m1", "m2"}
    assert [m["map_id"] for m in store.list_maps(status="review")] == ["m2"]


def test_explicit_and_keyword_mapping():
    from modules.reg_frameworks.mapping import map_clause
    from modules.reg_frameworks.regfw_model import DataItem
    # i1：名称在正文 → explicit；i2：名称不在正文、仅关键词部分命中 → keyword
    items = [DataItem(iid="i1", tid="t", name="保费收入", keywords=("保费收入", "保费")),
             DataItem(iid="i2", tid="t", name="资本充足率",
                      keywords=("资本充足率", "资本", "充足率"))]
    r = map_clause("RF-1", "第一条", "应报送保费收入与资本口径情况。", items)
    by_iid = {m["iid"]: m for m in r["maps"]}
    assert by_iid["i1"]["method"] == "explicit" and by_iid["i1"]["confidence"] == 1.0
    assert by_iid["i2"]["method"] == "keyword" and by_iid["i2"]["confidence"] <= 0.9
    assert len(r["maps"]) == 2


def test_low_confidence_mapping_enters_worklist():
    from modules.reg_frameworks.mapping import map_clause
    from modules.reg_frameworks.regfw_model import DataItem
    # 名称**不在**正文（否则 explicit 优先命中、不会进人工闸门），仅关键词组部分命中
    items = [DataItem(iid="i9", tid="t", name="资本充足率",
                      keywords=("核心偿付能力充足率", "资本充足率", "综合偿付能力"))]
    r = map_clause("RF-2", "第二条", "正文只提到「综合偿付能力」口径。", items)
    assert r["worklist"] and r["worklist"][0]["kind"] == "regfw_map_review"


def test_semantic_never_auto_confirms(monkeypatch):
    import modules.reg_frameworks.mapping as mp
    from modules.reg_frameworks.regfw_model import DataItem
    assert mp.map_clause("RF-3", "第三条", "任意正文", [])["maps"] == []
    items = [DataItem(iid="i1", tid="t", name="指标X", keywords=("完全不同的词",))]
    monkeypatch.setattr(mp, "semantic_hits",
                        lambda clause, its: [(its[0], 0.75, "semantic:sim")])
    r2 = mp.map_clause("RF-4", "第四条", "任意正文", items)
    sem = [m for m in r2["maps"] if m["method"] == "semantic"]
    assert sem and sem[0]["status"] == "review"


def _gap_for(exec_status, n_evidence: int, *, recommended: bool = False):
    from modules.implementation_audit.engine import evaluate
    from modules.implementation_audit.implaudit_model import (
        ClauseRequirement,
        EvidenceMaterial,
        ImplementationRecord,
    )
    req = ClauseRequirement(ipn="IPN-X", article_no="第一条",
                            requirement_level="recommended" if recommended else "required")
    recs = [] if exec_status is None else [
        ImplementationRecord(rid="r", dept="D", period="2026-09",
                             exec_status=exec_status, ipn="IPN-X", article_no="第一条")]
    evs = [EvidenceMaterial(eid=f"e{i}", ipn="IPN-X", article_no="第一条",
                            kind="report", path_or_ref=f"f{i}.pdf")
           for i in range(n_evidence)]
    return evaluate([req], recs, evs)


def test_gap_not_executed_high():
    g = _gap_for(None, 0)
    assert g and g[0].gap_class == "not_executed" and g[0].severity == "high"


def test_gap_partial_medium():
    g = _gap_for("partial", 0)
    assert g and g[0].gap_class == "partial" and g[0].severity == "medium"


def test_gap_no_evidence_when_executed_without_evidence():
    g = _gap_for("executed", 0)
    assert g and g[0].gap_class == "no_evidence"


def test_gap_insufficient_evidence_below_min_count():
    g = _gap_for("executed", 1)
    assert g and g[0].gap_class == "insufficient_evidence" and g[0].severity == "low"


def test_no_gap_when_execution_and_evidence_sufficient():
    assert _gap_for("executed", 2) == []


def test_recommended_clause_not_judged():
    assert _gap_for(None, 0, recommended=True) == []


def test_rules_cover_all_gap_classes():
    from config.enums import GAP_CLASS
    from modules.implementation_audit.engine import load_rules
    assert {str(r["gap_class"]) for r in load_rules()} == set(GAP_CLASS)


def test_execution_status_normalization():
    from modules.implementation_audit.ingest import normalize_status
    assert normalize_status("已执行")[0] == "executed"
    assert normalize_status(" 部分执行 ")[0] == "partial"
    assert normalize_status("未执行")[0] == "not_executed"
    val, hit = normalize_status("看不懂")
    assert hit is False and val == "not_executed"


def test_ingest_column_alias_and_missing_column(tmp_path):
    from modules.implementation_audit.ingest import parse_file
    p = tmp_path / "ledger.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["部门", "期间", "IPN", "条款号", "执行状态"])
        w.writerow(["寿险部", "2026-09", "IPN-001", "第一条", "已执行"])
    r = parse_file(str(p))
    assert len(r["records"]) == 1 and r["records"][0]["exec_status"] == "executed"
    bad = tmp_path / "bad.csv"
    with open(bad, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerow(["随便", "几列"])
    r2 = parse_file(str(bad))
    assert r2["skipped"] == 1 and "缺少必需列" in r2["reason"]


def test_tabular_shared_primitive_used_by_both_modules():
    from std_lib.common_lib import tabular
    assert tabular.SUPPORTED == (".csv", ".xlsx")
    src_e = open(os.path.join(paths.ROOT, "modules", "reg_frameworks", "materialize.py"),
                 encoding="utf-8").read()
    src_i = open(os.path.join(paths.ROOT, "modules", "implementation_audit", "ingest.py"),
                 encoding="utf-8").read()
    assert "from modules.reg_frameworks" not in src_i
    assert "tabular as _tabular" in src_e and "tabular as _tabular" in src_i
