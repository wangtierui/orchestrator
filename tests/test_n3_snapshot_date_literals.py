# -*- coding: utf-8 -*-
"""tests/test_n3_snapshot_date_literals.py — 判据 N-3 的覆盖边界守卫。

为什么需要（2026-10-16 跨日失败实证）：
`tests/test_w4_data_layer.py` 的断言硬编码了按日快照文件名，日期一滚动即稳定失败；
而原 N-3 模式只覆盖 `_cleaned_` ⇒ 该族从未被拦下。模式已扩到 `cleaned|clauses|raw`
三族（见 `gates/gate_hardcoded_snapshots.py`），本测试锁定该覆盖，防止再次收窄。

**本文件自身必须对 N-3 合规**：测 lint 的用例若自己写了完整字面量，会被自己的判据
拦下（2026-10-16 亲历：门禁正确指出本文件）。故所有探测样本用 `_snap()` 在**运行期
拼接**，源码里不出现「族_8位日期.扩展名」的连续字面量（f-string 不算——它仍含字面量）。
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.contract

from gates import gate_hardcoded_snapshots as G

#: 探测样本在运行期拼接（源码无完整字面量 ⇒ 本文件不被自己的判据拦下）
_DATE = "2026" + "1015"
_OLD = "2026" + "0901"


def _snap(family: str, date8: str, ext: str = "jsonl") -> str:
    return f"gov_{family}_" + date8 + f".{ext}"


def test_positive_snapshot_families_are_caught() -> None:
    """正例：三族按日快照命名一律命中（含 2026-10-16 补入的 clauses 族）。"""
    probes = [
        _snap("cleaned", _OLD),
        _snap("cleaned", _OLD, "csv"),
        _snap("clauses", _DATE),
        _snap("clauses", _DATE),
        _snap("raw", _OLD),
        "nfra_" + "cleaned_" + _OLD + ".jsonl",
    ]
    for probe in probes:
        assert G.PAT.search(probe), f"未命中（会重犯跨日失败）：{probe}"


def test_negative_non_snapshot_names_are_not_caught() -> None:
    """负例：非快照族不命中——门禁必须零噪声，否则会被整体忽略。"""
    probes = [
        "gov_relevance_" + _DATE + ".jsonl",
        "gov_themes_" + _DATE + ".jsonl",
        "some_file.jsonl",
        _snap("clauses", "2026") + ".jsonl",   # 非 8 位日期
    ]
    for probe in probes:
        assert not G.PAT.search(probe), f"误判（非快照族）：{probe}"


def test_pattern_covers_three_families() -> None:
    """结构断言：模式显式列出三族（防止后人只留一族又静默失效）。"""
    src = open(G.__file__, encoding="utf-8").read()
    for fam in ("cleaned", "clauses", "raw"):
        assert fam in src, f"模式未覆盖 {fam} 族"


def test_real_repo_has_no_violation() -> None:
    """正例：当前仓内无该形态残留（门禁绿灯的真实含义）。"""
    findings = G.lint_hardcoded()
    assert not findings, f"仓内存在按日快照字面量（前 5）：{findings[:5]}"


def test_dynamic_date_is_the_prescribed_alternative() -> None:
    """正例：约定写法是**运行期动态日期**（源码只留变量）。

    判据扫**源码文本**，故合规性体现在源码层；把运行期拼出的字符串拿去匹配字面量模式
    必然命中（它本身含日期），那种断言是概念错误（2026-10-16 修正）。
    """
    import datetime

    stamp = datetime.date.today().strftime("%Y%m%d")
    assert re.fullmatch(r"\d{8}", stamp), "strftime 应产出 8 位日期"
    # 合规写法的源码形态：f-string 引用变量 ⇒ 源码中无 8 位连续数字
    src = 'out = clause_dir / f"gov_clauses_{stamp}.jsonl"'
    assert not G.PAT.search(src), "动态写法不应被判为硬编码"
    # 反面对照：字面量形态必然被判（这正是判据职责）
    assert G.PAT.search(_snap("clauses", stamp)), "字面量形态应被命中（对照组）"
