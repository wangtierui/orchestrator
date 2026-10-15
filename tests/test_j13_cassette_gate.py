# -*- coding: utf-8 -*-
"""tests/test_j13_cassette_gate.py — J13（夹具完整性判据）正负例。

与 `test_j9_modules_manifest.py` 同构手法：临时目录 + 猴子补丁，把判据的路径常量
指向临时夹具树，现场构造五类失态（漏登记 /幽灵登记 /摘要漂移 /正文篡改 /凭据残留），
逐条断言判据确实 FAIL —— 判据只有**会失败**才值得信任。
"""
from __future__ import annotations

import json
import os

import pytest

from gates.gate_config_integrity import _cassette as C

pytestmark = pytest.mark.contract


def _write_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


@pytest.fixture()
def j13_env(tmp_path, monkeypatch):
    """构造一棵含单个合法夹具的临时树（含 manifest），返回 (cassettes_dir, tests_dir)。"""
    cassettes = tmp_path / "cassettes"
    tests_dir = tmp_path / "tests"
    monkeypatch.setattr(C, "TESTS_DIR", str(tests_dir))
    monkeypatch.setattr(C, "CASSETTES_DIR", str(cassettes))
    monkeypatch.setattr(C, "MANIFEST", str(cassettes / "manifest.json"))

    # `from tests import http_cassette` 直接可用：conftest 已 bootstrap 仓根，
    # `tests` 是包——此处**不做模块路径引导**（gate_import_bootstrap：注入数只减不增）。
    from tests import http_cassette as HC

    # 注入临时根目录（判据把 HC.CASSETTES_ROOT 指向 CASSETTES_DIR；这里预置同值，
    # 使夹具构造与 manifest 摘要计算都落在临时树上）
    monkeypatch.setattr(HC, "CASSETTES_ROOT", str(cassettes))

    doc = HC.make_cassette(source="gov", origin="synthetic", interactions=[{
        "method": "GET", "url": "https://x.gov/a", "status": 200,
        "headers": {"Content-Type": "text/html"}, "body": "<html>ok</html>"}])
    _write_json(str(cassettes / "one.json"), doc)
    # manifest 用真实 sha256
    import hashlib

    with open(cassettes / "one.json", "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    _write_json(str(cassettes / "manifest.json"), {
        "schema_version": 1,
        "cassettes": [{"name": "one", "source": "gov", "origin": "synthetic",
                       "interactions": 1, "sha256": sha}],
    })
    return cassettes, tests_dir


def _run():
    return C._check_cassette_manifest(".")


def test_positive_clean_tree(j13_env):
    """正例：合法夹具 + 一致 manifest ⇒ 无问题。"""
    problems, detail = _run()
    assert problems == [], problems
    assert detail["cassette_count"] == 1


def test_negative_missing_manifest_entry(j13_env):
    """负例 J13.1：新增夹具未登记 manifest ⇒ FAIL。"""
    import shutil

    shutil.copy(j13_env[0] / "one.json", j13_env[0] / "orphan.json")
    problems, _d = _run()
    assert any("J13.1" in p and "orphan" in p for p in problems), problems


def test_negative_ghost_manifest_entry(j13_env):
    """负例 J13.1：manifest 登记了不存在的夹具 ⇒ FAIL。"""
    man = json.load(open(j13_env[0] / "manifest.json", encoding="utf-8"))
    man["cassettes"].append({"name": "ghost", "sha256": "x", "origin": "synthetic"})
    _write_json(str(j13_env[0] / "manifest.json"), man)
    problems, _d = _run()
    assert any("J13.1" in p and "ghost" in p for p in problems), problems


def test_negative_digest_drift(j13_env):
    """负例 J13.2：夹具被手改、**结构自洽但 manifest 摘要陈旧** ⇒ FAIL。

    构造要点：同步更新 `body_sha256`（让 J13.3 通过），只不同步 manifest 的文件级
    sha256——这正是「有人改了夹具但没重跑 --rebuild-manifest」的典型形态。
    """
    import hashlib

    doc = json.load(open(j13_env[0] / "one.json", encoding="utf-8"))
    doc["interactions"][0]["body"] = "<html>改了但结构自洽</html>"
    doc["interactions"][0]["body_sha256"] = hashlib.sha256(
        doc["interactions"][0]["body"].encode("utf-8")).hexdigest()
    _write_json(str(j13_env[0] / "one.json"), doc)
    problems, _d = _run()
    assert any("J13.2" in p for p in problems), problems
    assert not any("J13.3" in p for p in problems), "结构应自洽，不该由 J13.3 命中"


def test_negative_body_tamper_detected(j13_env):
    """负例 J13.3：正文与 body_sha256 不一致 ⇒ 自校验 FAIL。"""
    doc = json.load(open(j13_env[0] / "one.json", encoding="utf-8"))
    doc["interactions"][0]["body"] = "<html>改内容</html>"
    _write_json(str(j13_env[0] / "one.json"), doc)
    problems, _d = _run()
    assert any("J13.3" in p for p in problems), problems


def test_negative_bad_origin(j13_env):
    """负例 J13.5：origin 非法（既非 recorded 也非 synthetic）⇒ FAIL。"""
    doc = json.load(open(j13_env[0] / "one.json", encoding="utf-8"))
    doc["origin"] = "unknown"
    _write_json(str(j13_env[0] / "one.json"), doc)
    problems, _d = _run()
    assert any("J13.5" in p or "J13.3" in p for p in problems), problems


def test_negative_secret_leak(j13_env):
    """负例 J13.4：夹具含凭据明文 ⇒ FAIL（对齐 gate_secret_scan）。"""
    import hashlib

    doc = json.load(open(j13_env[0] / "one.json", encoding="utf-8"))
    # 真实泄露形态：录制时把 Authorization 头原样写进夹具（_scrub_headers 漏网之鱼）
    doc["interactions"][0]["headers"]["Authorization"] = "Bearer sk-live-abcdef123456"
    doc["interactions"][0]["body_sha256"] = hashlib.sha256(
        doc["interactions"][0]["body"].encode("utf-8")).hexdigest()
    _write_json(str(j13_env[0] / "one.json"), doc)
    # manifest 摘要需重算，聚焦 J13.4
    with open(j13_env[0] / "one.json", "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    man = json.load(open(j13_env[0] / "manifest.json", encoding="utf-8"))
    man["cassettes"][0]["sha256"] = sha
    _write_json(str(j13_env[0] / "manifest.json"), man)
    problems, _d = _run()
    assert any("J13.4" in p for p in problems), problems
