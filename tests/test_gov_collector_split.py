# -*- coding: utf-8 -*-
"""tests/test_gov_collector_split.py — gov/collector.py 常量层下沉的守卫（第一百零四批）。

背景
----
`gov/collector.py` 841 行里前 204 行几乎全是被 `RobustSession`(99) /
`XzfgkScraper`(245) / `main`(227) 共用的「引导 + try-import 兜底 + 常量」，
且有 **6 处 try-import 别名**（赋值在 `try` 内，AST 依赖闭包捕获不到）。
2026-10-20 把常量层下沉到 `gov_constants.py`（206 行）⇒ `collector.py` **701 行**
（回到 J11 的 800 行预算内），`collector` 仍 re-export 全部符号。

**等价性基线**（`tests/gov_collector_baseline.py` + `tests/cassettes/
gov_collector_baseline.json`）锁定四段结构快照：

  1. 模块级名字全集（36 个，按名）
  2. 常量摘要（容器长度 + 排序键 / 字符串长度 + 前 12 字 / 正则 pattern+flags）
  3. 两个类的方法 → 参数名元组
  4. `main()` 的 argparse 选项与默认值（25 个）

守卫（本文件）
--------------
A **基线一致**：上述四段必须与基线逐项相等（防"搬运时改值/漏符号/签名漂移"）
B **预算**：`collector.py` < 800 行（J11）
C **单一出处**：常量不得在 `collector.py` 里再被重新赋值（防两处分叉）
D **双形态**：脚本形态（生产 `COLLECT_CMD` 形态）必须能跑

**为什么用结构快照而不是只用 cassette**：拆分风险是"漏搬/搬错/签名漂移"，
cassette 覆盖的是 HTTP 行为；且实测两次真实失误（把标准库导入一起搬走、
把引导块搬走）**只有结构快照能立刻抓到**（`cli.count` 25→0 / `ModuleNotFoundError`）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOV = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "gov")
COLL = os.path.join(GOV, "collector.py")
CONST = os.path.join(GOV, "gov_constants.py")

#: 已下沉到 `gov_constants` 的符号（`collector` 必须仍能取到 —— re-export）
MOVED = (
    "LOG", "USER_AGENTS", "_unified_doc_number", "OfflineMiss",
    "bind_source_cache", "_cache_ep", "DEFAULT_HEADERS", "CONTENT_SELECTORS",
    "TITLE_SELECTORS", "_ORGAN_KW", "_NOISE_CLASS_HINTS", "_SCHEME_UPGRADE_RE",
    "CSV_COLUMNS", "SOURCES", "MasterView", "ScrapeConfig",
    "_is_https_scheme_upgrade", "_longest_text_block", "build_config",
    "clean_text", "decode_html", "extract_date", "extract_doc_number",
    "extract_issue_organ", "load_resume", "make_summary", "merge_and_write",
    "merge_with_master", "write_collect_stats", "write_outputs",
    "fetch_gov_attachments", "_SCRAPERS_ROOT", "_STD_LIB_ROOT",
)

#: **刻意留在 collector**（跨调用可变全局 + 与其绑定的缓存函数 + 引导前提）
STAY = ("_RESP_TEXT", "_init_cache", "set_cache_dir", "set_offline",
        "RobustSession", "XzfgkScraper", "main")


class TestBaselineIdentical:
    """A. 结构快照必须与基线逐项一致。"""

    def test_baseline_matches(self) -> None:
        """调 `tests.gov_collector_baseline` 的比对（**单一实现**，不在此重复逻辑）。"""
        from tests import gov_collector_baseline as B

        assert B.main([]) == 0, (
            "结构快照与基线不一致（模块名/常量/方法签名/CLI 选项之一漂移）——"
            "若是有意变更，请 `python -m tests.gov_collector_baseline --write` "
            "并在 CHANGELOG 说明原因")

    def test_baseline_file_exists(self) -> None:
        """基线在 `tests/baselines/` 而**非** `tests/cassettes/`。

        后者是 **HTTP 录制夹具**目录，J13 判据会按夹具 schema
        （`interactions`/`origin`/`sha256`）校验其中每个文件；结构快照不具备回放语义，
        混进去必然被判 "schema 不支持"（2026-10-20 实测踩到）。
        """
        assert os.path.exists(
            os.path.join(ROOT, "tests", "baselines",
                         "gov_collector_baseline.json")), (
            "基线文件缺失（应在 tests/baselines/，不是 tests/cassettes/）")


class TestBudget:
    """B. 行数预算（J11 = 800）。"""

    def test_collector_within_budget(self) -> None:
        n = len(open(COLL, encoding="utf-8").read().splitlines())
        assert n < 800, f"collector.py {n} 行 ≥ 800（J11 预算）"
        print(f"  collector.py {n} 行 / gov_constants.py "
              f"{len(open(CONST, encoding='utf-8').read().splitlines())} 行")

    def test_both_files_exist(self) -> None:
        assert os.path.exists(COLL) and os.path.exists(CONST)


class TestSingleSourceOfTruth:
    """C. 常量不得在 `collector.py` 被重新赋值（防两处分叉）。"""

    @pytest.mark.parametrize("name", [
        "DEFAULT_HEADERS", "CONTENT_SELECTORS", "TITLE_SELECTORS",
        "_ORGAN_KW", "_NOISE_CLASS_HINTS", "_SCHEME_UPGRADE_RE", "CSV_COLUMNS",
    ])
    def test_const_not_reassigned(self, name: str) -> None:
        code = "\n".join(
            ln for ln in open(COLL, encoding="utf-8").read().splitlines()
            if not ln.strip().startswith("#"))
        assert not re.search(rf"^{name}\s*=", code, re.M), (
            f"{name} 在 collector.py 又被赋值了一次 ⇒ 与 gov_constants 两处分叉"
            "（改一处漏一处不会报错，只会行为漂移）")

    def test_moved_symbols_reachable_from_collector(self) -> None:
        import importlib

        m = importlib.import_module(
            "modules.regulatory_scrapers.collectors.gov.collector")
        missing = [n for n in MOVED if not hasattr(m, n)]
        assert not missing, f"collector 未 re-export：{missing}"

    def test_stateful_symbols_stay_in_collector(self) -> None:
        """**可变全局必须留在使用处**（否则 `global` 语义跨模块）。"""
        import importlib

        from modules.regulatory_scrapers.collectors.gov import gov_constants as C

        m = importlib.import_module(
            "modules.regulatory_scrapers.collectors.gov.collector")
        for n in STAY:
            assert hasattr(m, n), f"{n} 必须留在 collector"
        assert not hasattr(C, "_RESP_TEXT"), (
            "_RESP_TEXT 被搬到 gov_constants ⇒ global 语义跨模块，缓存开关会分两处状态")


class TestDualForm:
    """D. 脚本形态（生产形态）必须能跑通整条 import 链。"""

    def test_script_form_help(self) -> None:
        r = subprocess.run([sys.executable, COLL, "--help"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180, cwd=ROOT)
        assert r.returncode == 0, (
            "脚本形态跑不通（生产采集会整体失败）：\n"
            f"{(r.stderr or '')[-700:]}")

    def test_dual_form_import_declared(self) -> None:
        src = open(COLL, encoding="utf-8").read()
        assert "except ImportError" in src, "collector 缺脚本形态兜底导入"
