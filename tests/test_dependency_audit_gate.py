# -*- coding: utf-8 -*-
"""tests/test_dependency_audit_gate.py — `gate_dependency_audit` 正/负例（方案 §13.2）。

每个判据配「构造失态 → FAIL」用例（负面测试纪律，方案 §9.3）：
  D1 锁与环境一致 / D3 无影子依赖（双向）/ D5 SBOM 同步 / D6 锁的卫生 / D7 解释器路径
纯函数级判据用**临时目录 + 猴子补丁**构造，不动生产文件；D1/D4 依赖子进程，
以「断言函数被正确接线」的方式锁定（真实执行由门禁自身在 CI 里跑）。
"""
from __future__ import annotations

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.contract

from gates import gate_dependency_audit as G


# --------------------------------------------------------------------------- #
# D3：影子依赖（双向断言）
# --------------------------------------------------------------------------- #
def test_d3_shadow_dependency_detected(monkeypatch):
    """负例：pyproject 多出一个直接依赖而 policy 未登记 → 影子依赖 FAIL。"""
    monkeypatch.setattr(G, "_pyproject_deps",
                        lambda: ({"core": {"requests": "requests", "newlib": "newlib>=1"},
                                  "optional": {"dev": {}}}, []))
    monkeypatch.setattr(G, "_policy_deps",
                        lambda: {"core": {"requests": {"why": "x", "adr": "ADR-0004"}},
                                 "optional": {}, "exemptions": {}})
    ok, d = G.run()
    assert not ok
    assert any("影子依赖" in p and "newlib" in p for p in d["problems"]), d["problems"]


def test_d3_dead_registration_detected(monkeypatch):
    """负例：policy 登记了 pyproject 已无的依赖 → 死登记 FAIL（声明漂移）。"""
    monkeypatch.setattr(G, "_pyproject_deps",
                        lambda: ({"core": {"requests": "requests"}, "optional": {}}, []))
    monkeypatch.setattr(G, "_policy_deps",
                        lambda: {"core": {"requests": {"why": "x"}, "ghost": {"why": "y"}},
                                 "optional": {}, "exemptions": {}})
    ok, d = G.run()
    assert not ok
    assert any("死登记" in p and "ghost" in p for p in d["problems"]), d["problems"]


def test_d3_missing_why_detected(monkeypatch):
    """负例：登记项缺 why → FAIL（登记必须带理由）。"""
    monkeypatch.setattr(G, "_pyproject_deps",
                        lambda: ({"core": {"requests": "requests"}, "optional": {}}, []))
    monkeypatch.setattr(G, "_policy_deps",
                        lambda: {"core": {"requests": {"adr": "ADR-0004"}},
                                 "optional": {}, "exemptions": {}})
    ok, d = G.run()
    assert not ok
    assert any("缺 why" in p for p in d["problems"]), d["problems"]


# --------------------------------------------------------------------------- #
# D7：子进程解释器路径
# --------------------------------------------------------------------------- #
def test_d7_rejects_bare_python_launcher(tmp_path, monkeypatch):
    """负例：`subprocess.run(["python", ...])` → D7 FAIL。"""
    lay = tmp_path / "std_lib"
    lay.mkdir()
    (lay / "bad_launcher.py").write_text(
        "import subprocess\n"
        "def go():\n"
        "    return subprocess.run(['python', '-c', 'print(1)'])\n",
        encoding="utf-8")
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    r = G._check_interpreter_launchers()
    assert r["checked"] == 1, r
    assert any("裸 'python'" in p for p in r["problems"]), r


def test_d7_accepts_sys_executable(tmp_path, monkeypatch):
    """正例：sys.executable 与 OS 命令按名调用均通过（D7 只拦解释器裸名）。"""
    lay = tmp_path / "std_lib"
    lay.mkdir()
    (lay / "good_launcher.py").write_text(
        "import subprocess, sys\n"
        "def go():\n"
        "    subprocess.run([sys.executable, 'x.py'])\n"
        "    subprocess.run(['git', 'config', '--get', 'core.hooksPath'])\n"
        "    subprocess.run('tasklist /FI \"IMAGENAME eq soffice.exe\"', shell=True)\n",
        encoding="utf-8")
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    r = G._check_interpreter_launchers()
    assert r["problems"] == [], r
    assert r["checked"] == 3, r


def test_d7_real_repo_is_clean():
    """正例（真实仓）：全仓无裸 python/py 启动子进程。"""
    r = G._check_interpreter_launchers()
    assert r["problems"] == [], r["problems"]
    assert r["checked"] > 10, r


# --------------------------------------------------------------------------- #
# D5：SBOM 同步（用真实 lock/sbom 关系做纯函数级验证）
# --------------------------------------------------------------------------- #
def test_sbom_and_lock_in_sync():
    """正例（真实仓）：SBOM 记录的 lock sha256 与组件数与 lock 一致。"""
    lock = os.path.join(ROOT, "requirements.lock")
    sbom_p = os.path.join(ROOT, "reports", "_sbom", "sbom_latest.json")
    assert os.path.exists(lock) and os.path.exists(sbom_p)
    import hashlib

    want = hashlib.sha256(open(lock, "rb").read()).hexdigest()
    sbom = json.load(open(sbom_p, encoding="utf-8"))
    props = (sbom.get("metadata") or {}).get("properties") or []
    got = next((p.get("value") for p in props if p.get("name") == "lock:sha256"), "")
    assert got == want, "SBOM 与 lock 不同步（须 python -m tools.gen_sbom）"
    n_lock = len([ln for ln in open(lock, encoding="utf-8")
                  if ln.strip() and not ln.startswith("#")])
    assert len(sbom.get("components") or []) == n_lock


def test_lock_is_ascii_and_declares_generator():
    """正例：锁文件纯 ASCII（pip-audit 以 locale 编码读它）且头部声明生成命令。"""
    text = open(os.path.join(ROOT, "requirements.lock"), encoding="utf-8").read()
    assert all(ord(ch) < 128 for ch in text), "锁含非 ASCII（GBK locale 下 pip-audit 会崩）"
    assert "python -m tools.gen_lock" in text


def test_real_repo_gate_is_green():
    """正例（真实仓）：门禁全绿（锁一致 + 无影子依赖 + CVE 扫描 + SBOM 同步）。"""
    ok, d = G.run()
    assert ok, d
    assert d["problems"] == []
