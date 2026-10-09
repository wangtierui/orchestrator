# -*- coding: utf-8 -*-
"""tests.test_gov_incremental — gov 子源拆分（R-C）与增量治理（N-190/N-191）的守卫。

覆盖三块**纯逻辑/契约**（不联网、不加载模型，秒级）：
  ① `gov_zhengceku.should_stop_paging`：**增量早停**判据（原实现恒扫 629 页 ⇒ 单轮 20+ 分钟空转）；
  ② `run_production_refresh.collect_targets`：`--collect gov` **展开为两个子源步骤**（别名契约），
     以及子源 argv / 超时 / 主库文件的一致性（拆分后下游**零改动**的落点）；
  ③ **删除回归守卫**（N-191）：pbc 不再生成 `report.md`、nfra 不再写 `raw/README.md`；
     采集统计落在 `data/run_state/collect_stats/`（**不写事实源目录**）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

import paths

ROOT = paths.ROOT
CD = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors")


def _load(rel_path: str, name: str):
    """按路径加载模块（采集器/工具脚本**不在包内**，需显式 spec 加载）。

    ⚠️ 必须**先注册进 `sys.modules`**：否则 `@dataclass` 解析 `cls.__module__` 时取到 `None`
    （dataclasses 内部 `sys.modules.get(cls.__module__).__dict__` 会抛 AttributeError）。
    """
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def zck():
    return _load(os.path.join("modules", "regulatory_scrapers", "collectors", "gov_zhengceku.py"), "_zck")


@pytest.fixture(scope="module")
def rpr():
    return _load(os.path.join("tools", "run_production_refresh.py"), "_rpr_inc")


# --------------------------------------------------------------------------- #
# ① 增量早停判据
# --------------------------------------------------------------------------- #
def test_stop_paging_incremental_stops_on_empty_streak(zck) -> None:
    """增量模式：连续无新页数达到阈值 → 停（默认阈值 1）。"""
    assert zck.should_stop_paging(1, 1, False) is True
    assert zck.should_stop_paging(2, 2, False) is True
    assert zck.should_stop_paging(0, 1, False) is False


def test_stop_paging_backfill_never_stops(zck) -> None:
    """backfill（历史存量补全）**永不早停** —— 否则历史正文停止收敛（须扫全 629 页）。"""
    assert zck.should_stop_paging(1, 1, True) is False
    assert zck.should_stop_paging(50, 3, True) is False


def test_stop_paging_threshold_and_guard(zck) -> None:
    """阈值可调；非法阈值（0/None）按 1 处理（**不得恒停/恒不停**）。"""
    assert zck.should_stop_paging(1, 3, False) is False
    assert zck.should_stop_paging(3, 3, False) is True
    assert zck.should_stop_paging(1, 0, False) is True
    assert zck.should_stop_paging(1, None, False) is True  # type: ignore[arg-type]
    assert zck.DEFAULT_STOP_AFTER_EMPTY_PAGES == 1


# --------------------------------------------------------------------------- #
# ② 子源拆分契约（--collect 别名展开 / argv / 超时 / 主库同一）
# --------------------------------------------------------------------------- #
def test_collect_targets_expands_gov_alias(rpr) -> None:
    """`gov` → 两个子源步骤；既有排程 argv（`gov,mof,pbc`）**无需改动**即得子源粒度。"""
    assert rpr.collect_targets("gov") == ["gov_xzfgk", "gov_zhengceku"]
    assert rpr.collect_targets("gov,mof,pbc") == [
        "gov_xzfgk", "gov_zhengceku", "mof", "pbc",
    ]


def test_collect_targets_all_and_dedup(rpr) -> None:
    """`all` 展开全部事实源（gov 仍拆两子源）；重复键去重保序；未登记键透传。"""
    t = rpr.collect_targets("all")
    assert t[:2] == ["gov_xzfgk", "gov_zhengceku"] and "pbc" in t and "supp" in t
    assert rpr.collect_targets("gov,gov_xzfgk") == ["gov_xzfgk", "gov_zhengceku"]
    assert rpr.collect_targets("nonexistent") == ["nonexistent"]
    assert rpr.collect_targets("") == []


def test_substep_argv_pins_envelope_and_source(rpr) -> None:
    """每个子源 argv 必须：指定 `--source`、**固定 gov 信封**（否则主库信封被最后一子源覆盖）。"""
    for key, sub in (("gov_xzfgk", "xzfgk"), ("gov_zhengceku", "zhengceku")):
        argv = rpr.COLLECT_CMD[key]
        assert "--source" in argv and argv[argv.index("--source") + 1] == sub
        assert "--env-source" in argv and argv[argv.index("--env-source") + 1] == "gov"
        assert "--env-category" in argv


def test_substeps_share_single_master_and_have_timeouts(rpr) -> None:
    """拆分后**仍写同一主库**（下游零改动）；每步有分档超时（不是一刀切 7200）。"""
    # S-A（N-206）后主库为 **JSONL**（`gov_laws.jsonl`，首行 _meta）——详见 tests/test_raw_jsonl.py
    assert rpr.RAW_JSON["gov"] == "gov_laws.jsonl"
    assert "gov_xzfgk" not in rpr.RAW_JSON and "gov_zhengceku" not in rpr.RAW_JSON, (
        "子源不得进 RAW_JSON（那是事实源口径，clean 的体量超时按它取大小）"
    )
    assert rpr.COLLECT_TIMEOUT["gov_xzfgk"] < rpr.COLLECT_TIMEOUT["gov_zhengceku_backfill"]
    assert rpr.COLLECT_TIMEOUT["gov_zhengceku_backfill"] >= 3600


def test_backfill_step_carries_backfill_flag(rpr) -> None:
    """月度补全步骤必须带 `--backfill`（否则会被早停，历史存量不再收敛）。"""
    assert "--backfill" in rpr.COLLECT_CMD["gov_zhengceku_backfill"]


def test_soft_budget_below_hard_timeout(rpr) -> None:
    """**软预算必须小于步骤硬超时**（留收尾余量）——否则软预算永不生效（被硬超时先杀）。

    已在 argv 里写明收尾余量（载入主库 + 合并 + 1GB 原子写，实测 ≈3~4 分钟）。
    """
    for key in ("gov_xzfgk", "gov_zhengceku", "gov_zhengceku_backfill"):
        argv = rpr.COLLECT_CMD[key]
        assert "--max-seconds" in argv, "%s 未设软预算" % key
        soft = float(argv[argv.index("--max-seconds") + 1])
        hard = float(rpr.COLLECT_TIMEOUT[key])
        assert 0 < soft < hard, "%s 软预算 %s 未小于硬超时 %s" % (key, soft, hard)
        assert hard - soft >= 120, "%s 收尾余量不足 120s（载入/合并/原子写会被硬超时截断）" % key


# --------------------------------------------------------------------------- #
# ③ 删除回归守卫（N-191）与统计落点
# --------------------------------------------------------------------------- #
def test_pbc_has_no_report_generation() -> None:
    """pbc 采集器**不得**再生成报告（R-F：原写到 `dirname(--out)/reports/report.md`）。

    判据用**代码标识符 + 报告标题字面量**（不用 `report.md` 子串）——否则**说明文字**里的
    文件名会把守卫自己判红（本测试首版即踩此坑）。
    """
    src = open(os.path.join(CD, "pbc_collector.py"), encoding="utf-8").read()
    assert "build_report" not in src
    assert "report_path" not in src
    assert "抓取统计报告" not in src


def test_nfra_has_no_readme_writer() -> None:
    """nfra 采集器**不得**再写 `README.md` 到 out_dir（即 `data/raw/README.md`）。"""
    src = open(os.path.join(CD, "nfra_collector.py"), encoding="utf-8").read()
    assert "report_path" not in src
    assert "国家金融监督管理总局 · 政策法规抓取结果" not in src
    assert "return json_path, csv_path\n" in src


def test_raw_readme_is_gone() -> None:
    """被删除的产出**不得**回到事实源目录（防"顺手重建"）。"""
    assert not os.path.exists(
        os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "raw", "README.md")
    )


def test_collect_stats_path_outside_fact_source(monkeypatch) -> None:
    """统计落点必须在 `data/run_state/`（旁路观测），**绝不能**落 `data/raw/`（事实源）。"""
    gp = _load(os.path.join("modules", "regulatory_scrapers", "collectors", "gov_parse.py"), "_gp_stats")
    p = gp.collect_stats_path("gov_xzfgk")
    assert p.endswith(os.path.join("collect_stats", "gov_xzfgk.json"))
    norm = p.replace(os.sep, "/")
    assert "/data/run_state/collect_stats/" in norm
    assert "/data/raw/" not in norm


def test_write_collect_stats_roundtrip(monkeypatch) -> None:
    """统计写入：原子落盘 + 内容可回读；写失败**不得抛错**（旁路观测）。"""
    gp = _load(os.path.join("modules", "regulatory_scrapers", "collectors", "gov_parse.py"), "_gp_stats2")
    monkeypatch.setattr(gp, "_COLLECT_STATS_REL", ("reports", "_tmp", "collect_stats_test"))
    payload = {"sub_source": "xzfgk", "stats": {"list_pages_fetched": 3, "list_pages_without_new": 2}}
    p = gp.write_collect_stats("unit_test", payload)
    try:
        assert p and os.path.exists(p)
        assert json.load(open(p, encoding="utf-8")) == payload
        assert not os.path.exists(p + ".tmp"), "不得残留临时文件"
    finally:
        if p and os.path.exists(p):
            os.remove(p)
    # 不可写路径 → 返回空串且不抛（采集不因统计失败而中断）
    monkeypatch.setattr(gp, "_COLLECT_STATS_REL", ("reports", "_tmp", "x" * 2, "\0bad"))
    assert gp.write_collect_stats("unit_test2", payload) == ""
