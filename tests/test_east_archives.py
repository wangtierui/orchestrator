# -*- coding: utf-8 -*-
"""tests/test_east_archives.py — EAST 两份存档的**一致性**守卫（2026-10-26）。

## 背景（用户提问）
> "supp 目录里 金非银检函〔2025〕82号 全部文件 与 data/corpus/regulatory_stats/…
>  20250410 目录内相同，梳理明确仓内文件的两份存档关系，是否存在冗余？"

## 实测结论
* `20250410/` 下 **13 个文件与采集目录逐字节相同**，合计 **14.68MB**；
* `20250410/` **没有**采集目录之外的文件（`corpus_only = 0`）；
* 但整个 `data/corpus/regulatory_stats/…` 目录另有 **161 个 B 独有**材料
  （官方 zip 原件、征求意见稿、后续通报）—— 那部分是采集拿不到的，必须留。

## 纪律
**程序只读采集目录**（`interfaces.attachment_api` 单一入口），本目录仅供人工追溯。
冗余允许存在，但**不允许悄悄漂移** —— 本守卫逐对比 sha256：
有人只更新了一边（比如手工替换了归档里的附件1-3），测试立刻红。
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

pytestmark = pytest.mark.contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "data", "corpus", "regulatory_stats",
                      "00.关于修订人身保险公司2024版监管数据标准化规范部分数据项报送口径的通知")
MANIFEST = os.path.join(CORPUS, "_archive_manifest.json")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_manifest_exists() -> None:
    assert os.path.exists(MANIFEST), (
        "缺 _archive_manifest.json（运行生成脚本重录：见 README 中的纪律段）")


def test_no_drift_between_two_archives() -> None:
    """逐对比 sha256：两份存档必须仍然逐字节一致（防"只更新一边"）。"""
    m = json.load(open(MANIFEST, encoding="utf-8"))
    pairs = m.get("identical_pairs") or []
    assert pairs, "manifest 里没有 identical_pairs（录错了？）"
    drift = []
    for p in pairs:
        a = os.path.join(ROOT, m["supp_dir"], p["supp_name"])
        b = os.path.join(ROOT, m["corpus_dir"], "20250410", p["corpus_name"])
        if not os.path.exists(a):
            drift.append(f"采集侧缺失：{p['supp_name']}")
        elif not os.path.exists(b):
            drift.append(f"归档侧缺失：{p['corpus_name']}")
        elif _sha(a) != p["sha256"] or _sha(b) != p["sha256"]:
            drift.append(f"内容已漂移：{p['corpus_name']}")
    assert not drift, drift[:5]


def _code_only(src: str) -> str:
    """去掉注释与 docstring 后再看路径（文档里提到归档目录是**说明**，不是读它）。"""
    import ast

    out = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    try:
        tree = ast.parse(src)
    except SyntaxError:       # pragma: no cover
        return out
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):      # `IfExp.body` 等不是语句列表
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant):
            doc = body[0].value.value or ""
            if doc:
                out = out.replace(doc, "")
    return out


def test_program_reads_supp_only() -> None:
    """程序侧单一入口：`attachment_api` 指向采集目录，**代码里不出现** corpus 路径。"""
    src = open(os.path.join(ROOT, "interfaces", "attachment_api.py"),
               encoding="utf-8").read()
    assert "supplementary_regulations.jsonl" in src
    assert "regulatory_stats" not in _code_only(src), "附件读取口不应指向人工归档目录"

    builder = open(os.path.join(ROOT, "tools", "build_east_structure.py"),
                   encoding="utf-8").read()
    assert "attachment_api" in builder
    assert "regulatory_stats" not in _code_only(builder), "构建器不应再 glob 归档目录"


def test_corpus_keeps_evidence_absent_from_supp() -> None:
    """归档目录的价值在于"采集拿不到的东西"——zip/征求意见稿/通报必须仍在。"""
    m = json.load(open(MANIFEST, encoding="utf-8"))
    dirs = set()
    for name in os.listdir(CORPUS):
        p = os.path.join(CORPUS, name)
        if os.path.isdir(p):
            dirs.add(name)
    assert "0通知文件" in dirs, dirs
    assert any(d.startswith("2024") for d in dirs), dirs
    assert m["stats"]["identical"] >= 10
