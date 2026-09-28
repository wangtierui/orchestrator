# -*- coding: utf-8 -*-
"""std_lib.common_lib.artifact_shape — 交付物**形状**校验（共享纯函数）

N-82（2026-09-28）：消除**双套实现**。
--------------------------------------------------------------------------
`recall_audit/run_retrieval_after_checks.py`（自检脚本的 Gate 4）与
`gates/gate_contract.py`（第 17 道门禁）原本各自持有一套**逐行等价**的产物形状校验：

| 能力 | recall（原） | gate_contract（原） |
|---|---|---|
| CSV 列头 | `_csv_header` | `_header` |
| JSON 顶层结构 + 键集 | `_json_shape` | `_json_shape` |
| 底座命名正则 | `_BASE_RE` | `_BASE_RE` |
| 明细表数 | **硬编码 `≠ 11`** | `_EXPECT_DETS = len(THEME_MAP)`（派生） |
| 底座总数 | **硬编码 `≠ 40`** | `_EXPECT_BASE = len(_EXPECT_BASE_FILES)`（派生） |
| 每后缀基数 | **硬编码 `每类 10 个`** | 同上（派生） |

→ 一旦新增主题 T11，门禁侧**自动跟随**，而 recall 侧私有副本会**静默分叉**
（报出"明细表 12 ≠ 11"这类**假阳性**，且无人知道该改哪里）。本模块把共同纯函数与
**派生后的期望值**收为唯一实现，两处一律 import 本模块。

依赖方向（不制造反向依赖）：`gates/` 与 `modules/` 同为**消费者**；期望值唯一来源是
`interfaces.theme_api`（→ `rfn.THEME_MAP`）。本模块**不** import 任何 gates/modules。
"""

from __future__ import annotations

import csv
import json
import os
import re

from interfaces.theme_api import theme_map as _theme_map

# 数据底座：{T1..T10} × {base,final,matched,citerefs}（T0 不生成底座）
BASE_SUFFIXES: tuple[str, ...] = ("base", "final", "matched", "citerefs")
BASE_SUFFIX_SHAPE: dict[str, str] = {
    "base": "list",
    "final": "list",
    "matched": "dict",
    "citerefs": "dict",
}

# 底座文件名（`_t{n}_{suf}.json`）；`_t0_*` 不在期望集内
BASE_RE = re.compile(r"^_t\d+_(base|final|matched|citerefs)\.json$")
DET_RE = re.compile(r"^(T\d+)_\d+逐份条款引用与上位法依据明细表\.csv$")


def theme_codes() -> dict[str, str]:
    """主题码 → 全名（唯一来源 `interfaces.theme_api`）。"""
    return dict(_theme_map())


def expect_base_files() -> list[str]:
    """数据底座**文件名全集**（T1–T10 × 4 类 = 40；T0 不生成底座）。"""
    return sorted(
        f"_t{int(c[1:])}_{suf}.json"
        for c in _theme_map()
        if c != "T0"
        for suf in BASE_SUFFIXES
    )


def expect_base_count() -> int:
    """底座总数（= 主题数 − 1 × 4）。"""
    return len(expect_base_files())


def expect_base_per_suffix() -> int:
    """每个后缀（base/final/matched/citerefs）应有的底座数（= 非 T0 主题数）。"""
    return len(_theme_map()) - 1


def expect_detail_table_count() -> int:
    """明细表应有份数（= 主题总数，每主题 ≥1；T0–T10）。"""
    return len(_theme_map())


def read_csv_header(path: str) -> list[str]:
    """CSV 首行（`utf-8-sig`，兼容 BOM）。"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def json_shape(path: str) -> tuple[str, set[str]]:
    """→ (顶层结构名, 首元素键集)。

    兼容 list（`base`/`final`：元素为记录 dict）与 dict（`matched`/`citerefs`：
    RFN/键 → 记录 dict）；空容器返回空键集，非容器返回其类型名。
    """
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, list):
        return "list", (set(d[0].keys()) if d else set())
    if isinstance(d, dict):
        v0 = next(iter(d.values()), None)
        return "dict", (set(v0.keys()) if isinstance(v0, dict) else set())
    return type(d).__name__, set()


def dotted(rel: str) -> str:
    """仓库相对 .py 路径 → 可导入点号路径（`a/b/__init__.py` → `a.b`）。"""
    p = rel[:-3] if rel.endswith(".py") else rel
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def base_suffix_of(name: str) -> str:
    """底座文件名 → 后缀（非底座返回 ""）。"""
    m = BASE_RE.match(name)
    return m.group(1) if m else ""


def is_base_file(name: str) -> bool:
    return bool(BASE_RE.match(name)) and os.path.splitext(name)[1] == ".json"
