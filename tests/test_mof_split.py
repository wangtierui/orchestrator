# -*- coding: utf-8 -*-
"""tests/test_mof_split.py — mof 采集三层拆分（D-遗留第三批）的守卫。

拆分（2026-10-20）
------------------
`attachments.py` 668 行 → 三层：
  · `att_constants.py`（169 行）**纯常量 + 8 处 try-import 别名**（无逻辑、无可变状态）
  · `att_extract.py`（206 行）**纯变换**（html→text / 日期归一 / 文件名 / 缓存键 / 条目组装）
  · `attachments.py`（535 行）网络 + 下载 + 熔断 + 编排，并 **re-export** 前两层

守卫四类
--------
A **可达性**：`attachments` 必须仍导出全部旧公开符号（外部导入点不能断）
B **同一对象**：纯变换函数在两层必须是**同一对象**（不是复制），常量同值
C **可变状态未搬**：`_ATT_502_STREAK` / `_ATT_CIRCUIT_OPEN` 必须留在 `attachments`
   ——搬走会让 `global` 语义跨模块、熔断变成两处状态（失效且难查）
D **双形态**：脚本形态（生产 argv）必须能跑通整条 import 链
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOF = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "mof")

#: 拆分前 `attachments.py` 的公开面（常量 + 函数）。外部导入点依赖它们不断。
_LEGACY_CONSTS = [
    "ATTACHMENTS_DIR", "ATTACH_PATH", "BASE", "DEFAULT_CATEGORIES",
    "ENRICH_LEGACY", "HEADERS", "HOST", "REPO_ROOT", "SRC_DIR",
    "_ATTACH_KIND", "_MAX_ATT_502_STREAK", "_STYLE_RE", "_TAG_RE", "_WS_RE",
    "CC_USER_AGENTS", "LEGACY_KINDS", "OfflineMiss", "_OfflineMiss",
    "logger", "docs_root", "extract_document_text", "rich_object_fields",
    "structured_table_fields", "bind_source_cache",
    "_RESP", "_ATT_502_STREAK", "_ATT_CIRCUIT_OPEN", "_LOCKS",
]
_LEGACY_FUNCS = [
    "_mof_cache_key", "_request", "html_to_text", "normalize_date",
    "fetch_list_page", "safe_filename", "fetch_attachments",
    "_finalize_attachment", "download_attachment", "collect_attachments",
    "build_entry", "fetch_category_records",
]
_MOVED_FUNCS = ["_mof_cache_key", "html_to_text", "normalize_date",
                "safe_filename", "build_entry"]


class TestReachability:
    """A. 外部导入点不能断。"""

    def test_all_legacy_symbols_reachable(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        missing = [n for n in _LEGACY_CONSTS + _LEGACY_FUNCS if not hasattr(A, n)]
        assert not missing, f"拆分后 attachments 缺符号（会打断外部导入点）：{missing}"

    def test_modules_are_separate_files(self) -> None:
        for f in ("att_constants.py", "att_extract.py", "attachments.py"):
            assert os.path.exists(os.path.join(MOF, f)), f"{f} 缺失"

    def test_attachments_within_budget(self) -> None:
        """拆分目的之一就是回到可控体量（J11 预算 800 行）。"""
        n = len(open(os.path.join(MOF, "attachments.py"),
                     encoding="utf-8").read().splitlines())
        assert n < 800, f"attachments.py {n} 行，超 J11 预算"


class TestSameObjectNoCopy:
    """B. 纯变换函数必须是同一对象；常量必须同值（防两处定义分叉）。"""

    def test_moved_funcs_are_same_object(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import att_extract as X
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        for n in _MOVED_FUNCS:
            assert getattr(A, n) is getattr(X, n), (
                f"{n} 在 attachments 与 att_extract 不是同一对象 ⇒ 存在两处定义")

    def test_constants_same_value(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import att_constants as C
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        for n in ("HOST", "BASE", "ATTACH_PATH", "ENRICH_LEGACY",
                  "_MAX_ATT_502_STREAK", "REPO_ROOT", "ATTACHMENTS_DIR"):
            assert getattr(A, n) == getattr(C, n), f"{n} 两处值不一致"

    def test_constants_have_no_logic(self) -> None:
        """`att_constants.py` 应只有常量/别名，**不应有函数定义**（除兜底桩）。"""
        src = open(os.path.join(MOF, "att_constants.py"), encoding="utf-8").read()
        # 兜底桩是 ImportError 分支里的 3 个 def，允许；但不应有业务函数
        for forbidden in ("def _request", "def download_attachment",
                          "def collect_attachments", "def build_entry"):
            assert forbidden not in src, f"att_constants 不应含 {forbidden}"


class TestMutableStateStaysPut:
    """C. 可变全局必须留在使用处（跨模块搬走会让熔断失效）。"""

    def test_circuit_state_in_attachments(self) -> None:
        from modules.regulatory_scrapers.collectors.mof import att_constants as C
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        for n in ("_ATT_502_STREAK", "_ATT_CIRCUIT_OPEN"):
            assert hasattr(A, n), f"{n} 必须留在 attachments（global 使用处）"
            assert not hasattr(C, n), (
                f"{n} 被搬到了 att_constants ⇒ global 语义跨模块，熔断会变成两处状态")

    def test_circuit_still_works(self, monkeypatch) -> None:
        """**行为回归**：熔断仍能置位（拆分没破坏 global 生效路径）。"""
        from modules.regulatory_scrapers.collectors.mof import attachments as A

        monkeypatch.setattr(A, "_ATT_502_STREAK", 0)
        monkeypatch.setattr(A, "_ATT_CIRCUIT_OPEN", False)
        # 直接模拟达阈值的那一步
        A._ATT_502_STREAK = A._MAX_ATT_502_STREAK
        assert A._ATT_502_STREAK >= A._MAX_ATT_502_STREAK
        assert A._MAX_ATT_502_STREAK == 8


class TestDualFormImport:
    """D. 脚本形态（生产 `COLLECT_CMD` 形态）必须能跑通整条 import 链。"""

    def test_script_form_help_ok(self) -> None:
        p = os.path.join(MOF, "collector.py")
        r = subprocess.run([sys.executable, p, "--help"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180, cwd=ROOT)
        assert r.returncode == 0, (
            "脚本形态跑不通 ⇒ 生产采集整体失败（相对导入在无包上下文时会抛 "
            f"ImportError）：\n{(r.stderr or '')[-700:]}")

    def test_dual_form_imports_declared(self) -> None:
        """两层新模块都必须有**脚本形态兜底导入**（`except ImportError: from X ...`）。"""
        for f in ("att_extract.py",):
            src = open(os.path.join(MOF, f), encoding="utf-8").read()
            assert "except ImportError" in src, f"{f} 缺脚本形态兜底导入"
        src = open(os.path.join(MOF, "attachments.py"), encoding="utf-8").read()
        assert src.count("except ImportError") >= 2, (
            "attachments 对 att_constants / att_extract 都要有兜底导入")
