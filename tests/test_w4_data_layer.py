# -*- coding: utf-8 -*-
"""tests/test_w4_data_layer.py — W4 数据层强化单测（方案 §6.3.1，D-16）

覆盖三件事（全部用临时治理库 + 临时产物目录，不触碰生产 data/）：
  ① `GOV_ARTIFACTS` 声明面：`raw:{src}` 观察型登记 + `cleaned:{src}` 声明 raw 输入边；
  ② `_artifact_paths` 单映射：覆盖全部会被声明为 inputs 的键族（登记与核验同源）；
  ③ resume 第三条件（**并行计算、只披露不生效**）：「链外 raw 更新 → clean 不跳」
     的机器可判面——文件已变但水位未更新时 v2 剔除该步、差异披露并留痕台账。
"""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    """隔离的治理库（经 REG_ORCH_GOVERNANCE_DB 覆盖，绝不落到生产库）。"""
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    monkeypatch.delenv("REG_ORCH_RUN_ID", raising=False)
    monkeypatch.delenv("REG_ORCH_RESUME_V2", raising=False)
    from std_lib.common_lib import governance_store

    governance_store.init_db()
    return governance_store


def _load_rpr():
    import run_production_refresh as rpr

    return rpr


# --------------------------------------------------------------------------- #
# ① 声明面
# --------------------------------------------------------------------------- #
def test_raw_registered_and_cleaned_declares_raw_input():
    """raw:{src} 为观察型（stage 0）；cleaned:{src} 声明 raw:{src} 输入边。"""
    rpr = _load_rpr()
    spec = rpr.GOV_ARTIFACTS.get("raw:{src}")
    assert spec is not None, "raw:{src} 未登记（W4 §6.3.1：raw 纳入水位体系）"
    assert spec.get("stage") == "0"
    assert spec.get("inputs") in (None, []), "raw 是链外写方 → 必须观察型（不得声明 inputs）"
    assert rpr.GOV_ARTIFACTS["cleaned:{src}"].get("inputs") == ["raw:{src}"]


def test_artifact_paths_covers_all_declared_inputs():
    """每个会被声明为 inputs 的键族都有路径映射（登记与第三条件核验同源）。"""
    rpr = _load_rpr()
    deps = set()
    for spec in rpr.GOV_ARTIFACTS.values():
        for d in spec.get("inputs") or []:
            deps.add(d.replace("{src}", "gov"))
    for d in sorted(deps):
        # 映射必须认识该键（返回 list；文件不存在时为空列表亦可，但不得 KeyError/异常）
        assert isinstance(rpr._artifact_paths(d), list), f"{d} 无路径映射"


# --------------------------------------------------------------------------- #
# ③ 第三条件：链外 raw 更新 → clean 不跳（并行计算，v1 仍生效）
# --------------------------------------------------------------------------- #
def _fake_artifact_paths(monkeypatch, rpr, raw_path: str, cleaned_path: str):
    """把 raw/cleaned 两族路径钉到临时文件（其余键返回空 → 无证据不参与判定）。"""
    real = rpr._artifact_paths

    def fake(key: str) -> list[str]:
        if key.startswith("raw:"):
            return [raw_path]
        if key.startswith("cleaned:"):
            return [cleaned_path]
        return real(key) if key in ("rfn_attr", "rfn_theme") else []

    monkeypatch.setattr(rpr, "_artifact_paths", fake)


