# -*- coding: utf-8 -*-
"""tests.test_resource_hygiene — 批 46（资源占用治理）守卫。

覆盖四处**实测驱动**的修复（均以"峰值内存与语料体积解耦"为判据）：
  ① **N-197** 备份快照保留上限（`apply_timeliness_to_cleaned._cleanup_backups`：按 (tag, 源) 分组）；
  ② **N-198** 时效回写**流式化**（`writeback_source` 不再整表载入 jsonl/csv）；
  ③ **N-199** 清洗去重校验**流式化**（`run_clean_pipeline` 只逐行取 `dedup_key`）+ `sanitize_csv`
     **流式 + 原子写**；
  ④ **N-200** 主库**旁路索引**（`gov_parse.MasterView`：索引命中 ⇒ 不解析正文；缺失/陈旧 ⇒
     **流式重建**；任何失败 ⇒ 回退全量解析，语义不变）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

import paths

ROOT = paths.ROOT


def _load(rel_path: str, name: str):
    """按路径加载非包内模块（先注册 `sys.modules`，否则 `@dataclass` 解析失败）。

    ⚠️ **不在测试里改 `sys.path`**（门禁「sys.path 引导纪律」：tests 层基线冻结）——
    被加载模块自带仓库引导（`gov_parse`/`apply_timeliness_to_cleaned` 均有），
    依赖包按 `std_lib.*` 全路径导入即可。
    """
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gp():
    return _load(os.path.join("modules", "regulatory_scrapers", "collectors", "gov_parse.py"), "_gp_res")


# --------------------------------------------------------------------------- #
# N-200 主库旁路索引
# --------------------------------------------------------------------------- #
def _mk_master(tmp_path, records):
    """夹具主库：**JSONL 形态**（N-206/S-A 后的规范形态；首行 `_meta`）。"""
    out = tmp_path / "raw"
    out.mkdir(exist_ok=True)
    with open(out / "gov_laws.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"source": "gov", "format": "jsonl/1"}}, ensure_ascii=False) + "\n")
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(out)


RECS = [
    {"detail_url": "u1", "title": "A", "full_text": "正文" * 10},
    {"detail_url": "u2", "title": "B", "full_text": ""},          # 已知但无正文 → 待补
    {"detail_url": "", "title": "C", "full_text": "x"},           # 无 URL（边界）
]


def test_index_keys_semantics(gp, tmp_path) -> None:
    """索引语义：seen=（标题,URL）全集；detailed=**已有正文**的 URL 集（无正文者不进）。"""
    out = _mk_master(tmp_path, RECS)
    gp.build_master_index_streaming(out)
    assert os.path.exists(gp.master_index_path(out))
    mv = gp.MasterView(out)
    seen, detailed = mv.keys()
    assert mv.from_index is True, "应命中旁路索引（不得解析主库正文）"
    assert seen == {("A", "u1"), ("B", "u2"), ("C", "")}
    assert detailed == {"u1"}


def test_streaming_index_matches_full_parse(gp, tmp_path) -> None:
    """**流式重建**必须与"全量解析后写索引"逐字段一致（等价性判据）。"""
    out = _mk_master(tmp_path, RECS)
    gp.build_master_index_streaming(out)
    a = json.load(open(gp.master_index_path(out), encoding="utf-8"))
    gp.write_master_index(out, gp.MasterView(out).records())
    b = json.load(open(gp.master_index_path(out), encoding="utf-8"))
    assert a["entries"] == b["entries"] and a["count"] == b["count"]


def test_stale_index_triggers_rebuild(gp, tmp_path) -> None:
    """主库变更（size/mtime 不符）⇒ 索引失效 ⇒ **自动流式重建**（语义仍正确）。"""
    out = _mk_master(tmp_path, RECS)
    gp.build_master_index_streaming(out)
    mp = gp._find_master(out)                     # N-206：主库可能是 JSONL（迁移期亦支持旧 .json）
    with open(mp, "a", encoding="utf-8") as f:    # 追加一条 → size/mtime 变化 ⇒ 索引失效
        f.write(json.dumps({"detail_url": "u9", "title": "D", "full_text": "新"}, ensure_ascii=False) + "\n")
    mv = gp.MasterView(out)
    seen, detailed = mv.keys()
    assert mv.from_index is True and ("D", "u9") in seen and "u9" in detailed


def test_missing_index_is_built_without_full_parse(gp, tmp_path, monkeypatch) -> None:
    """索引缺失 ⇒ 走**流式重建**（判据：不调用 `json.load` 解析主库整表）。"""
    out = _mk_master(tmp_path, RECS)
    calls = {"n": 0}
    real_load = json.load

    def _spy(fp, *a, **k):
        # N-206：主库现为 `gov_laws.jsonl`（旧 `.json` 仅迁移期存在）⇒ 两种名都要盯
        name = getattr(fp, "name", "")
        if name.endswith("gov_laws.jsonl") or name.endswith("gov_laws.json"):
            calls["n"] += 1
        return real_load(fp, *a, **k)

    monkeypatch.setattr(gp.json, "load", _spy)
    mv = gp.MasterView(out)
    mv.keys()
    assert calls["n"] == 0, "索引缺失时不得全量解析主库（应流式重建）"
    assert os.path.exists(gp.master_index_path(out))


def test_merge_reuses_parsed_master(gp, tmp_path) -> None:
    """`merge_with_master(master_records=...)` 复用外部已解析结果（不再二次解析）。"""
    out = _mk_master(tmp_path, RECS)
    old = gp.MasterView(out).records()
    merged = gp.merge_with_master([{"detail_url": "u1", "title": "A", "full_text": "新正文"}],
                                  out, master_records=old)
    by = {r["detail_url"]: r for r in merged}
    assert len(merged) == 3 and by["u1"]["full_text"] == "新正文", "同 URL 应新优先覆盖"


def test_build_index_on_absent_master_is_noop(gp, tmp_path) -> None:
    """主库不存在 ⇒ 流式重建返回空串（不得抛错、不得造出空索引）。"""
    out = str(tmp_path / "empty")
    os.makedirs(out, exist_ok=True)
    assert gp.build_master_index_streaming(out) == ""
    assert gp.MasterView(out).keys() == (set(), set())
    assert gp.MasterView(out).records() == []


# --------------------------------------------------------------------------- #
# N-197 备份保留上限（分组语义）
# --------------------------------------------------------------------------- #
def test_backup_prune_grouped_by_tag_and_source(tmp_path, monkeypatch) -> None:
    """按 (tag, 源) 分组各留 1 份：**不同源互不牵连**、**最新一份必留**。"""
    mod = _load(os.path.join("modules", "regulatory_scrapers", "timeliness_review",
                             "apply_timeliness_to_cleaned.py"), "_apply_res")
    root = tmp_path / "backups"
    root.mkdir()
    # 同一 tag 下：gov 两份（新/旧）、nfra 一份 ⇒ 只应删 gov 的旧那份。
    # ⚠️ 快照内文件名刻意**不带 8 位日期**：门禁「硬编码 cleaned 快照日期（N-3）」按
    #    `<src>_cleaned_<8位数字>.csv|jsonl` 判据扫描全仓文本，夹具无需真实日期即可校验分组语义。
    for name, fname in (
        ("cleaned_before_timeliness_20261008_010101", "gov_cleaned_newer.jsonl"),
        ("cleaned_before_timeliness_20261007_010101", "gov_cleaned_older.jsonl"),
        ("cleaned_before_timeliness_20261008_020202", "nfra_cleaned_newer.jsonl"),
    ):
        d = root / name
        d.mkdir()
        (d / fname).write_text("x", encoding="utf-8")
    monkeypatch.setattr(mod, "BACKUP_ROOT", str(root))
    removed = mod._cleanup_backups()
    assert removed == ["cleaned_before_timeliness_20261007_010101"]
    assert (root / "cleaned_before_timeliness_20261008_010101").exists()
    assert (root / "cleaned_before_timeliness_20261008_020202").exists(), "他源快照不得被牵连"


# --------------------------------------------------------------------------- #
# N-199 sanitize 流式 + 原子
# --------------------------------------------------------------------------- #
def test_sanitize_csv_streaming_and_atomic(tmp_path) -> None:
    """CSV 归一：字段内换行被压平、保留 BOM/列序、**无 .tmp 残留**、行数返回值正确。"""
    san = _load(os.path.join("modules", "regulatory_scrapers", "clean", "sanitize.py"), "_san_res")
    p = tmp_path / "s.csv"
    p.write_text('a,b\n"x\ny",2\n"p\r\nq",3\n', encoding="utf-8-sig", newline="")
    n = san.sanitize_csv(str(p))
    assert n == 2
    txt = p.read_text(encoding="utf-8-sig")
    lines = txt.splitlines()          # 字段内换行若未压平，这里会出现**多于 3 行**
    assert lines[0] == "a,b"
    assert lines[1] == "x y,2", "字段内换行应被压平为空格"
    assert lines[2] == "p q,3", "CRLF 型字段内换行同样应被压平"
    assert len(lines) == 3, "行数应为 1 表头 + 2 数据行"
    assert not os.path.exists(str(p) + ".tmp"), "不得残留 .tmp"


def test_sanitize_csv_empty_file_untouched(tmp_path) -> None:
    """空文件：返回 0 且**不**产生空覆盖（保持原行为）。"""
    san = _load(os.path.join("modules", "regulatory_scrapers", "clean", "sanitize.py"), "_san_res2")
    p = tmp_path / "e.csv"
    p.write_text("", encoding="utf-8-sig")
    assert san.sanitize_csv(str(p)) == 0
    assert p.read_text(encoding="utf-8-sig") == ""
    assert not os.path.exists(str(p) + ".tmp")


def test_dedup_check_accepts_generator() -> None:
    """`check_unique_dedup_keys` 必须支持**生成器**（N-199 流式调用的前提）。"""
    from std_lib.scraper_std.schema_validation import check_unique_dedup_keys as cdk

    ok, dups = cdk({"dedup_key": k} for k in ("a", "b", "a"))
    assert ok is False and dups[0]["dedup_key"] == "a" and dups[0]["count"] == 2
    ok2, dups2 = cdk({"dedup_key": k} for k in ("a", "b", "c"))
    assert ok2 is True and dups2 == []


# --------------------------------------------------------------------------- #
# R-C 补全模式短路
# --------------------------------------------------------------------------- #
def test_backfill_short_circuits_when_nothing_to_backfill() -> None:
    """backfill：主库**全部已有正文** ⇒ 不翻页（`nothing_to_backfill`），避免空扫 629 页。"""
    zck = _load(os.path.join("modules", "regulatory_scrapers", "collectors", "gov_zhengceku.py"), "_zck_res")
    cfg = type("C", (), {"out_dir": "", "resume": True, "backfill": True, "checkpoint_every": 0,
                        "max_pages": 0, "max_items": 0, "fetch_details": True,
                        "stop_after_empty_pages": 1, "max_seconds": 0})()
    sc = zck.ZhengcekuScraper(cfg, client=None, seen_urls={"u1", "u2"}, known_urls={"u1", "u2"})
    assert sc.run() == []
    assert sc.stats["stopped_reason"] == "nothing_to_backfill"
    assert sc.stats["list_pages_fetched"] == 0
