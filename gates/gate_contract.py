# -*- coding: utf-8 -*-
"""
gates/gate_contract — 数据契约门禁（实装：归属表 8 列 / 主题表 3 列 / 明细 11 份×10 列 / 底座 40 键集）

数据契约唯一事实 = interfaces/contract.py（程序可读契约）。gate 读 modules 数据与契约比对，
超集判定（允许下游加字段，缺必报）。纯读校验，不写回。
"""
from __future__ import annotations

import csv
import json
import os
import re

import paths
from interfaces import contract

_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
_BASE_RE = re.compile(r"^_t\d+_(base|final|matched|citerefs)\.json$")
_DET_RE = re.compile(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$")
_EXPECT_BASE = 40  # T1–T10 × base/final/matched/citerefs（T0 不生成底座）
_EXPECT_DETS = 11  # T0–T10


def _header(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def _json_shape(path):
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, list):
        return "list", (set(d[0].keys()) if d else set())
    if isinstance(d, dict):
        v0 = next(iter(d.values()), None)
        return "dict", (set(v0.keys()) if isinstance(v0, dict) else set())
    return type(d).__name__, set()


def run():
    problems, checked = [], {}

    # 1) 归属表 8 列
    attr = os.path.join(_DATA, "人身保险公司-文件归属表.csv")
    head = _header(attr)
    ok = head == contract.REGISTRY_CSV_FIELDS
    checked["attr_csv"] = {"ok": ok, "cols": len(head)}
    if not ok:
        problems.append(f"归属表列头 {head} ≠ 契约 {contract.REGISTRY_CSV_FIELDS}")

    # 2) 主题归属表 3 列
    theme = os.path.join(_DATA, "人身保险公司-主题归属表.csv")
    head = _header(theme)
    ok = head == contract.THEME_FIELDS
    checked["theme_csv"] = {"ok": ok, "cols": len(head)}
    if not ok:
        problems.append(f"主题归属表列头 {head} ≠ 契约 {contract.THEME_FIELDS}")

    # 3) 明细表 11 份 × 10 列（列头 == build_detail_tables.FIELDS；此处以文件实际 10 列并比对契约列数）
    dets = sorted(f for f in os.listdir(_DATA) if _DET_RE.match(f))
    checked["detail_tables"] = {"count": len(dets), "expected": _EXPECT_DETS}
    if len(dets) != _EXPECT_DETS:
        problems.append(f"明细表数量 {len(dets)} ≠ {_EXPECT_DETS}: {dets}")
    for f in dets:
        head = _header(os.path.join(_DATA, f))
        if len(head) != 10:
            problems.append(f"明细表 {f} 列数 {len(head)} ≠ 10: {head}")

    # 4) 数据底座 40 个：结构类型 + 核心键超集
    bfiles = sorted(f for f in os.listdir(_DATA) if _BASE_RE.match(f))
    checked["base_files"] = {"count": len(bfiles), "expected": _EXPECT_BASE}
    if len(bfiles) != _EXPECT_BASE:
        problems.append(f"数据底座数量 {len(bfiles)} ≠ {_EXPECT_BASE}")
    expect = {"base": contract.BASE_KEYS, "final": contract.FINAL_KEYS,
              "matched": contract.MATCHED_KEYS, "citerefs": contract.CITEREFS_KEYS}
    shape = {"base": "list", "final": "list", "matched": "dict", "citerefs": "dict"}
    for f in bfiles:
        suf = _BASE_RE.match(f).group(1)
        try:
            st, keys = _json_shape(os.path.join(_DATA, f))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{f} 解析失败: {e!r}")
            continue
        if st != shape[suf]:
            problems.append(f"{f} 顶层结构 {st} ≠ 期望 {shape[suf]}")
        missing = expect[suf] - keys
        if missing:
            problems.append(f"{f} 缺核心键: {sorted(missing)}")

    return (not problems), {"problems": problems[:40], "checked": checked}