def test_resume_third_condition_detects_out_of_chain_raw_write(gs, tmp_path, monkeypatch):
    """台账实证（方案 §6.5 出口判据）：链外改写 raw 文件、水位未更新 →
    v2 跳过集剔除 clean:gov，且差异披露带证据（v1 跳过集不变——只披露不生效）。"""
    rpr = _load_rpr()
    raw = tmp_path / "gov_laws.jsonl"
    cleaned = tmp_path / "gov_cleaned.jsonl"
    raw.write_text('{"a":1}\n', encoding="utf-8")
    cleaned.write_text('{"b":1}\n', encoding="utf-8")

    # 上一轮执行记录：cleaned:gov 在 raw 版本 V0 上算出（inputs_json 留痕）
    v0 = gs.version_of_file(str(raw))
    gs.record_watermark("raw:gov", "collectors.gov.collector", v0, run_id="run-prev", round_id="run-prev")
    gs.record_watermark(
        "cleaned:gov",
        "regulatory_scrapers.clean.run_clean_pipeline.main",
        gs.version_of_file(str(cleaned)),
        inputs={"raw:gov": v0},
        run_id="run-prev",
    )
    # 步骤台账：clean:gov 上次 rc=0（v1 判据下可跳）
    gs.step_record("clean:gov", 0, run_id="run-prev")
    gs.step_record("gates", 0, run_id="run-prev")

    _fake_artifact_paths(monkeypatch, rpr, str(raw), str(cleaned))

    # 基线：无链外改写 → v2 == v1（clean:gov 可跳）
    plan = rpr._resume_plan()
    assert plan["enabled"] is True
    assert "clean:gov" in plan["steps"]
    assert plan["skip_delta"] == []
    assert "clean:gov" in plan["steps_v2"]

    # 链外写方：raw 文件被改写，但**未**登记水位（水位表停在 V0）
    raw.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    plan2 = rpr._resume_plan()
    assert "clean:gov" in plan2["steps"], "v1 跳过集不得被第三条件影响（只披露不生效）"
    assert "clean:gov" not in plan2["steps_v2"], "v2 必须剔除：链外 raw 已改写"
    hit = [d for d in plan2["skip_delta"] if d["step"] == "clean:gov"]
    assert hit and hit[0]["dep"] == "raw:gov"
    assert hit[0]["declared"] == v0
    assert hit[0]["file_now"] != v0
    # 台账留痕（audit_log）：并行验证期的差异可审计
    audits = gs.list_audit(target="resume")
    assert any(a["action"] == "resume_plan_v2" for a in audits)


def test_resume_third_condition_ignores_legitimate_reproduction(gs, tmp_path, monkeypatch):
    """合法再生产（文件变 + **同轮**水位已跟进，即 ok_within_round）不算链外改写——
    否则第三条件会把每轮都重写的产物误判为"必须重跑"（假阳性）。

    反面对照（跨轮水位跟进）由 v1 水位判据拦截：stale → 全量重跑，根本进不到 v2。
    """
    rpr = _load_rpr()
    raw = tmp_path / "gov_laws.jsonl"
    cleaned = tmp_path / "gov_cleaned.jsonl"
    raw.write_text('{"a":1}\n', encoding="utf-8")
    cleaned.write_text('{"b":1}\n', encoding="utf-8")
    v0 = gs.version_of_file(str(raw))
    gs.record_watermark("raw:gov", "collectors.gov.collector", v0, run_id="run-prev", round_id="run-prev")
    gs.record_watermark(
        "cleaned:gov",
        "regulatory_scrapers.clean.run_clean_pipeline.main",
        gs.version_of_file(str(cleaned)),
        inputs={"raw:gov": v0},
        run_id="run-prev",
        round_id="run-prev",
    )
    gs.step_record("clean:gov", 0, run_id="run-prev")
    _fake_artifact_paths(monkeypatch, rpr, str(raw), str(cleaned))

    # 同轮合法再生产：raw 再采集（文件变）+ 水位同步登记到新版本（同 run-prev 轮）
    raw.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    gs.record_watermark(
        "raw:gov", "collectors.gov.collector", gs.version_of_file(str(raw)),
        run_id="run-prev", round_id="run-prev",
    )
    plan = rpr._resume_plan()
    assert "clean:gov" in plan["steps_v2"], "水位已跟进（同轮）的合法再生产不得判为链外改写"
    assert plan["skip_delta"] == []


def test_resume_v2_switch_is_opt_in(monkeypatch):
    """第三条件生效须显式 `REG_ORCH_RESUME_V2=1`（默认关：并行验证一周后才切）。"""
    rpr = _load_rpr()
    monkeypatch.delenv("REG_ORCH_RESUME_V2", raising=False)
    assert os.environ.get("REG_ORCH_RESUME_V2") != "1"
    # 开关语义在主链 main() 消费；此处固化"默认不生效"的约定本身
    assert rpr._resume_plan.__doc__ and "只披露不生效" in rpr._resume_plan.__doc__


