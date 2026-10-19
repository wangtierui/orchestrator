# -*- coding: utf-8 -*-
"""tests/test_j13_shadow_safe_import.py — J13 夹具加载的**同名遮蔽**守卫。

背景（2026-10-17/18/19 三天生产门禁连续 FAIL）
------------------------------------------------
`gates/gate_config_integrity/_cassette.py` 原用 `from tests import http_cassette`。
本仓 `tests/` 是**命名空间包**（无 `__init__.py`），而 `refactor/collectors/tests/`
是**真包**（有 `__init__.py`）——Python 导入规则**真包优先于命名空间包**。
一旦 `refactor/collectors` 进入 `sys.path`（生产跑的 bootstrap 会扫到它），
`import tests` 就解析到那个没有 `http_cassette.py` 的目录，报
`ImportError: cannot import name 'http_cassette' from 'tests'`。

这与判据 J14（同名模块裸名导入禁令）是同一类问题，只是发生在**门禁自身**。

守卫要点：**在同名遮蔽环境下**验证新写法仍有效——只在干净环境里测等于没测
（干净环境下旧写法也"能用"，故障不会复现）。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADOW_DIR = os.path.join(ROOT, "refactor", "collectors")


def _ensure() -> None:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from bootstrap import bootstrap

    bootstrap("all")


@pytest.fixture()
def shadowed(monkeypatch):
    """把含真包 `tests/` 的目录插到 sys.path 最前（**复现生产故障环境**）。

    ⚠️ 必须**清掉 `sys.modules` 里的 `tests`**：`tests` 是命名空间包，一旦被
    导入过，`importlib.find_spec("tests")` 会直接从缓存返回**旧位置**，
    遮蔽就复现不了（单独跑绿、整套跑红——测试顺序依赖）。
    """
    _ensure()
    sys.modules.pop("tests", None)
    for k in [k for k in sys.modules if k.startswith("tests.")]:
        sys.modules.pop(k, None)
    monkeypatch.syspath_prepend(SHADOW_DIR)
    yield SHADOW_DIR
    sys.modules.pop("tests", None)
    for k in [k for k in sys.modules if k.startswith("tests.")]:
        sys.modules.pop(k, None)


def test_shadow_environment_is_really_shadowed(shadowed) -> None:
    """正例：确认测试环境**真的**造成了遮蔽（否则后面几条毫无意义）。"""
    spec = importlib.util.find_spec("tests")
    found = list(spec.submodule_search_locations)[0] if spec else None
    assert found and "refactor" in found, (
        f"未能复现同名遮蔽（import tests 解析到 {found}）——"
        f"本文件的守卫将失去意义")
    assert not os.path.exists(os.path.join(found, "http_cassette.py")), (
        "遮蔽目录里竟有 http_cassette.py ⇒ 遮蔽复现失败")


def test_old_style_import_would_fail_under_shadow(shadowed) -> None:
    """**失态对照**：旧写法（`from tests import http_cassette`）在该环境下必失败。

    这条是"修复必要性"的证据——它必须在遮蔽环境下失败。
    """
    with pytest.raises(ImportError):
        from tests import http_cassette as _HC  # noqa: F401


def test_path_based_loader_survives_shadow(shadowed) -> None:
    """正例：新写法（按文件路径加载）在遮蔽环境下**仍然成功**。"""
    from gates.gate_config_integrity import _cassette as C

    C._cassette_cache = None                    # 清缓存，强制本次真加载
    HC = C._load_cassette_module()
    assert HC is not None, "按路径加载在遮蔽环境下失败（修复无效）"
    assert os.path.basename(HC.__file__) == "http_cassette.py"
    assert hasattr(HC, "load_cassette"), "加载到的不是夹具层"


def test_gate_runs_clean_under_shadow(shadowed) -> None:
    """正例：J13 判据在遮蔽环境下**不报 J13.3**（生产故障已消除）。"""
    from gates.gate_config_integrity import _cassette as C

    C._cassette_cache = None
    problems, detail = C._check_cassette_manifest(ROOT)
    assert not any("J13.3" in p for p in problems), (
        f"遮蔽环境下仍报 J13.3：{problems}")
    assert detail.get("cassette_count", 0) > 0, "遮蔽环境下夹具计数为 0"


def test_loader_does_not_inject_syspath(shadowed) -> None:
    """负例：加载器**不得**做 sys.path 注入（gate_import_bootstrap 纪律：
    注入数只减不增——判据自身不得违反它守护的纪律）。"""
    before = list(sys.path)
    from gates.gate_config_integrity import _cassette as C

    C._cassette_cache = None
    C._load_cassette_module()
    added = [p for p in sys.path if p not in before]
    assert not added, f"加载器注入了 sys.path 项：{added}"


def test_missing_file_falls_back_to_package_import(monkeypatch, tmp_path) -> None:
    """正例：`TESTS_DIR` 下无 `http_cassette.py` ⇒ **回退到包导入**（两级策略的第 2 级）。

    这是测试注入临时树时的既有语义（`test_j13_cassette_gate.py` 只造
    `cassettes/` 子目录、`http_cassette` 仍从真仓导入）⇒ 判据必须回退，
    否则负例构造失效。
    """
    _ensure()
    from gates.gate_config_integrity import _cassette as C

    sys.modules.pop(C._CASSETTE_MODULE, None)
    monkeypatch.setattr(C, "TESTS_DIR", str(tmp_path))
    monkeypatch.setattr(C, "_cassette_cache", None)
    HC = C._load_cassette_module()
    assert HC is not None, "路径不存在时应回退到包导入（两级策略第 2 级）"
    assert hasattr(HC, "load_cassette")


def test_both_paths_missing_returns_none(monkeypatch, tmp_path) -> None:
    """负例：**两级都不可用**才返回 None（真·失态，由调用方报 J13.3）。"""
    _ensure()
    from gates.gate_config_integrity import _cassette as C

    sys.modules.pop(C._CASSETTE_MODULE, None)
    monkeypatch.setattr(C, "TESTS_DIR", str(tmp_path / "空目录"))
    monkeypatch.setattr(C, "_cassette_cache", None)

    import builtins

    real_import = builtins.__import__

    def _no_pkg(name, *a, **k):
        if name == "tests" or name.startswith("tests."):
            raise ImportError("模拟 tests 包不可导入")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_pkg)
    assert C._load_cassette_module() is None
