# -*- coding: utf-8 -*-
"""tests/test_baseline_delete_semantics.py — **「删符号」的确切语义**（第一百零六批 T2）。

问题（用户提出）
----------------
`test_baseline_matches` / `test_baseline_identical` 依赖基线工具，而它做的是
**单向包含**比对。用户问：若将来**有意删符号**，会怎样？

精确回答（本文件用可执行断言固化，而不是只写在文档里）
------------------------------------------------------
`expected` 是**全量模块级名字**（含导入名），但比对时按 `_is_noise` 分成两类：

| 被删的符号类型 | 例子 | 后果 |
|---|---|---|
| **本仓符号**（`def`/`class`/赋值） | `main` / `RobustSession` / `HOST` | `missing` 非空 ⇒ **FAIL**（必须修） |
| **导入名**（标准库/三方/内部别名） | `csv` / `Any` / `_att` | 过去**完全静默** ⇒ 现在**显式告警**（不阻断） |

**为什么"导入名"不判 FAIL**：拆分后 `csv`/`html` 本就该迁到 `mof_io`，
若判 FAIL 就等于"拆分必须保留所有无用的导入" —— 那会把守卫变成负担，
最终被 `# noqa` 绕过（比没有守卫更糟）。

**为什么不能静默**（这条是本次加固的核心）：`from collector import csv` 是合法
调用；删了它就断，而原来**毫无提示**。现在会打印汇总提示行，且写进 CHANGELOG
就有了变更记录。

**额外盲区已补**：若被删的"导入名"当前**仍被本模块定义/赋值**（如自定义了
`json()` 函数），说明它其实是本仓符号 ⇒ **提升为 FAIL**。

第三类后果：**不受本工具保护**
--------------------------------
单向包含**不检查"新增"** —— 那是有意的（拆分后 AST 自然多出 re-export 目标）。
因此"改名"若表现为"删旧名 + 加新名"，只有**删旧名**会被抓；
"新名是否语义正确"由**具体业务守卫**（如 `test_mof_early_stop`）负责。
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def fake_baseline(tmp_path, monkeypatch):
    """把基线文件指到 tmp，并可注入伪造的"当前快照"。"""
    from tests import collector_baseline as B

    p = tmp_path / "x_baseline.json"
    monkeypatch.setattr(B, "baseline_path", lambda src: str(p))

    def _write(expected: list[str]) -> None:
        p.write_text(json.dumps({
            "module_names": {"expected": expected, "present": expected,
                             "missing": []},
            "const_digest": {}, "class_methods": {}, "cli": {"count": 0,
                                                            "options": []},
        }, ensure_ascii=False), encoding="utf-8")

    def _inject(present: list[str], defined: list[str]) -> None:
        monkeypatch.setattr(B, "build", lambda src, expected=None: {
            "module_names": {"expected": list(expected or present),
                             "present": present, "missing": []},
            "defined_names": defined,
            "const_digest": {}, "class_methods": {}, "cli": {"count": 0,
                                                            "options": []},
        })

    return B, _write, _inject


class TestDeleteRepoOwnSymbolFails:
    """删「本仓符号」⇒ 必须 FAIL（外部 `from collector import X` 会断）。"""

    def test_def_symbol_missing_is_fail(self, fake_baseline) -> None:
        B, write, inject = fake_baseline
        write(["main", "HOST"])
        inject(present=["HOST"], defined=["HOST"])       # `main` 被删
        rep = B.compare_report("mof")
        assert any("main" in f for f in rep["fails"]), (
            f"删掉 `def main` 却未 FAIL：{rep}")
        assert not rep["warnings"] or "main" not in rep["warnings"]

    def test_const_symbol_missing_is_fail(self, fake_baseline) -> None:
        B, write, inject = fake_baseline
        write(["HOST", "BASE"])
        inject(present=["BASE"], defined=["BASE"])       # `HOST` 被删
        rep = B.compare_report("mof")
        assert any("HOST" in f for f in rep["fails"]), rep


class TestDeleteImportNameWarns:
    """删「导入名」⇒ 告警可见，但**不** FAIL（拆分必然发生）。"""

    def test_stdlib_import_missing_is_warning(self, fake_baseline) -> None:
        B, write, inject = fake_baseline
        write(["main", "csv"])                            # csv 是标准库导入名
        inject(present=["main"], defined=["main"])        # csv 已不在
        rep = B.compare_report("mof")
        assert not rep["fails"], f"删导入名不应 FAIL：{rep}"
        assert "csv" in rep["warnings"], f"删导入名必须告警（不能静默）：{rep}"

    def test_alias_missing_is_warning(self, fake_baseline) -> None:
        B, write, inject = fake_baseline
        write(["main", "_att"])                           # 内部别名
        inject(present=["main"], defined=["main"])
        rep = B.compare_report("mof")
        assert not rep["fails"], rep
        assert "_att" in rep["warnings"], rep


class TestSameNameDefinitionIsPromoted:
    """**补盲区**：被删的名字虽是"导入名形态"，但当前仍定义同名对象 ⇒ 提升为 FAIL。"""

    def test_import_like_name_but_defined_now(self, fake_baseline) -> None:
        B, write, inject = fake_baseline
        # `json` 是标准库名（会被判为噪声），但当前模块**自己定义了** `json`
        write(["main", "json"])
        inject(present=["main"], defined=["main", "json"])
        rep = B.compare_report("mof")
        assert any("json" in f for f in rep["fails"]), (
            f"当前仍定义 `json` 却被当过噪声放过（真盲区）：{rep}")
        assert "json" not in rep["warnings"]


class TestRealBaselinesStillPass:
    """真实基线必须仍然"一致"（本次加固不得引入假 FAIL）。"""

    @pytest.mark.parametrize("src", ["mof", "gov"])
    def test_real_baseline_fails_empty(self, src: str) -> None:
        from tests import collector_baseline as B

        rep = B.compare_report(src)
        assert not rep["fails"], f"{src} 基线出现 FAIL：{rep['fails']}"
