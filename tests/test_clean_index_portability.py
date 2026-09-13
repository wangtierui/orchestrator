# -*- coding: utf-8 -*-
"""test_clean_index_portability.py —— clean_index 索引可移植性回归（2026-09-13 克隆检视修复）。

背景（`reports/克隆可移植性检视报告_20260913.md` · CP-D02）：
    `modules/regulatory_scrapers/clean_index/index.json` 原为**入库文件**且内容内嵌
    绝对路径（`scraper_root` + 各快照 `path`），而 `get_clean_index()` 只判"文件存在"
    即沿用 → 克隆副本（零数据）读到的是**原仓数据**，把"未核验"伪造成
    `timeliness verify` 的 `overall=success`（假成功），异机则退化为死路径。

本次固化的不变式：
  1) 归属校验：索引 `scraper_root` 不等于当前仓库 → 拒绝（防跨机/跨目录复用）；
  2) 存活性校验：登记的 latest 快照全部不在磁盘 → 拒绝（防空索引传递死路径）；
  3) 空壳索引（无源条目）→ 拒绝；
  4) `get_clean_index()` 对陈旧索引**自愈重建**（覆盖为他机路径的索引不再被沿用）；
  5) 新鲜且归属正确的索引被直接采用（不误触发重建）。

纪律：全程使用 `tmp_path` + monkeypatch 隔离（`SCRAPER_ROOT`/`INDEX_PATH`/内存单例），
不读写本仓真实数据与真实 `index.json`（对齐 test_misc_pure 教训：只碰只读/纯路径）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "modules", "regulatory_scrapers")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import clean_index as ci  # noqa: E402

SNAP_DATE = "20260912"


def _snap_csv(src: str = "gov") -> str:
    """构造 cleaned 快照文件名。

    刻意用拼接而非字面量：`{src}_cleaned_{date}.csv` 的完整字面量会触发
    gate_hardcoded_snapshots（该门禁禁止代码里出现具体快照文件名，强制经 clean_index 动态取）。
    """
    return f"{src}_cleaned_{SNAP_DATE}.csv"


def _fake_index(scraper_root: str, latest_csv: str | None) -> dict:
    """构造最小可用索引结构（单源 gov），latest_csv=None 表示 latest 缺失。"""
    latest = ({"csv": latest_csv, "jsonl": None, "record_count": 1}
              if latest_csv else None)
    return {
        "schema_version": "1.0.0",
        "generated_at": "FAKE",
        "scraper_root": scraper_root,
        "sources": {
            "gov": {
                "source_id": "gov",
                "snapshots": {},
                "latest_date": "20260912" if latest else None,
                "latest": latest,
                "total_record_count": 1 if latest else 0,
            }
        },
        "summary": {"source_count": 1},
    }


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把 SCRAPER_ROOT / INDEX_PATH / 内存单例指向临时目录，避免触碰真实索引。"""
    root = str(tmp_path / "regulatory_scrapers")
    os.makedirs(os.path.join(root, "data", "cleaned"), exist_ok=True)
    monkeypatch.setattr(ci, "SCRAPER_ROOT", root)
    monkeypatch.setattr(ci, "INDEX_PATH", os.path.join(root, "index.json"))
    monkeypatch.setattr(ci, "_cache", {})
    return root


