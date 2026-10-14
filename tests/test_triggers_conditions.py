# -*- coding: utf-8 -*-
"""tests/test_triggers_conditions.py — 条件触发六条件的单元测试（W0-1，2026-10-13）。

覆盖重点（D-1 回归）：`publish_manifest_changed` 观察**双底座真实清单**（联合版本 vs
`wiki:sync` 水位基线）；四条口径——清单缺失/治理库未启用 → **不判定（False）**；无基线 →
视为变化；版本一致 → 不满足；版本变化 → 满足。其余五条件各至少一条路径。

隔离纪律：治理库经 `REG_ORCH_GOVERNANCE_DB` 指向 pytest 临时目录（同
test_governance_store.py 口径），**绝不触碰生产 data/governance.db**；观察路径经
monkeypatch 指向 tmp。
"""
from __future__ import annotations

import json
import os

import pytest

from std_lib.common_lib import triggers as trg


@pytest.fixture()
def gov(tmp_path, monkeypatch):
    """隔离治理库（env 覆盖 → 临时库），返回 (gs, db_path)。"""
    monkeypatch.setenv("REG_ORCH_GOVERNANCE_DB", str(tmp_path / "gov.db"))
    from std_lib.common_lib import governance_store as gs

    gs.init_db()
    return gs, str(tmp_path / "gov.db")


def _mk_manifest(p, payload: dict) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ---- D-1：publish_manifest_changed 双清单观察（W0-1 回归）----


def test_publish_cond_manifests_absent_not_judged(monkeypatch, tmp_path):
    """两份清单都不存在 → False + 明示原因（**不静默按 True**）。"""
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS", (str(tmp_path / "no_ext.json"),
                                                   str(tmp_path / "no_int.json")))
    ok, ev = trg._c_publish_manifest_changed(True, {})
    assert ok is False
    assert "均不存在" in ev


def test_publish_cond_governance_disabled_not_judged(monkeypatch, tmp_path, gov):
    """清单在场但治理库未启用 → 不判定（D-1 旧口径的镜像：不猜）。

    ⚠️ 双模块实例（问题项 P-9）：`_c_publish_manifest_changed` 内部是
    `from common_lib import governance_store`，与 `std_lib.common_lib.governance_store`
    可能是**两个模块对象**（视 sys.path 引导形态）⇒ 须patch 全部已加载实例才生效。
    """
    gs, _db = gov
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS",
                        (_mk_manifest(tmp_path / "a" / "publish_manifest.json", {"x": 1}),))
    import importlib as _il
    import sys as _sys

    # 先触发**惰性导入**（条件函数内是 `from common_lib import governance_store`，
    # 与 `std_lib.common_lib.governance_store` 可能是两个模块对象——问题项 P-9），
    # 再把全部已加载实例patch 掉，否则 patch 落在未加载的实例上不生效。
    _il.import_module("common_lib.governance_store")
    patched = 0
    for name in ("common_lib.governance_store", "std_lib.common_lib.governance_store"):
        mod = _sys.modules.get(name)
        if mod is not None:
            monkeypatch.setattr(mod, "enabled", lambda: False)
            patched += 1
    assert patched >= 2, f"应同时存在两个模块实例（P-9），实际 patch 了 {patched} 个"
    ok, ev = trg._c_publish_manifest_changed(True, {})
    assert ok is False
    assert "治理库未启用" in ev


def test_publish_cond_no_baseline_treated_as_changed(monkeypatch, tmp_path, gov):
    """清单在场、治理库启用、**无 wiki:sync 基线** → 视为变化（满足）。"""
    gs, _db = gov
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS",
                        (_mk_manifest(tmp_path / "a" / "publish_manifest.json", {"x": 1}),))
    ok, ev = trg._c_publish_manifest_changed(True, {})
    assert ok is True
    assert "无 wiki:sync 水位基线" in ev


