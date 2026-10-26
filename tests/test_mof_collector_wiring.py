# -*- coding: utf-8 -*-
"""tests/test_mof_collector_wiring.py — mof 采集器**脚本形态**的两个致命缺陷守卫。

## 事实（2026-10-26 整链实测，`collect:mof rc=1`，stderr 1,988,492B）
1. `_att_mod` 未绑定：`try: from . import attachments as _att_mod / except ImportError: pass`
   —— 链上**以脚本形态**跑 `collector.py`，相对导入必然失败，`_att_mod` 从未赋值，
   而收尾汇总要读 `_att_mod.REUSE_STATS` ⇒ `NameError`。**数据已落盘，步骤却判 FAIL**。
2. `EarlyStopGuard` 三个属性未初始化：`self.reason / skipped_in_run /
   restored_from_history` 被写在 `deadline` property 的 `return` 之后（永远不执行），
   而 `__slots__` 声明了它们 ⇒ 第一次 `+= 1` 就 `AttributeError`。

两者都只在"脚本形态 + 早停/跳过"这条路径上暴露，单测与交互式导入都看不见。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOF = os.path.join(ROOT, "modules", "regulatory_scrapers", "collectors", "mof")


def test_att_mod_has_script_fallback() -> None:
    src = open(os.path.join(MOF, "collector.py"), encoding="utf-8").read()
    assert "from . import attachments as _att_mod" in src
    idx = src.index("from . import attachments as _att_mod")
    tail = src[idx:idx + 400]
    assert "import attachments as _att_mod" in tail, (
        "脚本形态下 _att_mod 未绑定 ⇒ 收尾 NameError（历史故障）")


def test_early_stop_guard_initialises_counters() -> None:
    src = open(os.path.join(MOF, "limits.py"), encoding="utf-8").read()
    # 三个属性必须在 __init__ 里出现
    init = src[src.index("def __init__"):src.index("def ", src.index("def __init__") + 10)]
    for name in ("reason", "skipped_in_run", "restored_from_history"):
        assert f"self.{name}" in init, f"{name} 未在 __init__ 初始化（__slots__ 会 AttributeError）"
    # 且不得再出现"return 之后赋值"的死代码形态
    assert "return self.started_at + self.max_seconds\n        self." not in src


def test_guard_object_really_has_attributes() -> None:
    """按**文件路径**加载（不改 sys.path：门禁要求 tests/ 的注入数只减不增）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mof_limits_probe", os.path.join(MOF, "limits.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    g = mod.EarlyStopGuard(0)
    assert g.skipped_in_run == 0 and g.restored_from_history == 0 and g.reason == ""
    g.skipped_in_run += 1
    assert g.summary()["skipped_in_run"] == 1


def test_script_mode_runs_without_nameerror() -> None:
    """真跑一次脚本形态（1 秒预算）：只要不出现 NameError/AttributeError 即可。

    `--max-seconds 1` 会触发早停分支 —— 正是两个缺陷的暴露路径。
    """
    out = subprocess.run([sys.executable, "collector.py", "--max-seconds", "1",
                          "--outdir", os.path.join(ROOT, "modules", "regulatory_scrapers",
                                                   "data", "raw")],
                         cwd=MOF, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600)
    blob = (out.stdout or "") + (out.stderr or "")
    assert "NameError" not in blob and "AttributeError" not in blob, blob[-800:]
    assert out.returncode == 0, blob[-800:]
