# -*- coding: utf-8 -*-
"""
gates/gate_contract — 数据契约门禁（实装：归属表 8 列 / 主题表 3 列 / 明细契约列 / 底座 40 键集）

数据契约唯一事实 = interfaces/contract.py（程序可读契约）。gate 读 modules 数据与契约比对，
超集判定（允许下游加字段，缺必报）。纯读校验，不写回。
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import re

import paths
from interfaces import contract
from interfaces.theme_api import theme_map as _theme_map

_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
# R16（二期）：主题集合唯一事实源 = rfn.THEME_MAP，期望数量/命名由遍历派生，禁字面 40/11。
# v2 §3.1.3 I-4（2026-09-26）：原为治理层**直接依赖模块内部实现**
# （自行 sys.path 注入 classifier 目录 + `from rfn import THEME_MAP`），现改经
# `interfaces.theme_api`（协议见 `interfaces/protocols.RfnProvider`）——
# 治理层由此不再持有任何 modules 内部导入，本文件也不再需要 sys.path 注入。
THEME_MAP = _theme_map()

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


_MANIFEST = os.path.join(paths.CONFIG_DIR, "schema", "contract_manifest.json")


def _dotted(rel: str) -> str:
    """仓库相对 .py 路径 → 可导入的点号路径（`a/b/__init__.py` → `a.b`）。"""
    p = rel[:-3] if rel.endswith(".py") else rel
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def _manifest_checks() -> tuple[list[str], dict]:
    """M1 修复（v2 §3.4 / P1-4）：让契约清单**有消费方**——file/symbol/count 三元断言。

    背景：`config/schema/contract_manifest.json` 改造前**全仓 0 处代码读取**（仅文档与
    一处注释提及），因此静默漂移两处：`detail_table_fields.count=10`（实为 14）、
    `base_json_keys.file` 指向**不存在**的 `theme_analysis/node2_rebuild/`。
    本判据逐条断言：① `file` 存在；② `symbol`（或 `symbols` 映射）可经点号导入取得；
    ③ `len(符号) == count`。清单新增 `version` 顶层字段以便未来做格式演进判别。
    """
    problems: list[str] = []
    detail: dict = {"checked": {}, "missing": []}
    root = paths.ROOT

    if not os.path.exists(_MANIFEST):
        return [f"契约清单不存在：{_MANIFEST}"], detail
    try:
        man = json.load(open(_MANIFEST, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"契约清单不可解析：{type(e).__name__}: {e}"], detail

    detail["version"] = man.get("version", "")
    if not str(man.get("version") or "").strip():
        problems.append("契约清单缺顶层 version（v2 §3.4：加 version 以便格式演进判别）")

    for name, spec in sorted((man.get("contracts") or {}).items()):
        rel = str(spec.get("file") or "")
        fp = os.path.join(root, rel.replace("/", os.sep))
        if not rel:
            problems.append(f"{name}: 缺 file 字段")
            continue
        if not os.path.exists(fp):
            # 目录型/文件型统一判定：两者都必须存在（历史漂移即此判据抓出）
            problems.append(f"{name}: file 不存在 {rel}")
            detail["missing"].append(rel)
            continue

        pairs: list[tuple[str, int]] = []
        if spec.get("symbol") and spec.get("count") is not None:
            pairs.append((str(spec["symbol"]), int(spec["count"])))
        for sym, cnt in (spec.get("symbols") or {}).items():
            pairs.append((str(sym), int(cnt)))
        if not pairs:
            detail["checked"][name] = {"file": rel, "symbols": "（无 symbol/count，仅登记定位）"}
            continue

        if not rel.endswith(".py"):
            problems.append(f"{name}: 登记了 symbol 但 file 非 .py（{rel}）")
            continue
        mod_name = _dotted(rel)
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001  加载失败本身即清单漂移信号
            problems.append(f"{name}: 无法导入 {mod_name}（{type(e).__name__}: {e}）")
            continue
        got: dict = {}
        for sym, cnt in pairs:
            obj = getattr(mod, sym, None)
            if obj is None:
                problems.append(f"{name}: {mod_name} 无符号 {sym}")
                continue
            actual = len(obj)
            got[sym] = {"count": actual, "declared": cnt, "shape": type(obj).__name__}
            if actual != cnt:
                problems.append(
                    f"{name}: {mod_name}::{sym} 实际 {actual} ≠ 清单声明 {cnt}"
                    f"（清单漂移，须同步）")
        detail["checked"][name] = {"file": rel, "module": mod_name, **got}

    return problems, detail


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

    # 0) 契约清单自洽（M1 修复 / P1-4）：与数据面无关，先跑——清单漂移必须独立可报
    _mp, _mdet = _manifest_checks()
    problems += _mp
    checked["manifest"] = _mdet

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

    # 3) 明细表（R16：主题码全覆盖，THEME_MAP 遍历派生）× DETAIL_TABLE_FIELDS 契约列
    dets = sorted(f for f in os.listdir(_DATA) if _DET_RE.match(f))
    det_codes = {_DET_RE.match(f).group(1) for f in dets}
    missing_codes = set(THEME_MAP) - det_codes
    checked["detail_tables"] = {"count": len(dets), "expected": _EXPECT_DETS,
                                "covered_themes": sorted(det_codes),
                                "cols": len(contract.DETAIL_TABLE_FIELDS)}
    if missing_codes:
        problems.append(f"明细表主题覆盖缺 {sorted(missing_codes)}（THEME_MAP 驱动）: {dets}")
    for f in dets:
        head = _header(os.path.join(_DATA, f))
        if head != contract.DETAIL_TABLE_FIELDS:
            problems.append(f"明细表 {f} 列头 ≠ 契约({len(contract.DETAIL_TABLE_FIELDS)}列): {head}")

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