# --------------------------------------------------------------------------- #
# §6.3.3 .tmp sweeper（写入侧保证之外的崩溃残留兜底）
# --------------------------------------------------------------------------- #
def test_sweep_stale_tmp_only_removes_old(tmp_path):
    """24h 内的 .tmp 不动（可能是并发写入方在用）；超龄的删除并披露清单。"""
    import time

    from std_lib.common_lib.fs_lock import sweep_stale_tmp

    old = tmp_path / ".tmp_old.json"
    old.write_text("x", encoding="utf-8")
    fresh = tmp_path / ".tmp_fresh.json"
    fresh.write_text("x", encoding="utf-8")
    other = tmp_path / "keep.json"
    other.write_text("x", encoding="utf-8")
    # 把 old 的 mtime 拨到 48h 前（跨平台：os.utime）
    ts = time.time() - 48 * 3600
    os.utime(old, (ts, ts))

    res = sweep_stale_tmp(str(tmp_path), max_age_hours=24, apply=True)
    assert res["found"] == 1 and res["files"][0]["file"] == ".tmp_old.json"
    assert not old.exists(), "超龄 .tmp 应被删除"
    assert fresh.exists(), "24h 内的 .tmp 可能是并发写入方在用，不得删"
    assert other.exists()

    # dry-run（apply=False）只披露不删
    ts2 = time.time() - 48 * 3600
    os.utime(fresh, (ts2, ts2))
    res2 = sweep_stale_tmp(str(tmp_path), max_age_hours=24, apply=False)
    assert res2["found"] == 1 and fresh.exists(), "apply=False 不得删文件"


def test_sweep_stale_tmp_skips_heavy_dirs(tmp_path):
    """external/graphify-out 等重目录不遍历（启动侧不得为兜底付全仓遍历成本）。"""
    from std_lib.common_lib.fs_lock import _TMP_SKIP_DIRS, sweep_stale_tmp

    heavy = tmp_path / "external" / "x"
    heavy.mkdir(parents=True)
    (heavy / ".tmp_big").write_text("x", encoding="utf-8")
    res = sweep_stale_tmp(str(tmp_path), max_age_hours=0, apply=True)
    assert res["found"] == 0, "external/ 必须被剪枝（第三方资产，永不随仓分发）"
    assert "external" in _TMP_SKIP_DIRS and "corpus" in _TMP_SKIP_DIRS


