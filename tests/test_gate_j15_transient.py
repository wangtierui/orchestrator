# -*- coding: utf-8 -*-
"""tests/test_gate_j15_transient.py — J15 读到"写入中间态"时不应判红。

背景（2026-10-28 06:10）：链尾 `gates` 报了 265 条 `index.html:870 括号未闭合`，
但同一文件前后两次手跑 `python -m tools.js_syntax_lint` 都 OK —— 巡检恰好落在
"截断 + 重写"的窗口里。判据不该把**正在写的文件**当成语法错误。

判据（两条）：
  ① 第一次 lint 失败、第二次通过 ⇒ 不产生 problem，且 detail 里留"瞬时"证据；
  ② 两次都失败 ⇒ 照常报问题（不能用重试掩盖真错误）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gates.gate_config_integrity import _js


def _fake_lint(seq):
    """按顺序返回预先写好的 lint 结果。"""
    it = iter(seq)

    def _lint(_path: str) -> dict:
        return next(it)

    return _lint


BAD = {"ok": False, "backend": "esprima", "scripts": 1, "lines": 10,
       "issues": [{"line": 870, "col": 24, "detail": "括号 '{' 未闭合"}]}
GOOD = {"ok": True, "backend": "esprima", "scripts": 1, "lines": 10, "issues": []}


def test_transient_failure_is_ignored(tmp_path, monkeypatch) -> None:
    target = tmp_path / "web" / "assets" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("<script>1</script>", encoding="utf-8")

    calls = {"n": 0}

    def lint(_path: str) -> dict:
        calls["n"] += 1
        return BAD if calls["n"] == 1 else GOOD

    monkeypatch.setattr("tools.js_syntax_lint.lint_file", lint)
    problems, detail = _js._check_inline_js(str(tmp_path))
    assert problems == [], f"瞬时失败被当成真错误：{problems}"
    assert detail.get("transient"), "没有留下'命中写入中间态'的证据"
    assert calls["n"] == 2, "应当只重试一次"


def test_persistent_failure_still_reported(tmp_path, monkeypatch) -> None:
    target = tmp_path / "web" / "assets" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("<script>1</script>", encoding="utf-8")

    monkeypatch.setattr("tools.js_syntax_lint.lint_file", _fake_lint([BAD, BAD]))
    problems, _detail = _js._check_inline_js(str(tmp_path))
    assert problems and "J15" in problems[0], "真错误必须报出来"
