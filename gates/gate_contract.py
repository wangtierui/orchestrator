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

_CLASS_MOD = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
if _CLASS_MOD not in __import__("sys").path:
    __import__("sys").path.insert(0, _CLASS_MOD)
_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
# R16（二期）：主题集合唯一事实源 = rfn.THEME_MAP，期望数量/命名由遍历派生，禁字面 40/11。
from rfn import THEME_MAP  # noqa: E402

_DET_RE = re.compile(r"^(T\d+)_\d+逐份条款引用与上位法依据明细表\.csv$")
# 底座命名：{T1..T10}×{base,final,matched,citerefs}（T0 不生成底座）
_EXPECT_BASE_FILES = sorted(
    f"_t{int(c[1:])}_{suf}.json"
    for c in THEME_MAP if c != "T0"
    for suf in ("base", "final", "matched", "citerefs"))
_BASE_RE = re.compile(r"^_t\d+_(base|final|matched|citerefs)\.json$")
_EXPECT_DETS = len(THEME_MAP)   # 每主题 ≥1 明细（T0–T10 主题码全覆盖）
_EXPECT_BASE = len(_EXPECT_BASE_FILES)


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

    # 3) 明细表（R16：主题码全覆盖，THEME_MAP 遍历派生）× 10 列
    dets = sorted(f for f in os.listdir(_DATA) if _DET_RE.match(f))
    det_codes = {_DET_RE.match(f).group(1) for f in dets}
    missing_codes = set(THEME_MAP) - det_codes
    checked["detail_tables"] = {"count": len(dets), "expected": _EXPECT_DETS,
                                "covered_themes": sorted(det_codes)}
    if missing_codes:
        problems.append(f"明细表主题覆盖缺 {sorted(missing_codes)}（THEME_MAP 驱动）: {dets}")
    for f in dets:
        head = _header(os.path.join(_DATA, f))
        if len(head) != 10:
            problems.append(f"明细表 {f} 列数 {len(head)} ≠ 10: {head}")

    # 4) 数据底座（R16：期望文件名集 = THEME_MAP{T1..T10}×4 派生）：结构类型 + 核心键超集
    bfiles = sorted(f for f in os.listdir(_DATA) if _BASE_RE.match(f))
    extra = sorted(set(bfiles) - set(_EXPECT_BASE_FILES))
    miss = sorted(set(_EXPECT_BASE_FILES) - set(bfiles))
    checked["base_files"] = {"count": len(bfiles), "expected": _EXPECT_BASE}
    if miss:
        problems.append(f"数据底座缺 {len(miss)} 个（THEME_MAP 派生期望）: {miss[:5]}")
    if extra:
        problems.append(f"数据底座多余 {len(extra)} 个: {extra[:5]}")
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