# --------------------------------------------------------------------------- #
# §6.3.3 发布版本史（append-only）
# --------------------------------------------------------------------------- #
def test_append_publish_history_is_append_only(tmp_path):
    """每次发布追加一行（manifest 自身 sha + 计数）；重复发布逐行累积不覆盖。"""
    from base_publish import append_publish_history

    mp = tmp_path / "publish_manifest.json"
    manifest = {
        "base": "external",
        "snapshot_date": "2026-10-15",
        "schema_version": "1.0.0",
        "generated_at": "2026-10-15 01:00:00",
        "counts": {"records": 3},
        "files": {"external_records.jsonl": {"sha256": "a" * 64, "count": 3}},
    }
    mp.write_text("{}", encoding="utf-8")
    hist = append_publish_history(str(mp), manifest)
    assert os.path.basename(hist) == "publish_manifest_history.jsonl"
    lines = open(hist, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["base"] == "external" and row["counts"] == {"records": 3}
    assert len(row["manifest_sha256"]) == 64, "版本史自证：该行哈希描述的正是刚落盘的 manifest"

    # 第二次发布（manifest 内容变化）→ 追加第二行，首行不动
    mp.write_text('{"v":2}', encoding="utf-8")
    append_publish_history(str(mp), {**manifest, "counts": {"records": 4}})
    rows = [json.loads(x) for x in open(hist, encoding="utf-8") if x.strip()]
    assert len(rows) == 2
    assert rows[0]["counts"] == {"records": 3}, "append-only：历史行不得被改写"
    assert rows[1]["manifest_sha256"] != rows[0]["manifest_sha256"], "manifest 变了 → 指纹必须变"


# --------------------------------------------------------------------------- #
# §6.3.2 双轨决策：clauses MD 默认关（ADR-0013），CSV 保留（有生产消费面）
# --------------------------------------------------------------------------- #
def test_clause_md_flag_defaults_off(tmp_path, monkeypatch):
    """ADR-0013：with_md 默认 False——链上不再每轮重写 147MB 渲染视图；
    `--md`/with_md=True 时按需生成（可逆，历史产物不删）。"""
    import datetime

    import clause_index

    clause_dir = tmp_path / "clauses"
    clause_dir.mkdir()
    # 日期**动态生成**（gate_hardcoded_snapshots N-3：测试源码不得含 cleaned 快照日期字面量）
    stamp = datetime.date.today().strftime("%Y%m%d")
    cleaned = tmp_path / f"gov_cleaned_{stamp}.jsonl"
    rec = {
        "dedup_key": "dk1",
        "source_url": "https://example.com/1",
        "document_number": "测试〔2026〕1号",
        "title": "测试办法",
        "body_text": "第一条 为了测试，制定本办法。\n第二条 本办法自发布之日起施行。",
    }
    cleaned.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

    class _FakeIdx:
        def latest_jsonl_path(self, _src):
            return str(cleaned)

    monkeypatch.setattr(clause_index, "CLAUSE_DIR", str(clause_dir))
    monkeypatch.setattr(clause_index, "_CLAUSE_HISTORY_DIR", str(clause_dir / "history"))
    monkeypatch.setattr(clause_index, "_STATE_PATH", str(clause_dir / "state.json"))
    monkeypatch.setattr(clause_index, "get_clean_index", _FakeIdx)
    monkeypatch.setattr(clause_index, "_load_rfn_bridge", lambda: ({}, {}))
    monkeypatch.setattr(clause_index, "_make_rfn_title_lookup", lambda _d: lambda _t: "")
    monkeypatch.setattr(clause_index, "_rfn_by_docno_fallback", lambda _d: "")

    res = clause_index.build_clause_index()  # 默认：不写 MD
    assert res["gov"]["built"] is True
    # ⚠️ 断言侧也必须用**动态日期**（与输入侧同一个 stamp）：曾硬编码 `20261015`，
    # 日期一滚动（2026-10-16）即稳定失败——输入侧遵守了 gate_hardcoded_snapshots N-3，
    # 断言侧却漏改，属"写当天不可见、跨日必现"的时间炸弹。
    out_jsonl = clause_dir / f"gov_clauses_{stamp}.jsonl"
    out_md = clause_dir / f"gov_clauses_{stamp}.md"
    assert out_jsonl.exists(), f"JSONL（事实源）必须照常产出：{out_jsonl.name}"
    assert not out_md.exists(), f"默认不得生成 MD 渲染视图：{out_md.name}"
    state = json.loads((clause_dir / "state.json").read_text(encoding="utf-8"))
    assert state["gov"]["md_path"] == "", "state 须如实反映 MD 未生成"

    res2 = clause_index.build_clause_index(rebuild=True, with_md=True)  # 按需开启
    assert res2["gov"]["built"] is True
    md = out_md
    assert md.exists(), "--md/with_md=True 须按需生成"
    assert "第一条" in md.read_text(encoding="utf-8"), "MD 内容仍是渲染视图语义"


# --------------------------------------------------------------------------- #
# §6.3.3 retention：published_tmp 类目（include_glob 收窄 + delete）
# --------------------------------------------------------------------------- #
def test_retention_published_tmp_entry_is_narrow_and_deleting():
    """published_tmp 必须 include_glob 收窄到 .tmp_* 且 delete——否则 keep_days+delete
    会误删发布目录的正式件（manifest/records 无保留策略）。"""
    import retention

    names = {s["name"]: s for s in retention.POLICY}
    for key in ("published_tmp_ext", "published_tmp_int"):
        spec = names.get(key)
        assert spec is not None, f"{key} 未登记（W4 §6.3.3）"
        assert spec.get("include_glob") == ".tmp_*"
        assert spec.get("keep_days") == 1
        assert spec.get("delete") is True


def test_plan_age_honors_include_glob(tmp_path, monkeypatch):
    """_plan_age 的 include_glob：只计划匹配名（正式件即使超龄也不进计划）。"""
    import retention

    # _plan_age 按 paths.ROOT 拼相对目录 → 测试内经 monkeypatch 改派根（用后即还原）
    monkeypatch.setattr(retention.paths, "ROOT", str(tmp_path))
    pub = tmp_path / "published"
    pub.mkdir()
    stale_tmp = pub / ".tmp_dead"
    stale_tmp.write_text("x", encoding="utf-8")
    stale_manifest = pub / "publish_manifest.json"
    stale_manifest.write_text("{}", encoding="utf-8")
    ts = tmp_path.stat().st_mtime - 3 * 86400
    os.utime(stale_tmp, (ts, ts))
    os.utime(stale_manifest, (ts, ts))

    spec = {"name": "published_tmp_ext", "dir": "published", "include_glob": ".tmp_*",
            "keep_days": 1, "delete": True}
    rows, st = retention._plan_age(spec)
    assert [r["file"] for r in rows] == ["published/.tmp_dead"], "正式件不得进计划（include_glob 收窄）"
    assert st["total"] == 1, "total 只计候选集（收窄后）"
