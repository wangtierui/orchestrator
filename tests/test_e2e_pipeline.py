# -*- coding: utf-8 -*-
"""
tests/test_e2e_pipeline.py — 全流程端到端验收（P0–P7 成果固化，2026-09-08）

定位：对「新仓现行产物」做确定性一致性断言（快速、幂等、不重跑耗时采集/清洗脚本）。
覆盖数据链：clean_index 五源最新快照 → rfn 归属表/索引 → 40 底座/11 明细契约 →
internal 107 制度(index/align/merged) → reconcile 桥 → 交付门禁。
任一断言失败 = 迁移或数据被破坏，须先修复再交付。

运行：python -m pytest tests/test_e2e_pipeline.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,
          os.path.join(ROOT, "modules"),
          os.path.join(ROOT, "modules", "regulatory_classifier"),
          os.path.join(ROOT, "modules", "regulatory_scrapers"),
          os.path.join(ROOT, "std_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

MOD_CLASS = os.path.join(ROOT, "modules", "regulatory_classifier")
DATA = os.path.join(MOD_CLASS, "data")
MOD_SCRAPERS = os.path.join(ROOT, "modules", "regulatory_scrapers")
IBP = os.path.join(ROOT, "modules", "internal_policy_base")


# ---------------- 层0：clean_index 五源最新快照 ----------------
def test_clean_index_five_sources_latest():
    from clean_index import get_clean_index  # noqa: PLC0415
    idx = get_clean_index()
    live = [s for s in ("gov", "mof", "nfra", "pbc", "supp")
            if idx.latest_csv_path(s) and os.path.exists(idx.latest_csv_path(s))]
    assert len(live) == 5, f"五源快照缺失: {live}"


# ---------------- 层1：RFN 归属表 / 索引 ----------------
def test_attr_and_index_count():
    import csv
    with open(os.path.join(DATA, "人身保险公司-文件归属表.csv"), encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 1000, f"归属表过少: {len(rows)}"
    assert all(len(r.get("监管文件编号", "")) == 20 for r in rows[:10])  # RFN-<16hex>
    from rfn import get_index  # noqa: PLC0415
    idx = get_index()
    assert len(idx.all_rfns()) == len(rows)


# ---------------- 层2：底座/明细契约 ----------------
def test_base_and_detail_contract():
    from gates.gate_contract import run  # noqa: PLC0415
    ok, detail = run()
    assert ok, detail.get("problems", [])[:5]
    assert detail["checked"]["base_files"]["count"] == 40
    assert detail["checked"]["detail_tables"]["count"] == 11


def test_rfn_sync_consistency():
    from gates.gate_rfn_sync import run  # noqa: PLC0415
    ok, detail = run()
    assert ok, detail.get("problems", [])[:5]


# ---------------- 层3：internal 107 制度 ----------------
def test_internal_policy_index():
    p = os.path.join(IBP, "data", "internal_policy_index.json")
    assert os.path.exists(p), "internal_policy_index.json 缺失（先跑 internal index）"
    idx = json.load(open(p, encoding="utf-8"))
    # 动态基线（2026-09-12 部门制度摄取扩至 957+；不硬编码具体条数）
    assert idx["count"] >= 100, f"内部制度数异常: {idx['count']}"
    ipns = [r["ipn"] for r in idx["records"]]
    assert len(set(ipns)) == len(ipns), "主索引 IPN 非唯一（去重口径失效）"


def test_internal_align_merged_present():
    for fn in ("align_result.json", "merged_view.json"):
        p = os.path.join(IBP, "data", fn)
        assert os.path.exists(p), f"{fn} 缺失（先跑 internal align/merged）"
    merged = json.load(open(os.path.join(IBP, "data", "merged_view.json"), encoding="utf-8"))
    assert merged["count"] >= 100
    ipns = [r["ipn"] for r in merged["records"]]
    assert len(set(ipns)) == len(ipns), "merged_view IPN 非唯一"
    assert merged["stat"]["with_rfn_refs"] > 0, "merged_view 无 RFN 引用关联"


def test_internal_clauses_md_generated():
    import glob
    mds = glob.glob(os.path.join(IBP, "data", "processed", "*_clauses.md"))
    assert len(mds) >= 30, f"条文 MD 视图不足: {len(mds)}（先跑 backfill_clauses）"


# ---------------- 层2b：clause_index 条文产物（② 固定节点） ----------------
def test_clause_index_schema():
    from clause_index import latest_clause_path, validate_schema  # noqa: PLC0415
    for s in ("gov", "mof", "nfra", "pbc", "supp"):
        assert latest_clause_path(s), f"{s} clause 产物缺失（先跑 clean 管道固定节点）"
    r = validate_schema()
    assert r["consistent"], r["problems"][:5]
    assert r["files"] >= 1000 and r["articles"] >= 10000


# ---------------- 层4：reconcile 桥表 / 漂移状态 ----------------
def test_reconcile_bridge_and_state():
    import csv as _csv
    bp = os.path.join(DATA, "rfn_clean_bridge.csv")
    sp = os.path.join(DATA, "rfn_drift_state.json")
    assert os.path.exists(bp), "桥表缺失（先跑 reconcile_clean_drift）"
    assert os.path.exists(sp), "漂移 state 缺失"
    with open(bp, encoding="utf-8-sig", newline="") as f:
        b = list(_csv.DictReader(f))
    assert len(b) >= 800, f"桥表过少: {len(b)}"
    state = json.load(open(sp, encoding="utf-8"))
    assert state.get("clean_snapshots", {}).get("nfra"), "state 无快照记录"


# ---------------- 层5：时效单源传播（SSOT 断言） ----------------
def test_timeliness_ssot():
    from gates.gate_timeliness_ssot import run  # noqa: PLC0415
    ok, detail = run()
    assert ok, detail.get("problems", [])[:5]


# ---------------- 层6：交付门禁全绿 ----------------
def test_all_gates_pass():
    from gates import GatesRunner  # noqa: PLC0415
    ok, results = GatesRunner().run()
    failed = [r["desc"] for r in results if not r["passed"]]
    assert ok, f"门禁未全绿: {failed}"


# ---------------- 层7：主题报告产物 ----------------
def test_theme_reports_present():
    import glob
    reps = glob.glob(os.path.join(MOD_CLASS, "docs", "reports", "T*主题报告.md"))
    assert len(reps) == 11, f"主题报告数 {len(reps)} != 11"
