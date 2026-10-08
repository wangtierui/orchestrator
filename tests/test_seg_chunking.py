# -*- coding: utf-8 -*-
"""tests.test_seg_chunking — **分块分词**与"长文本失真"守卫（N-187）。

背景（2026-10-08 实测）：
  · **LTP 4.x 静默失真**：输入 > ~550 字时只处理前 ~276 token，**其余整段被当成单个 token**
    （400 字 → 201 token/最长 7 字；600 字 → 276 token/最长 68 字；2000 字 → **仍 276 token、
    最长 1468 字**）⇒ 下游"以分词为锚切句"对长法规（中位 ~2.6K 字）**~85% 内容不切句**
    （3417 字正文只切出 2 句，确定性切分 47 句）。
  · **HanLP MTL 爆炸**：`truncate_long_sequences: false` ⇒ 13.8K 字单条 **>34 分钟未完成**。

修法：调用侧**分块**（`_chunk_text`，边界优先落句末标点）+ 两后端各头**共用同一分块**。

本测试**不加载真模型**（CI 快速稳定）：
  ① `_chunk_text` 的**纯函数**性质：不丢字符、块长受限、优先句末断开、超长无标点串硬切；
  ② **短文本不分块**（≤ 阈值 → 单块）⇒ 既有行为逐字节不变（回归保护）；
  ③ **真机守卫**（有模型时才跑）：长文本分词不得出现"超长 token"（这正是 LTP 截断的指纹）。
"""
from __future__ import annotations

import pytest

from std_lib.common_lib import semantic_models as sm


def _mk_text(n: int, seg: int = 40) -> str:
    """构造 n 字的正文（每 seg 字一句，句末标点）。"""
    unit = "为规范市场秩序制定本办法" * 3 + "。"  # 13*3+1 = 40 字
    return (unit * (n // len(unit) + 1))[:n]


def test_chunk_text_is_lossless_and_bounded() -> None:
    """① 不丢字符 + 每块 ≤ 阈值 + 块数正确。"""
    t = _mk_text(2600)
    chunks = sm._chunk_text(t)
    assert "".join(chunks) == t, "分块必须**不丢字符**（拼接还原为原文）"
    assert all(len(c) <= sm._SEG_CHUNK_CHARS for c in chunks), "每块不得超过阈值"
    assert len(chunks) >= 5, f"2600 字 / {sm._SEG_CHUNK_CHARS} 字 → 应有多块，实际 {len(chunks)}"


def test_chunk_text_prefers_sentence_boundary() -> None:
    """② 块边界**优先落句末标点**（除非单段本身超长）。"""
    t = _mk_text(1500)
    chunks = sm._chunk_text(t)
    for c in chunks[:-1]:  # 末块可为尾段，不做要求
        assert c[-1] in "。；！？\n", f"块边界应落在句末标点，实际 {c[-1]!r}"


def test_chunk_text_hard_splits_unpunctuated() -> None:
    """③ 无标点长串 → 硬切（仍不丢字符）。"""
    t = "甲" * 1300
    chunks = sm._chunk_text(t)
    assert "".join(chunks) == t
    assert all(len(c) <= sm._SEG_CHUNK_CHARS for c in chunks)
    assert len(chunks) == 3


def test_short_text_is_single_chunk() -> None:
    """④ ≤ 阈值 → **单块**（⇒ 与既有单次调用行为完全一致，零回归）。"""
    t = _mk_text(400)
    assert sm._chunk_text(t) == [t]
    assert sm._chunk_text("") == [""]


def test_chunk_size_matches_measured_limit() -> None:
    """⑤ 阈值须**小于实测的 LTP 失真起点**（~550 字），否则守卫失效。"""
    assert sm._SEG_CHUNK_CHARS <= 500, "阈值必须低于 LTP 的 ~550 字失真起点"


def test_real_long_text_has_no_giant_token() -> None:
    """⑥ **真机守卫（ltp）**：长文本分词不得出现超长 token（＝截断失真的指纹）。

    成本控制：用 3 块（约 1500 字）而非全量样本；LTP 分词本身很快（实测 ~2.6s/条）。
    超长 token 是"截断后把剩余整段当成一个词"的指纹（LTP 实测最长 **1468 字**），阈值取 80 字。
    """
    try:
        seg = sm.load_segmenter("ltp")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ltp 不可用：{type(e).__name__}: {e}")
    body = _mk_text(1500)
    toks = seg.cws([body])[0]
    longest = max((len(t) for t in toks), default=0)
    assert longest <= 80, (
        f"ltp 在 1500 字正文上出现超长 token（{longest} 字）→ 疑似长度上限失真（分块未生效）"
    )
    total = sum(len(t) for t in toks)
    assert total >= len(body) * 0.9, f"token 覆盖字符数 {total} 远小于正文 {len(body)} → 有丢失"


def test_real_long_text_hanlp_opt_in() -> None:
    """⑥b **真机守卫（hanlp，仅显式开启）**：HanLP MTL 单条 30~60s ⇒ 默认不跑（不拖累 CI 预算）。

    置 `REG_ORCH_TEST_HANLP=1` 后手动跑（如发布前）：
        $env:REG_ORCH_TEST_HANLP=1; python -m pytest tests/test_seg_chunking.py -q
    """
    import os

    if os.environ.get("REG_ORCH_TEST_HANLP") != "1":
        pytest.skip("HanLP MTL 单条 30~60s（默认不跑；置 REG_ORCH_TEST_HANLP=1 启用）")
    try:
        seg = sm.load_segmenter("hanlp")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"hanlp 不可用：{type(e).__name__}: {e}")
    body = _mk_text(1200)
    toks = seg.cws([body])[0]
    longest = max((len(t) for t in toks), default=0)
    assert longest <= 80, f"hanlp 在 1200 字正文上出现超长 token（{longest} 字）"
    assert sum(len(t) for t in toks) >= len(body) * 0.9
