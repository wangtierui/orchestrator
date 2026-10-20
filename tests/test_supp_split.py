# -*- coding: utf-8 -*-
"""tests/test_supp_split.py — supp 采集拆分 + 输出路径修正的守卫（第一百零三批 ③b）。

拆分（2026-10-20）
------------------
`supp/ingest.py` 603 行 → 常量/引导下沉到 `supp_constants.py`（52 行），
`ingest.py` 保留 12 个函数并 re-export 常量。

**为什么常量区必须单独成文件**：原常量区里有一条**隐式顺序依赖**——
先算 `HERE`/`SCRAPERS_ROOT` → 再 `sys.path.insert` → 之后才能
`import config.exitcodes / std_lib.scraper_std.pipeline`。混在 13 个函数之间
完全看不出来；抽出后成为文件级事实。

守卫三类
--------
A **路径修正**（第 4 处同类 bug）：`SCRAPERS_ROOT` 必须指向
  `modules/regulatory_scrapers`（不是 `collectors`），且 `RAW_OUT` 规范化后
  必须等于**现存产物路径**（否则 supp 采集白跑到错位置）
B **可达性**：`ingest` 仍导出全部旧符号；常量与 `supp_constants` 同值
C **双形态**：脚本形态（`python collectors/supp/ingest.py`）能 import 通
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPP = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "supp")
SCRAPERS = os.path.join(ROOT, "modules", "regulatory_scrapers")

_LEGACY_CONSTS = ["DOCS_DIR", "HERE", "LOCAL_PDFS", "RAW_OUT", "REPO_ROOT",
                  "SCRAPERS_ROOT", "SRC_DIR", "STATE_DIR", "docs_root"]
_LEGACY_FUNCS = ["md5", "_load_register_doc", "_sanitize_key", "build_records",
                 "enrich_attachments", "enrich_placeholder_records", "land_doc",
                 "main", "parse_pipe_table", "read_text", "register_supplements",
                 "verify_official_source"]


class TestPathsPointToMainData:
    """A. 路径必须指向统一数据根（否则采集白跑）。"""

    def test_scrapers_root_is_scrapers(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        assert os.path.abspath(S.SCRAPERS_ROOT) == os.path.abspath(SCRAPERS), (
            f"SCRAPERS_ROOT={S.SCRAPERS_ROOT} 不是 modules/regulatory_scrapers "
            "⇒ SRC_DIR/RAW_OUT/STATE_DIR 全部错位、采集白跑")

    def test_raw_out_normalizes_to_existing_artifact(self) -> None:
        """**核心断言**：`RAW_OUT`（经 `.json`→`.jsonl` 规范化）必须等于现存产物。"""
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        p = S.RAW_OUT
        if p.endswith(".json"):
            p = p[:-5] + ".jsonl"
        want = os.path.join(SCRAPERS, "data", "raw",
                            "supplementary_regulations.jsonl")
        assert os.path.abspath(p) == os.path.abspath(want), (
            f"RAW_OUT 规范化后={p}，现存产物={want} ⇒ 会写出一份没人读的副本")
        assert os.path.exists(want), "现存 supp 产物不存在（数据目录未落地）"

    def test_src_and_state_dirs_under_scrapers(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        for n in ("SRC_DIR", "STATE_DIR"):
            v = os.path.abspath(getattr(S, n))
            assert v.startswith(os.path.abspath(SCRAPERS) + os.sep), (
                f"{n}={v} 不在 modules/regulatory_scrapers 下")


class TestReachability:
    """B. 外部导入点不能断、常量不得两处定义。"""

    def test_all_legacy_symbols_reachable(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import ingest as I

        missing = [n for n in _LEGACY_CONSTS + _LEGACY_FUNCS if not hasattr(I, n)]
        assert not missing, f"拆分后 ingest 缺符号：{missing}"

    def test_constants_same_value(self) -> None:
        from modules.regulatory_scrapers.collectors.supp import ingest as I
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        for n in ("RAW_OUT", "SRC_DIR", "REPO_ROOT", "SCRAPERS_ROOT", "DOCS_DIR",
                  "STATE_DIR", "LOCAL_PDFS", "HERE"):
            assert getattr(I, n) == getattr(S, n), f"{n} 两处不一致"

    def test_repo_root_equals_scrapers_root(self) -> None:
        """既有口径：`REPO_ROOT` 就是 `SCRAPERS_ROOT`（不引入第三个基准）。"""
        from modules.regulatory_scrapers.collectors.supp import supp_constants as S

        assert S.REPO_ROOT == S.SCRAPERS_ROOT

    def test_ingest_within_budget(self) -> None:
        n = len(open(os.path.join(SUPP, "ingest.py"), encoding="utf-8")
                .read().splitlines())
        assert n < 800, f"ingest.py {n} 行超 J11 预算"


class TestDualFormImport:
    """C. 脚本形态（生产调度形态）必须能 import 通。"""

    def test_script_form_import_ok(self) -> None:
        probe = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "import ingest as M;\n"
            "names=%r;\n"
            "missing=[n for n in names if not hasattr(M,n)];\n"
            "assert not missing, missing\n"
            "assert M.RAW_OUT.endswith('supplementary_regulations.json'), M.RAW_OUT\n"
            "print('ok')\n" % (SUPP, _LEGACY_CONSTS + _LEGACY_FUNCS)
        )
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180, cwd=SUPP)
        assert r.returncode == 0, (
            "脚本形态 import 失败（生产调度会整体失败）：\n"
            f"{(r.stderr or '')[-700:]}")

    def test_dual_form_import_declared(self) -> None:
        src = open(os.path.join(SUPP, "ingest.py"), encoding="utf-8").read()
        assert "except ImportError" in src, "ingest.py 缺脚本形态兜底导入"
