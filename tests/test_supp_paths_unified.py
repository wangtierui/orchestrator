# -*- coding: utf-8 -*-
"""tests/test_supp_paths_unified.py — supp 路径单一事实源 + local_path 跨基准解析（第一百零四批）。

背景
----
**真实故障链**（2026-10-20 实跑 supp 时逐步暴露，共 10 处同一根因的不同表现）：

  `REPO_ROOT = os.path.dirname(HERE)` 只上溯**一级**，而采集器在
  `modules/regulatory_scrapers/collectors/<src>/`（两级）⇒ 实际得到 `.../collectors`
  （**注释却都写 `regulatory_scrapers`**）。`supp` 目录下 **4 个脚本各自派生路径**，
  只有 `supp_constants` 是对的 ⇒ 上一批只修了 `ingest.py`，其余三个仍在错：

  | 文件 | 后果 |
  |---|---|
  | `extract_doc_text.py` | `SRC_DIR` 错位 ⇒ 提取的正文写错位置，`enrich_placeholder_records` 永远读不到 |
  | `ingest_batch.py` | `RAW_LOGICAL_PATH` 错位 + `clean/run_clean_pipeline.py` 拼成不存在的路径 |
  | `ingest_local_dir.py` | `RAW_PATH` 不存在 ⇒ `FileNotFoundError`；`relpath` 产出 `../data/…` 脏路径；`--run-clean` 脚本路径错 |

**另外两处独立缺陷**（`ingest_local_dir.py`，实跑才暴露）：
  · 用 `json.load()` 读 JSONL 主库 ⇒ `JSONDecodeError: Extra data`；
  · 用 `json.dump()` 写成 JSON 数组到 `.json` ⇒ 与主库 `.jsonl` **分叉**且格式不合规。

守卫四类
--------
A **单一事实源**：supp 目录下**不得再有**独立派生的 `SCRAPERS_ROOT`/`SRC_DIR`/`RAW_*`
B **路径正确**：三者算出的 raw 路径规范化后必须等于**现存主库文件**
C **双形态**：三个脚本以**脚本形态**（生产形态）都能 import 通
D **跨基准解析**：`resolve_local_path` 必须能解析**两种历史基准**（仓根 / SCRAPERS）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPP = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "supp")
SCRAPERS = os.path.join(ROOT, "modules", "regulatory_scrapers")
MAIN_RAW = os.path.join(SCRAPERS, "data", "raw")
MAIN_SUPP = os.path.join(MAIN_RAW, "supplementary_regulations.jsonl")

#: 允许**派生**路径的文件（单一事实源本身）
_ALLOWED_DERIVE = {"supp_constants.py"}


class TestSingleSourceOfTruth:
    """A. supp 目录下只得 `supp_constants.py` 一处派生数据根。"""

    def test_no_independent_scrapers_root_derivation(self) -> None:
        """除 `supp_constants.py` 外，**不得**再出现 `SCRAPERS_ROOT = ...` 的独立派生。"""
        offenders = []
        for name in sorted(os.listdir(SUPP)):
            if not name.endswith(".py") or name in _ALLOWED_DERIVE:
                continue
            src = open(os.path.join(SUPP, name), encoding="utf-8",
                          errors="replace").read()
            for ln_no, ln in enumerate(src.splitlines(), 1):
                s = ln.strip()
                if s.startswith("#"):
                    continue
                if re.match(r"^SCRAPERS_ROOT\s*=", s):
                    offenders.append(f"{name}:{ln_no}")
        assert not offenders, (
            "以下位置**独立派生**了 SCRAPERS_ROOT（应改为从 supp_constants 导入）："
            f"{offenders}")

    def test_no_independent_src_dir_derivation(self) -> None:
        """同样禁止独立派生 `SRC_DIR`（`_sources` 目录）。"""
        offenders = []
        for name in sorted(os.listdir(SUPP)):
            if not name.endswith(".py") or name in _ALLOWED_DERIVE:
                continue
            src = open(os.path.join(SUPP, name), encoding="utf-8",
                          errors="replace").read()
            for ln_no, ln in enumerate(src.splitlines(), 1):
                s = ln.strip()
                if s.startswith("#"):
                    continue
                if re.match(r'^SRC_DIR\s*=\s*os\.path\.join', s):
                    offenders.append(f"{name}:{ln_no}")
        assert not offenders, (
            f"以下位置独立派生 SRC_DIR（应改为从 supp_constants 导入）：{offenders}")

    def test_three_modules_import_from_constants(self) -> None:
        for name in ("extract_doc_text.py", "ingest_batch.py",
                     "ingest_local_dir.py"):
            src = open(os.path.join(SUPP, name), encoding="utf-8",
                          errors="replace").read()
            assert "supp_constants" in src, f"{name} 未引用 supp_constants"


class TestPathsPointToRealArtifact:
    """B. 三者算出的 raw 路径必须指向**现存的**主库文件。"""

    def test_raw_paths_normalize_to_existing(self) -> None:
        import importlib

        expects = {
            "ingest_batch": ("RAW_LOGICAL_PATH",),
            "ingest_local_dir": ("RAW_PATH",),
        }
        for mod, attrs in expects.items():
            m = importlib.import_module(
                f"modules.regulatory_scrapers.collectors.supp.{mod}")
            for a in attrs:
                v = os.path.abspath(getattr(m, a))
                norm = v[:-5] + ".jsonl" if v.endswith(".json") else v
                assert os.path.abspath(norm) == os.path.abspath(MAIN_SUPP), (
                    f"{mod}.{a}={v} 规范化后 {norm} != 主库 {MAIN_SUPP}")

    def test_clean_pipeline_path_exists(self) -> None:
        """`--run-clean` 拼出的脚本必须真实存在（修复前指向不存在的 `collectors/clean/`）。"""
        import importlib

        for mod in ("ingest_batch", "ingest_local_dir"):
            m = importlib.import_module(
                f"modules.regulatory_scrapers.collectors.supp.{mod}")
            cand = os.path.join(os.path.abspath(m.SCRAPERS_ROOT), "clean",
                                "run_clean_pipeline.py")
            assert os.path.exists(cand), (
                f"{mod} 的 --run-clean 脚本路径不存在：{cand}")

    def test_no_json_dump_of_rows(self) -> None:
        """禁止再手写 `json.dump(rows)` 落 raw（会写成 JSON 数组、格式不合规）。"""
        src = open(os.path.join(SUPP, "ingest_local_dir.py"),
                      encoding="utf-8").read()
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        assert "json.dump(rows" not in code, (
            "ingest_local_dir.py 仍在用 json.dump(rows) 落 raw —— "
            "应改用 write_raw_jsonl（JSONL + 首行 _meta + 原子写）")
        assert "read_raw_records" in code, (
            "ingest_local_dir.py 未用 read_raw_records 读主库（json.load 读不了 JSONL）")


class TestDualFormImport:
    """C. 三个脚本都必须能以**脚本形态**（生产形态）import 通。"""

    @pytest.mark.parametrize("mod", ["extract_doc_text", "ingest_batch",
                                     "ingest_local_dir"])
    def test_script_form_import(self, mod: str) -> None:
        probe = (
            f"import sys; sys.path.insert(0, r'{SUPP}');"
            f"import {mod} as M; print('ok')"
        )
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180, cwd=SUPP)
        assert r.returncode == 0, (
            f"{mod} 脚本形态 import 失败（生产会整体失败）：\n"
            f"{(r.stderr or '')[-600:]}")


class TestCrossBaseResolver:
    """D. `resolve_local_path` 必须能解析**两种历史基准**。"""

    def test_resolves_repo_base(self, tmp_path, monkeypatch) -> None:
        """仓根基准（`modules/…`）：返回绝对路径且存在。"""
        from std_lib.scraper_std import local_paths as LP

        p = tmp_path / "modules" / "regulatory_scrapers" / "data" / "docs" / "x.pdf"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        monkeypatch.setattr(LP, "_WS_ROOT", str(tmp_path))
        got = LP.resolve_local_path("modules/regulatory_scrapers/data/docs/x.pdf")
        assert os.path.exists(got), got

    def test_resolves_scrapers_base(self, tmp_path, monkeypatch) -> None:
        """SCRAPERS 基准（`data/docs/…`）：也能解析到同一文件。"""
        from std_lib.scraper_std import local_paths as LP

        p = tmp_path / "modules" / "regulatory_scrapers" / "data" / "docs" / "y.pdf"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"y")
        monkeypatch.setattr(LP, "_WS_ROOT", str(tmp_path))
        monkeypatch.setattr(
            LP, "SCRAPERS_ROOT",
            os.path.join(str(tmp_path), "modules", "regulatory_scrapers"))
        got = LP.resolve_local_path("data/docs/y.pdf")
        assert os.path.exists(got), got

    def test_must_exist_returns_empty_not_wrong_path(self) -> None:
        """**不给错路径**：`must_exist=True` 时解析不到就返空，而不是给一个猜的路径。"""
        from std_lib.scraper_std.local_paths import resolve_local_path

        got = resolve_local_path("data/docs/__definitely_not_here__.pdf",
                                 must_exist=True)
        assert got == "", f"must_exist=True 却返回了路径：{got}"

    def test_real_data_all_reachable(self) -> None:
        """**真实数据回归**：五个源里有 local_path 的记录，解析可达率应接近 100%。"""
        import json

        from std_lib.scraper_std.local_paths import resolve_local_path

        files = {
            "mof_laws.jsonl": lambda d: [a.get("local_path")
                                         for a in (d.get("attachments") or [])],
            "pbc_laws.jsonl": lambda d: [d.get("local_path")],
        }
        checked = 0
        for fn, getter in files.items():
            p = os.path.join(MAIN_RAW, fn)
            if not os.path.exists(p):
                continue
            tot = ok = 0
            for ln in open(p, encoding="utf-8", errors="replace"):
                if not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                for lp in getter(d):
                    if not lp:
                        continue
                    tot += 1
                    r = resolve_local_path(lp)
                    if r and os.path.exists(r):
                        ok += 1
            if tot:
                assert ok / tot >= 0.99, (
                    f"{fn}: 解析可达 {ok}/{tot} < 99% ⇒ 基准识别逻辑失效")
                checked += 1
        if not checked:
            pytest.skip("无 raw 数据可回归")
