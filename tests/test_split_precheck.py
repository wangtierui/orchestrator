# -*- coding: utf-8 -*-
"""tests/test_split_precheck.py — 拆分前置检查工具的契约测试。

核心守卫：**判据必须能复现 E1–E4 的真实故障**（不是测"能跑"）。
先用人造样本锁判据逻辑（恒可跑），再用**已拆文件的备份**验证能复现真实故障。

四类历史故障：
  ① import 未随迁（E4：`html_to_text` 缺 `lxml` + `re`）
     —— **最隐蔽**的一类：`lxml` 缺失时函数内 try 抛 NameError ⇒
     **静默落到正则兜底**，`<script>` 不被剥离、正文多出换行，不报错。
  ② 模块级 logger/引导块（E2：`LOG` 不属任何闭包 ⇒ AST 捕获不到）
  ③ try-import 别名（E2：`_unified_doc_number` 赋值在 try 内）
  ④ 常量跨层共用（E2/E4：`BASE` 被多块用 ⇒ 复制两份必漏改其一）
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "reports", "_tmp")

SAMPLE = '''# -*- coding: utf-8 -*-
"""样本"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
LOG = __import__("logging").getLogger("x")
_PAT = re.compile(r"a")
try:
    from std_lib.x import helper
except ImportError:  # pragma: no cover
    helper = None


def pure(a):
    return _PAT.sub("", a) + re.escape(a)


def needs_lxml(html):
    """缺 lxml 时会**静默降级**（历史真实故障）。"""
    try:
        doc = lxml.html.fromstring(html)
        return doc.text_content()
    except Exception:
        return re.sub(r"<[^>]+>", "", html)


def uses_repo(p):
    return os.path.join(paths.DATA_DIR, p)


def uses_helper(x):
    return helper(x)
'''


def _ensure():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from bootstrap import bootstrap

    bootstrap("all")


@pytest.fixture()
def pc_of(tmp_path):
    _ensure()
    from tools import split_precheck as SP

    p = tmp_path / "sample_mod.py"
    p.write_text(SAMPLE, encoding="utf-8")
    return SP.survey(str(p))


def test_detects_third_party_import(pc_of) -> None:
    """**关键判据**：三方依赖未 import 也必须报出。

    这条曾因实现缺陷漏报（Attribute 主名同时出现在 `symbols` 里被误排除），
    而它对应的正是 E4 的静默降级故障——判据漏它＝工具不可信。
    """
    d = pc_of.deps["needs_lxml"]
    assert "lxml" in d.missing_third, (
        f"未报出缺失的三方依赖（正是 E4 的静默降级故障）：{d.missing_third}")


def test_no_false_positive_for_module_level_import(pc_of) -> None:
    """负例：`re` 已在模块级 import ⇒ **不得**报为缺失（零噪声是工具可用的前提）。"""
    assert "re" not in pc_of.deps["needs_lxml"].missing_third
    assert not pc_of.deps["pure"].missing_imports
    assert not pc_of.deps["pure"].missing_third


def test_detects_module_level_logger_and_paths(pc_of) -> None:
    """正例：模块级 `LOG`/`_HERE`/`_ROOT` 被标为风险（E2 故障类别②）。"""
    blob = " ".join(pc_of.module_level_risks)
    for nm in ("LOG", "_HERE", "_ROOT"):
        assert nm in blob, f"未标出模块级 {nm}：{pc_of.module_level_risks}"


def test_detects_try_import_alias(pc_of) -> None:
    """正例：try-import 别名被单列（E2 故障类别③）。"""
    assert any("helper" in a for a in pc_of.try_import_aliases), \
        pc_of.try_import_aliases


def test_detects_repo_module_import(pc_of) -> None:
    """正例：仓内模块未 import 也报（脚本形态下会 `ModuleNotFoundError`）。"""
    assert "paths" in pc_of.deps["uses_repo"].missing_repo


def test_unknown_block_is_reported(pc_of) -> None:
    """负例：不存在于顶层的名字必须报「不在顶层定义里」，不得静默跳过。"""
    out = pc_of  # 已有 pc
    from tools import split_precheck as SP

    assert "不在顶层定义里" in SP.render(out, ["不存在的函数"])


def test_tool_is_readonly(tmp_path) -> None:
    """负例：**只读**——工具绝不修改被检查的文件（它的核心承诺）。"""
    _ensure()
    from tools import split_precheck as SP

    p = tmp_path / "ro_mod.py"
    src = "import os\n\n\ndef f():\n    return os.getcwd()\n"
    p.write_text(src, encoding="utf-8")
    SP.render(SP.survey(str(p)), ["f"])
    assert p.read_text(encoding="utf-8") == src, "工具修改了源文件"


# --------------------------------------------------------------------------- #
# 真实备份：判据须复现 E2/E4 的实际故障（备份不在则跳过）
# --------------------------------------------------------------------------- #
def _backup(name: str) -> str:
    f = os.path.join(BACKUPS, name)
    if not os.path.exists(f):
        pytest.skip(f"备份不在：{name}（reports/_tmp 已清理）")
    return f


def test_reproduces_e4_silent_degradation() -> None:
    """正例：**复现 E4 的静默降级**——`html_to_text` 缺 `lxml`/`re`。"""
    _ensure()
    from tools import split_precheck as SP

    pc = SP.survey(_backup("nfra_collector_before.py"))
    d = pc.deps["html_to_text"]
    miss = d.missing_third | d.missing_imports
    assert "lxml" in miss, f"未复现 E4 的 lxml 缺失：{miss}"


def test_reproduces_e2_module_level_and_try_import() -> None:
    """正例：**复现 E2 的两类故障**——模块级 `LOG` 与 try-import `_unified_doc_number`。"""
    _ensure()
    from tools import split_precheck as SP

    pc = SP.survey(_backup("gov_parse_before.py"))
    assert any("LOG" in r for r in pc.module_level_risks), pc.module_level_risks
    assert any("_unified_doc_number" in a for a in pc.try_import_aliases), \
        pc.try_import_aliases


def test_reproduces_e4_path_constant_risk() -> None:
    """正例：**复现 E4 的路径常量连锁缺失**（`_GUIDE_ROOT`/`_ROOT`/`_ATT_DIR`）。"""
    _ensure()
    from tools import split_precheck as SP

    pc = SP.survey(_backup("nfra_collector_before.py"))
    blob = " ".join(pc.module_level_risks)
    assert "_ROOT" in blob and "_ATT_DIR" in blob, pc.module_level_risks