def test_publish_cond_equal_baseline_not_satisfied(monkeypatch, tmp_path, gov):
    """联合版本 == wiki:sync 基线 → 不满足（这是修复后**能真正跳过**的路径）。"""
    gs, _db = gov
    m = _mk_manifest(tmp_path / "a" / "publish_manifest.json", {"x": 1})
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS", (m,))
    cur = gs.version_of_files([m])
    gs.record_watermark("wiki:sync", "tools.sync_wiki_sources", cur)
    ok, ev = trg._c_publish_manifest_changed(True, {})
    assert ok is False
    assert "vs 基线" in ev


def test_publish_cond_changed_baseline_satisfied(monkeypatch, tmp_path, gov):
    """任一份清单变化 → 联合版本变 → 满足（D-1 修复的核心行为）。"""
    gs, _db = gov
    m1 = _mk_manifest(tmp_path / "a" / "publish_manifest.json", {"x": 1})
    m2 = _mk_manifest(tmp_path / "b" / "publish_manifest.json", {"y": 1})
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS", (m1, m2))
    gs.record_watermark("wiki:sync", "tools.sync_wiki_sources",
                        gs.version_of_files([m1]))  # 基线只含第一份 → 第二份新增即变化
    ok, ev = trg._c_publish_manifest_changed(True, {})
    assert ok is True
    assert "2/2 份在场" in ev


def test_publish_cond_want_false_inverts(monkeypatch, tmp_path, gov):
    """`want=False` 语义取反（yaml 声明 `publish_manifest_changed: false` 时可用）。"""
    gs, _db = gov
    m = _mk_manifest(tmp_path / "a" / "publish_manifest.json", {"x": 1})
    monkeypatch.setattr(trg, "_PUBLISH_MANIFESTS", (m,))
    gs.record_watermark("wiki:sync", "tools.sync_wiki_sources", gs.version_of_files([m]))
    ok, _ev = trg._c_publish_manifest_changed(False, {})
    assert ok is True  # 版本一致 + want=False → 满足（「未变化」成立）


# ---- 其余五条件的最小路径 ----


def test_token_exists_condition(tmp_path, monkeypatch):
    p = tmp_path / ".pkulaw_token"
    p.write_text("tok", encoding="utf-8")
    monkeypatch.setattr(trg, "_PKULAW_TOKEN", str(p))
    assert trg._c_token_exists(True, {})[0] is True
    assert trg._c_token_exists(False, {})[0] is False


def test_days_since_missing_state_is_satisfied(monkeypatch, tmp_path):
    monkeypatch.setattr(trg, "_TIMELINESS_STATE", str(tmp_path / "nope.json"))
    ok, ev = trg._c_days_since(7, {})
    assert ok is True and "从未核验" in ev


def test_explicit_arg_reads_ctx_argv():
    assert trg._c_explicit_arg("--force", {"argv": ["run", "--force"]})[0] is True
    assert trg._c_explicit_arg("--force", {"argv": ["run"]})[0] is False


def test_decide_table_has_reasons_for_all():
    rows = trg.decide({"argv": []})
    assert rows and all(r["reasons"] for r in rows)
    ids = {r["id"] for r in rows}
    assert "wiki_sync" in ids


def test_all_conditions_registered_in_yaml():
    """纪律②：yaml 声明的每个条件都有实现（防「条件触发」退化为「永远触发」）。"""
    cfg = trg.load_triggers()
    rows = cfg.get("triggers") or []
    assert isinstance(rows, list) and rows, f"triggers.yaml 结构异常: {type(rows)}"
    declared: set[str] = set()
    for t in rows:
        declared |= set((t.get("enabled_when") or {}).keys())
    missing = declared - set(trg.CONDITION_IMPLS)
    assert not missing, f"缺实现的条件: {missing}"


def test_condition_observation_paths_exist():
    """W0-1 加固（T4 判据的测试侧先行）：`publish_manifest_changed` 的观察路径必须
    落在 modules/ 下且至少一份清单在场（D-1 回归防线：观察路径再漂移，此测试先红）。"""
    import paths

    for p in trg._PUBLISH_MANIFESTS:
        assert os.path.isabs(p), p
        assert p.startswith(paths.MODULES_DIR), p
    assert any(os.path.exists(p) for p in trg._PUBLISH_MANIFESTS), (
        "双底座发布件清单均不存在——base:publish 未跑过或路径再次漂移"
    )
