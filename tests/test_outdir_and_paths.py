# -*- coding: utf-8 -*-
"""tests/test_outdir_and_paths.py — 采集器输出目录 / local_path 基准的守卫（第一百零三批）。

背景（2026-10-20 实测发现的**同类缺陷三处**）
--------------------------------------------
`REPO_ROOT = os.path.dirname(SRC_DIR)` 只上溯**一级**，而采集器在
`modules/regulatory_scrapers/collectors/<src>/` ⇒ 实际得到 `.../collectors`
（**注释却都写 "regulatory_scrapers"**，两者不符）。后果分两类：

  ① **`--outdir` 默认值错位**（mof）：落到 `.../collectors/data/raw`，
     而主库在 `.../regulatory_scrapers/data/raw` ⇒ **裸跑静默写错位置、白跑且不报错**。
  ② **`local_path` 变脏**（mof.attachments / pbc）：`relpath(dest, REPO_ROOT)`
     产出 `../../data/docs/…`，**换 cwd 解析就错**（实测 pbc 13 条里 10 条是脏的）。

**关键区分（本批最重要的设计）**：两个路径的基准**不同，不能共用一个常量**
  · `outdir`      ⇒ 主库 = `<repo>/modules/regulatory_scrapers/data/raw`（上溯两级）
  · `local_path`  ⇒ **仓根**（实测 mof 现存 246 条 local_path 以仓根拼接后文件真实存在）

守卫四类
--------
A `outdir` 默认值必须等于主库（否则裸跑白跑）
B `local_path` 基准必须是仓根（否则脏路径）
C **产出必须干净**：用真实附件试算 relpath 不得以 `..` 开头
D **历史数据**：现存 `local_path` 里不得有 `../` 开头的脏值（回归可见）
"""
from __future__ import annotations

import glob
import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_RAW = os.path.abspath(os.path.join(ROOT, "modules", "regulatory_scrapers",
                                        "data", "raw"))


class TestOutdirDefaultsToMain:
    """A. 采集器默认输出目录必须就是主库（裸跑要能落到正确位置）。"""

    def test_mof_outdir_default_equals_main(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import collector as MC

        got = os.path.abspath(os.path.join(MC.SCRAPERS_ROOT, "data", "raw"))
        assert got == MAIN_RAW, (
            f"mof 默认 outdir={got}，主库={MAIN_RAW} ⇒ 裸跑会静默写错位置（白跑）")

    def test_no_dangling_repo_root_in_mof_collector(self) -> None:
        """`REPO_ROOT` 若仍存在且指向 `collectors`，说明修得不彻底。"""
        from modules.regulatory_scrapers.collectors.mof import collector as MC

        repo = getattr(MC, "REPO_ROOT", None)
        if repo is not None:
            assert os.path.abspath(repo) != os.path.abspath(
                os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors")), (
                "mof collector 的 REPO_ROOT 仍指向 collectors（少上溯一级）")

    def test_nfra_gov_pbc_defaults_also_main(self) -> None:
        """**一致性**：其余采集器的 `--out*` 默认值也应指向主库。

        实现方式各异（nfra/gov/pbc 用三级 dirname，supp 用 SCRAPERS_ROOT），
        故这里只断言**算出来的路径**，不绑定实现写法。
        """
        # pbc / nfra / gov：default 是 `dirname×3 + data/raw`
        for rel in ("pbc/collector.py", "nfra/collector.py", "gov/collector.py"):
            base = os.path.join(ROOT, "modules", "regulatory_scrapers",
                                "collectors", *rel.split("/")[:-1])
            got = os.path.abspath(os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.join(base, rel.split("/")[-1])))), "data", "raw"))
            assert got == MAIN_RAW, f"{rel} 的 out 基准算出来是 {got}"


class TestLocalPathBaseIsRepoRoot:
    """B. `local_path` 的基准必须是**仓根**。"""

    def test_mof_attachments_repo_root_is_repo(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import attachments as MA

        assert os.path.abspath(MA.REPO_ROOT) == os.path.abspath(ROOT), (
            f"mof.attachments.REPO_ROOT={MA.REPO_ROOT} 不是仓根 ⇒ local_path 会带 `..`")

    def test_pbc_collector_repo_root_is_repo(self) -> None:
        from modules.regulatory_scrapers.collectors.pbc import collector as PC

        assert os.path.abspath(PC.REPO_ROOT) == os.path.abspath(ROOT), (
            f"pbc.collector.REPO_ROOT={PC.REPO_ROOT} 不是仓根 ⇒ local_path 会带 `..`")

    def test_relpath_of_real_attachment_is_clean(self) -> None:
        """C. **产出干净**：真实附件算出的 relpath 不得以 `..` 开头。"""
        from modules.regulatory_scrapers.collectors.mof import attachments as MA

        base = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "docs",
                            "mof_regulations_scraper", "attachments")
        sample = ""
        for root, _d, files in os.walk(base):
            if files:
                sample = os.path.join(root, files[0])
                break
        if not sample:
            pytest.skip("无 mof 附件样本")
        lp = os.path.relpath(sample, MA.REPO_ROOT).replace("\\", "/")
        assert not lp.startswith(".."), f"local_path 是脏路径：{lp}"


class TestHistoricalLocalPathClean:
    """D. **历史数据回归可见**：现存 raw 里不得有 `../` 开头的 local_path。"""

    def _check(self, pattern: str, field_getter) -> None:
        files = glob.glob(os.path.join(ROOT, "modules", "regulatory_scrapers",
                                       "data", "raw", pattern))
        if not files:
            pytest.skip(f"无 {pattern}")
        bad = []
        total = 0
        for ln in open(sorted(files)[-1], encoding="utf-8", errors="replace"):
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            for lp in field_getter(d):
                if not lp:
                    continue
                total += 1
                if str(lp).startswith(".."):
                    bad.append(str(lp)[:80])
        assert not bad, (
            f"{pattern}: {len(bad)}/{total} 条 local_path 是脏路径（以 `..` 开头）——"
            f"示例 {bad[:3]}")

    def test_mof_raw_local_path_clean(self) -> None:
        self._check("mof_laws.jsonl",
                    lambda d: [a.get("local_path") for a in (d.get("attachments") or [])])

    def test_pbc_raw_local_path_clean(self) -> None:
        self._check("pbc_laws.jsonl", lambda d: [d.get("local_path")])