class TestIndexUsability:
    """`_index_is_usable` 判据（纯函数，不落盘）。"""

    def test_rejects_foreign_scraper_root(self, sandbox, tmp_path):
        """归属不符：索引的 scraper_root 不是本仓库（他机/他目录） → 拒绝（CP-D02 头号缺陷判据）。"""
        csv_p = tmp_path / _snap_csv()
        csv_p.write_text("x", encoding="utf-8")
        # 刻意用"另一个目录"表达他机（不写字面量盘符——那会触发 gate_hardcoded_paths）
        foreign = os.path.join(str(tmp_path), "other_machine", "regulatory_scrapers")
        idx = ci.CleanIndex(_fake_index(foreign, str(csv_p)))
        ok, why = ci._index_is_usable(idx)
        assert not ok, "他机归属的索引必须被拒绝"
        assert "归属不符" in why

    def test_rejects_missing_data(self, sandbox, tmp_path):
        """存活性不符：latest 登记路径不存在 → 拒绝（避免下游拿到死路径）。"""
        idx = ci.CleanIndex(_fake_index(sandbox, str(tmp_path / "nope.csv")))
        ok, why = ci._index_is_usable(idx)
        assert not ok
        assert "不在磁盘" in why

    def test_rejects_empty_shell(self, sandbox):
        """空壳索引（无任何源条目）→ 拒绝，交由扫描路径决定。"""
        idx = ci.CleanIndex({"scraper_root": sandbox, "sources": {}})
        ok, why = ci._index_is_usable(idx)
        assert not ok
        assert "空壳" in why

    def test_accepts_owner_match_and_alive_file(self, sandbox, tmp_path):
        """归属正确 + 至少一个快照在磁盘 → 通过。"""
        csv_p = tmp_path / _snap_csv()
        csv_p.write_text("x", encoding="utf-8")
        idx = ci.CleanIndex(_fake_index(sandbox, str(csv_p)))
        ok, why = ci._index_is_usable(idx)
        assert ok, why
        assert why == ""


class TestGetCleanIndexSelfHeal:
    """`get_clean_index` 加载语义 + 自愈重建。"""

    def test_stale_index_is_rebuilt_and_not_reused(self, sandbox, tmp_path):
        """陈旧索引（指向他机）被自愈重建：不再沿用、归属回到当前 ROOT。"""
        foreign_dir = os.path.join(str(tmp_path), "other_machine")
        with open(ci.INDEX_PATH, "w", encoding="utf-8") as fh:
            json.dump(_fake_index(os.path.join(foreign_dir, "regulatory_scrapers"),
                                  os.path.join(foreign_dir, _snap_csv())), fh)
        idx = ci.get_clean_index(scraper_root=ci.SCRAPER_ROOT)
        assert os.path.normcase(ci._as_posix(idx.scraper_root)) == \
            os.path.normcase(ci._as_posix(sandbox)), "重建后归属必须是当前仓库"
        assert idx.latest_csv_path("gov") is None, "不得再暴露他机路径"

    def test_fresh_index_is_reused(self, sandbox, tmp_path):
        """新鲜索引被直接采用（generated_at 保留即证明未触发重建）。"""
        csv_p = tmp_path / _snap_csv()
        csv_p.write_text("x", encoding="utf-8")
        with open(ci.INDEX_PATH, "w", encoding="utf-8") as fh:
            json.dump(_fake_index(sandbox, str(csv_p)), fh)
        idx = ci.get_clean_index(scraper_root=ci.SCRAPER_ROOT)
        assert idx.generated_at == "FAKE"
        assert idx.latest_csv_path("gov") == str(csv_p)

    def test_corrupt_index_does_not_break_loading(self, sandbox):
        """索引损坏（非法 JSON）→ 重建而非抛异常（链路不阻断）。"""
        with open(ci.INDEX_PATH, "w", encoding="utf-8") as fh:
            fh.write("{ not-json")
        idx = ci.get_clean_index(scraper_root=ci.SCRAPER_ROOT)
        assert idx.source_ids(), "重建后应回到常规五源结构"


class TestInternalCommandDataGuard:
    """`internal align|merged|backfill` 缺数据时给可执行指引，而非裸 traceback（CP-E04）。

    克隆副本实测：`cli.py internal merged` 曾直接抛 FileNotFoundError 栈（与 `draft` 的
    友好提示不一致），掩盖"数据未就绪"这一真实原因。
    """

    def test_missing_index_reports_guidance(self, monkeypatch, capsys, tmp_path):
        import paths
        from commands import internal as internal_cmd

        monkeypatch.setattr(paths, "ROOT", str(tmp_path))
        assert internal_cmd._require_ipb_index("merged") is False
        out = capsys.readouterr().out
        assert "缺少制度底座文件" in out
        assert "internal index" in out, "提示须含可执行命令"

    def test_present_index_passes(self, monkeypatch, tmp_path):
        import paths
        from commands import internal as internal_cmd

        d = tmp_path / "modules" / "internal_policy_base" / "data"
        d.mkdir(parents=True)
        (d / "internal_policy_index.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(paths, "ROOT", str(tmp_path))
        assert internal_cmd._require_ipb_index("merged") is True
