# -*- coding: utf-8 -*-
"""tests/test_contract_manifest.py — 契约清单消费断言（v2 §3.4 / P1-4）

背景：`config/schema/contract_manifest.json` 在改造前**全仓 0 处代码读取**，因此静默漂移
两处（`detail_table_fields.count=10` 实为 14；`base_json_keys.file` 指向不存在的目录）。
本测试与 `gates.gate_contract._manifest_checks()` 同判据，额外覆盖"漂移能被抓出"的负例。
"""
from __future__ import annotations

import json
import os

import pytest

from gates import gate_contract


def test_manifest_has_no_drift():
    """正例：清单每条 file 存在、symbol 可加载、count 与实现一致。"""
    problems, detail = gate_contract._manifest_checks()
    assert problems == [], f"契约清单漂移：{problems}"
    assert detail["version"], "清单须有顶层 version"
    # 至少覆盖 6 条契约（含 base_json_keys 的 4 个键集）
    assert len(detail["checked"]) >= 6


def test_manifest_declares_consumer():
    """清单自身须声明消费方（防再次出现"零读取"）。"""
    with open(gate_contract._MANIFEST, encoding="utf-8") as fh:
        man = json.load(fh)
    assert "gate_contract" in str(man.get("consumer", ""))


def test_detects_count_drift(tmp_path, monkeypatch):
    """负例：把某条 count 改错 → 必须报出漂移。"""
    with open(gate_contract._MANIFEST, encoding="utf-8") as fh:
        man = json.load(fh)
    man["contracts"]["registry_theme_fields"]["count"] = 99
    p = tmp_path / "contract_manifest.json"
    p.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gate_contract, "_MANIFEST", str(p))

    problems, _ = gate_contract._manifest_checks()
    assert any("registry_theme_fields" in x and "99" in x for x in problems), problems


def test_detects_missing_file(tmp_path, monkeypatch):
    """负例：file 指向不存在的路径（历史漂移形态）→ 必须报出。"""
    with open(gate_contract._MANIFEST, encoding="utf-8") as fh:
        man = json.load(fh)
    man["contracts"]["base_json_keys"]["file"] = "modules/nonexistent/dir/"
    p = tmp_path / "contract_manifest.json"
    p.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gate_contract, "_MANIFEST", str(p))

    problems, detail = gate_contract._manifest_checks()
    assert any("base_json_keys" in x and "不存在" in x for x in problems), problems
    assert "modules/nonexistent/dir/" in detail["missing"]


def test_detects_missing_version(tmp_path, monkeypatch):
    with open(gate_contract._MANIFEST, encoding="utf-8") as fh:
        man = json.load(fh)
    man.pop("version", None)
    p = tmp_path / "contract_manifest.json"
    p.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gate_contract, "_MANIFEST", str(p))

    problems, _ = gate_contract._manifest_checks()
    assert any("version" in x for x in problems), problems


def test_dotted_path_derivation():
    assert gate_contract._dotted("a/b/c.py") == "a.b.c"
    assert gate_contract._dotted("a/b/__init__.py") == "a.b"
    assert gate_contract._dotted("interfaces/contract.py") == "interfaces.contract"


@pytest.mark.parametrize("name", ["cleaned_csv_columns", "registry_csv_fields",
                                  "registry_theme_fields", "detail_table_fields",
                                  "theme_map", "base_json_keys"])
def test_every_declared_contract_is_verified(name):
    """每条契约都必须落进 checked（无 symbol/count 的仅登记定位，故显式豁免 base_json_keys）。"""
    _, detail = gate_contract._manifest_checks()
    assert name in detail["checked"], f"{name} 未被断言覆盖"
    if name != "base_json_keys":
        assert isinstance(detail["checked"][name], dict)
        assert os.sep not in detail["checked"][name]["module"], "模块名应为点号形式"
