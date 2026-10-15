# -*- coding: utf-8 -*-
"""tests/test_j9_modules_manifest.py — J9 判据（modules 脚本三态归属）正/负例。

背景（W4 §7.2 A 类）：tools/ 的 manifest 机制（J1–J6）不覆盖 modules/**，
21 个零引用脚本无登记机制（supp_ingest 更是「纸面 collector」）。J9 把同构机制
扩展到 modules：每个 modules/*.py 必属 chain（生产引用）/ independent（清单登记）/
retired（<module>/retired/ + 登记 + 零可执行引用）三态之一。

负面测试纪律（方案 §9.3）：每个新门禁判据配「改一处 → FAIL」负例。本文件用
**临时目录 + 猴子补丁**构造失态场景，不动生产文件。
"""
from __future__ import annotations

import json
import os

import pytest

from gates.gate_config_integrity import _manifest as M

pytestmark = pytest.mark.contract  # 契约类用例（无数据环境可跑）


@pytest.fixture()
def j9_env(tmp_path, monkeypatch):
    """隔离的 J9 运行环境：临时 modules 树 + 临时清单 + 空生产引用扫描。"""
    mods = tmp_path / "modules"
    (mods / "pkg" / "scripts").mkdir(parents=True)
    (mods / "pkg" / "retired").mkdir(parents=True)
    # 生产消费方（链上 argv 形态引用 wired.py）
    chain = tmp_path / "tools"
    chain.mkdir()
    (chain / "run.py").write_text(
        'import os\nPY = "python"\nargv = [PY, os.path.join("C", "wired.py")]\n',
        encoding="utf-8",
    )
    man = {
        "schema_version": "1.0",
        "entries": [
            {"file": "pkg/scripts/indep.py", "category": "independent",
             "entry": "x", "independent_why": "y"},
            {"file": "pkg/retired/old.py", "category": "retired", "entry": "z",
             "independent_why": ""},
        ],
    }
    man_p = mods / "_manifest.json"
    man_p.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (mods / "pkg" / "scripts" / "wired.py").write_text("x = 1\n", encoding="utf-8")
    (mods / "pkg" / "scripts" / "indep.py").write_text("x = 1\n", encoding="utf-8")
    (mods / "pkg" / "retired" / "old.py").write_text("x = 1\n", encoding="utf-8")
    # 注意：orphan.py **不在夹具里**——它只由负例现场创建（正例树必须三态齐洁）。

    monkeypatch.setattr(M, "_MODULES_DIR", str(mods))
    monkeypatch.setattr(M, "_MODULES_MANIFEST", str(man_p))
    monkeypatch.setattr(M, "_MODULES_RETIRED_READMES", ())
    # 生产引用 = 临时 chain 文件（扫描范围收敛到 tmp_path；**动态重扫**——负例会
    # 在夹具返回后新增消费方文件，冻结列表会漏扫）。
    monkeypatch.setattr(M, "ROOT", str(tmp_path))

    def _scan() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for dirpath, _dn, filenames in os.walk(tmp_path):
            for fn in filenames:
                if not fn.endswith((".py", ".yaml", ".yml", ".toml", ".cfg", ".ini")):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, tmp_path).replace("\\", "/")
                if rel == "modules/_manifest.json":
                    continue  # 清单是声明不是消费方
                out.append((rel, open(fp, encoding="utf-8").read()))
        return out

    monkeypatch.setattr(M, "_load_prod_texts", _scan)
    return mods


def _run():
    return M._check_modules_manifest()


def test_positive_all_three_states_pass(j9_env):
    """正例：wired（链上 argv）/ independent（登记）/ retired（登记在册）三态齐备 → PASS。"""
    problems, detail, _w = _run()
    assert problems == [], problems
    assert detail["modules_unowned"] == []
    assert detail["modules_retired_count"] == 1
    assert len(detail["modules_independent"]) == 1


def test_negative_unowned_script_fails(j9_env):
    """负例：既无生产引用又未登记的 orphan.py → J9.6 FAIL。"""
    (j9_env / "pkg" / "scripts" / "orphan.py").write_text("x = 1\n", encoding="utf-8")
    problems, detail, _w = _run()
    assert any("J9.6" in p and "orphan.py" in p for p in problems), problems
    assert "pkg/scripts/orphan.py" in detail["modules_unowned"]


def test_negative_ghost_entry_fails(j9_env):
    """负例：清单登记了不存在的文件 → J9.1 FAIL（幽灵条目）。"""
    man = json.load(open(j9_env / "_manifest.json", encoding="utf-8"))
    man["entries"].append({"file": "pkg/scripts/ghost.py", "category": "independent",
                           "entry": "g", "independent_why": "w"})
    (j9_env / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    problems, _d, _w = _run()
    assert any("J9.1" in p and "ghost.py" in p for p in problems), problems


def test_negative_retired_still_referenced_fails(j9_env):
    """负例：retired 脚本仍被生产 import → J9.4 FAIL（退役不彻底）。"""
    p = j9_env / "pkg" / "scripts" / "consumer.py"
    p.write_text("from old import thing\n", encoding="utf-8")
    problems, _d, _w = _run()
    assert any("J9.4" in p and "old.py" in p for p in problems), problems


def test_negative_retired_without_manifest_fails(j9_env):
    """负例：文件在 retired/ 但清单未登记 → J9.3 FAIL（逃逸引用扫描）。"""
    (j9_env / "pkg" / "retired" / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
    problems, _d, _w = _run()
    assert any("J9.3" in p and "sneaky.py" in p for p in problems), problems


def test_negative_category_path_mismatch_fails(j9_env):
    """负例：category=retired 但文件不在 retired/ → J9.2 FAIL（双向失配）。"""
    man = json.load(open(j9_env / "_manifest.json", encoding="utf-8"))
    man["entries"].append({"file": "pkg/scripts/lying.py", "category": "retired",
                           "entry": "l", "independent_why": ""})
    (j9_env / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (j9_env / "pkg" / "scripts" / "lying.py").write_text("x = 1\n", encoding="utf-8")
    problems, _d, _w = _run()
    assert any("J9.2" in p and "lying.py" in p for p in problems), problems


def test_negative_independent_without_why_fails(j9_env):
    """负例：independent 登记缺 independent_why → J9.6 FAIL（登记须带理由）。"""
    (j9_env / "pkg" / "scripts" / "sloppy.py").write_text("x = 1\n", encoding="utf-8")
    man = json.load(open(j9_env / "_manifest.json", encoding="utf-8"))
    man["entries"].append({"file": "pkg/scripts/sloppy.py", "category": "independent",
                           "entry": "s", "independent_why": "  "})
    (j9_env / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    problems, _d, _w = _run()
    assert any("independent_why" in p and "sloppy.py" in p for p in problems), problems


def test_negative_missing_manifest_fails(monkeypatch, tmp_path):
    """负例：modules/_manifest.json 缺失 → J9 FAIL（归属事实源不得缺席）。"""
    monkeypatch.setattr(M, "_MODULES_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(M, "_MODULES_MANIFEST", str(tmp_path / "none" / "_manifest.json"))
    problems, _d, _w = _run()
    assert any("modules/_manifest.json" in p for p in problems), problems


def test_real_repo_state_is_green():
    """正例（真实仓）：当前 modules 树三态闭合——J9.6 无归属为 0、退役 13、独立 8。"""
    problems, detail, _w = _run()
    assert problems == [], problems
    assert detail["modules_unowned"] == []
    assert detail["modules_retired_count"] == 13
    assert len(detail["modules_independent"]) == 8
