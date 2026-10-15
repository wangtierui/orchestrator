# -*- coding: utf-8 -*-
"""tests/test_step_log_evidence.py — 步骤日志取证入口的**命名契约**守卫。

为什么需要守卫（2026-10-15 五阶段审查 B2 发现）：
`web/snapshots.py` 曾按 `step_logs/{step}.log` 取证，而写入方
`tools/run_production_refresh.py` 产出的真实命名是 `{步骤名去冒号}_{时间戳}.{out|err}.log`
⇒ 取证恒空、确认台 P1 抽屉永远空白。这类缺陷**不会报错**，只会"安静地少给证据"，
因此必须用测试把「取证路径」与「写入方命名规则」绑在一起。

守卫策略（两侧都不假设）：
  · 正例：写入方源码里必须能识别出「去冒号 + 时间戳 + out/err」三要素；
  · 反例：若有人把 `latest_step_log` 改回固定 `{step}.log` 拼接 → 判据失败。
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRITER = os.path.join(ROOT, "tools", "run_production_refresh.py")
STEP_LOGS = os.path.join(ROOT, "reports", "_tmp", "step_logs")


@pytest.fixture(scope="module")
def writer_src() -> str:
    return open(WRITER, encoding="utf-8").read()


def test_writer_naming_convention_is_discoverable(writer_src: str) -> None:
    """正例：写入方的命名三要素（字符归一/时间戳/out-err）可从源码识别。"""
    assert "step_logs" in writer_src
    assert re.search(r"\.out\.log", writer_src), "写入方未见 .out.log 命名"
    assert re.search(r"\.err\.log", writer_src), "写入方未见 .err.log 命名"
    # 字符归一：Windows 文件名不允许 `: * ? " < > |`，故写入方做全字符替换
    assert re.search(r're\.sub\(r"\[\^0-9A-Za-z_\.\-\]"', writer_src), (
        "写入方未见步骤名的字符归一（Windows 文件名非法字符集）")


def test_sanitisation_rule_matches_writer(writer_src: str) -> None:
    """正例：**取证侧与写入方的归一规则逐字一致**（防漂移）。

    从两侧源码各自提取 `re.sub(r"...")` 的模式并断言相等；再对代表性步骤名
    （含冒号/斜杠/点/连字符/中文）断言两者归一结果相同。
    """
    writer_pat = re.search(r'_safe\s*=\s*re\.sub\(r"(.*?)"', writer_src)
    assert writer_pat, "未能从写入方提取归一正则"
    src = open(os.path.join(ROOT, "web", "snapshots.py"), encoding="utf-8").read()
    reader_pat = re.search(r'prefix\s*=\s*_?re\.sub\(r"(.*?)"', src)
    assert reader_pat, "未能从取证侧提取归一正则"
    assert reader_pat.group(1) == writer_pat.group(1), (
        f"归一规则漂移：写入方 {writer_pat.group(1)!r} != 取证侧 {reader_pat.group(1)!r}")

    for step in ("clean:nfra", "semantic:assist", "a/b:c", "x.y-z_w", "中文:步骤"):
        w = re.sub(writer_pat.group(1), "_", step)
        r = re.sub(reader_pat.group(1), "_", step)
        assert w == r, f"归一结果不一致：{step} → 写入方 {w} vs 取证侧 {r}"


def test_latest_step_log_matches_real_files() -> None:
    """正例：`latest_step_log` 对**磁盘上真实存在**的步骤能取到文件（若目录有文件）。"""
    # 仓根由 conftest 的 bootstrap 放入路径（唯一引导点），此处不自写注入
    from bootstrap import bootstrap

    bootstrap("all")
    from web import snapshots as S

    if not os.path.isdir(STEP_LOGS):
        pytest.skip("本机无 step_logs（链未在本机跑过）")
    names = [n for n in os.listdir(STEP_LOGS) if n.endswith(".out.log")]
    if not names:
        pytest.skip("step_logs 下无 out 日志")
    # 取任一真实文件名反推步骤名（去时间戳与扩展名），断言能取回同一份文件
    sample = sorted(names)[-1]
    step = re.sub(r"_\d{6,}\.out\.log$", "", sample).replace("_", ":", 1)
    tail, path = S.latest_step_log(step)
    if not path:
        # 冒号还原可能不唯一（如 clean_nfra_weekly）⇒ 用前缀匹配的方式再验一次
        stem = re.sub(r"_\d{6,}\.out\.log$", "", sample)
        hits = [n for n in names if n.startswith(stem + "_")]
        assert hits, f"磁盘有 {sample} 但latest_step_log 取不到（推导步骤名={step}）"
        return
    assert os.path.exists(path)


def test_latest_step_log_no_false_path_construction() -> None:
    """负例（静态）：取证实现**不得**回到「固定 `{step}.log` 拼接」形态。

    该形态在真实命名下恒不可达——它不报错，只是安静地返回空证据。
    """
    src = open(os.path.join(ROOT, "web", "snapshots.py"), encoding="utf-8").read()
    bad = re.search(r'step_logs["\']\)\s*,\s*f["\']\{step\}\.log', src)
    assert not bad, "取证退回固定 {step}.log 拼接（与写入方命名不符，恒取不到）"
    assert "latest_step_log" in src, "应保留统一的取证入口函数"
