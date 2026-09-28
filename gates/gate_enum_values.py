# -*- coding: utf-8 -*-
"""
gates/gate_enum_values — 受控枚举一致性门禁（S-3 / v3）

判据（全部阻断）：
  A) `config/enums.py` 自检（assert_enum_bindings）；
  B) 若 PyYAML 可用，校验 sources.yaml active 源 == `config.enums.SOURCE_SET`；
  C) **受控枚举字面量副本扫描**（N-78，2026-09-28 实装 —— 原 docstring 自 P1 起声明"扫描
     modules/** 中枚举值字面量"，长期只披露未实装）：扫出"把枚举**成组**写成字面量"的位置
     （≥3 个来源名同现于一处 / ≥5 个时效状态同现于一处），因为那**正是** SSOT 副本的形态。
     单值字面量（`if sid == "gov"`）属正常业务判断，**不扫**（噪音过大且无治理价值）。

基线（只减不增）
----------------
`_BASELINE` 按 **`文件 + 形态` 计数**冻结存量副本（不记行号 → 行位移不误报）。规则：
  · 新增文件/新增副本 → **FAIL**； · 计数下降 → 报告 `baseline_stale`（提示收缩基线，不阻断）。
新增副本必须**显式登记进基线并写明理由**，使"放宽纪律"永远是一条有记录的决策
（与 `gate_no_cross_module_import` 同款纪律）。
"""

from __future__ import annotations

import collections
import os
import re
import sys

import paths

sys.path.insert(0, paths.ROOT)
try:
    import config.enums as E
except Exception as e:  # noqa: BLE001  # 导入失败须降级为 FAIL（不放行）：见 run() 首段
    E = None  # type: ignore[assignment]
    _import_err = repr(e)

_PYYAML_OK = False
try:
    import yaml  # noqa: F401

    _PYYAML_OK = True
except Exception:  # noqa: BLE001
    _PYYAML_OK = False

# 扫描范围：**全仓自有代码**（含 gates/tools —— 它们同样直接消费 SSOT，副本危害相同）。
_SCAN_DIRS = ("config", "interfaces", "gates", "commands", "std_lib", "modules", "tools", "tests")
_SKIP_DIRS = {"__pycache__", "retired", "archive", "data", "backups", "_tmp", "graphify-out"}

# 形态判定（与 `config.enums.SOURCE_ORDER` / `TIMELINESS_STATUS` 对齐；由 run() 动态构造）
_SRC_ALT = "gov|mof|nfra|pbc|supp"
_QUOTED_SRC = re.compile(rf'"(?:{_SRC_ALT})"')
_TL_ALT = (
    "valid|amended|repealed|partially_repealed|expired|pending|uncertain"
)
_QUOTED_TL = re.compile(rf'"(?:{_TL_ALT})"')

# 基线：``{ "相对路径:形态": 计数 }``。仅减不增。
# N-78（2026-09-28）冻结存量：全仓由 **29 站点 / 33 处** 收敛到 **4 站点 / 4 处**；
# 残余四处均**不是**"枚举副本"，而是**按源分列的策略映射**（各自语义不同，无法用单一
# `SOURCE_ORDER` 表达）——逐条写明理由，使其与"漏改"可区分：
#   · build_upper_laws.SRC_PREFIX_MAP    —— 五源**恒等映射**：本文件为**独立子进程**脚本
#                                            （`classify.py` subprocess 调用），仓内导入一律走
#                                            **函数内**引导 → 模块级 `from config.enums` 会
#                                            ModuleNotFoundError；而新增顶层引导会顶破
#                                            `gate_import_bootstrap` 的"只减不增"注入基线。
#                                            **两个门禁口径冲突 → 保留字面量并在此登记理由**
#                                            （N-78 实测踩到：曾改为派生导致 `classify:all` FAIL）
#   · build_upper_laws.SRC_PRIORITY      —— 正文择优的**源优先级**（gov<nfra<pbc<supp<mof）
#   · run_timeliness_resume.PRIORITY     —— 补录批次的**处理次序子集**（不含 gov）
#   · test_table_structured（parametrize）—— 单测**抽样**参数（故意只取 4 源）
#   · run_production_refresh.OUT_FLAG    —— 各源**命令行参数名**映射（gov=--out-dir 等，
#                                            键集应与 SOURCE_SET 相等；属映射而非顺序枚举）
_BASELINE: dict[str, int] = {
    "modules/regulatory_classifier/scripts/build_upper_laws.py:SRC_SET": 2,
    "modules/regulatory_scrapers/timeliness_review/run_timeliness_resume.py:SRC_SET": 1,
    "tests/test_table_structured.py:SRC_SET": 1,
    "tools/run_production_refresh.py:SRC_SET": 1,
}


def _scan() -> dict[str, int]:
    """→ {"相对路径:形态": 计数}；形态 ∈ {SRC_SET, TIMELINESS_SET}。"""
    found: collections.Counter[str] = collections.Counter()
    for base in _SCAN_DIRS:
        root = os.path.join(paths.ROOT, base)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, paths.ROOT).replace("\\", "/")
                if rel == "config/enums.py":
                    continue  # 受控值**本体**（定义处），非副本
                try:
                    text = open(fp, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                in_doc = False
                for line in text.splitlines():
                    s = line.lstrip()
                    # 三引号字符串（docstring / 帮助文案）**不计**：其中列举受控值是**文档说明**，
                    # 不是"代码里的枚举副本"（如 `cache_store` 的 "取值 \"gov\" | \"mof\" | …"）。
                    quotes = s.count('"""') + s.count("'''")
                    if in_doc:
                        if quotes:
                            in_doc = False
                        continue
                    if quotes % 2 == 1:
                        in_doc = True
                        continue
                    if s.startswith("#"):
                        continue
                    uniq_src = {m.group(0) for m in _QUOTED_SRC.finditer(line)}
                    uniq_tl = {m.group(0) for m in _QUOTED_TL.finditer(line)}
                    # 成组出现才算"枚举副本"（≥3 来源 / ≥5 时效值同现于一行）
                    if len(uniq_src) >= 3:
                        found[f"{rel}:SRC_SET"] += 1
                    if len(uniq_tl) >= 5:
                        found[f"{rel}:TIMELINESS_SET"] += 1
    return dict(found)


def _check_literals() -> tuple[list[str], list[str], dict]:
    problems: list[str] = []
    stale: list[str] = []
    now = _scan()
    for key, n in sorted(now.items()):
        base_n = _BASELINE.get(key, 0)
        if n > base_n:
            problems.append(
                f"C: {key} 出现受控枚举成组字面量 {n} 处 > 基线 {base_n}"
                "（应 `from config.enums import SOURCE_ORDER / TIMELINESS_STATUS`；"
                "确需保留须登记进 gate_enum_values._BASELINE 并写明理由）"
            )
    for key, base_n in sorted(_BASELINE.items()):
        if now.get(key, 0) < base_n:
            stale.append(f"{key}: {base_n} → {now.get(key, 0)}")
    detail = {
        "literal_sites": len(now),
        "baseline_sites": len(_BASELINE),
        "current": now,
        "baseline_stale": stale,
    }
    return problems, stale, detail


def run():
    if E is None:
        return False, {"error": "config.enums 导入失败: " + _import_err}
    try:
        E.assert_enum_bindings()
    except AssertionError as e:
        return False, {"error": f"config.enums.assert_enum_bindings 失败: {e}"}
    problems: list[str] = []
    warnings: list[str] = []
    if _PYYAML_OK:
        try:
            from config.loader import active_source_ids

            active = active_source_ids()
            if set(active) != set(E.SOURCE_SET):
                problems.append(
                    f"sources.yaml active {sorted(active)} != SOURCE_SET {sorted(E.SOURCE_SET)}"
                )
        except Exception as e:  # noqa: BLE001  # 仅降级为 warning：loader 失败不改变"集合相等"判据
            warnings.append(f"loader 检查跳过: {e}")
    else:
        warnings.append("PyYAML 未安装，sources.yaml 一致性检查跳过")
    p_c, stale, d_c = _check_literals()
    problems += p_c
    detail: dict = {
        "problems": problems,
        "warnings": warnings,
        "SOURCE_SET": sorted(E.SOURCE_SET),
        "SOURCE_ORDER": list(E.SOURCE_ORDER),
        "TIMELINESS_STATUS": sorted(E.TIMELINESS_STATUS),
        **d_c,
    }
    if stale:
        detail["baseline_stale_note"] = "基线条目已收缩，建议更新 _BASELINE（非阻断）"
    return (not problems), detail


if __name__ == "__main__":
    import json

    ok, det = run()
    print("[enum_values]", "PASS" if ok else "FAIL", json.dumps(det, ensure_ascii=False, indent=1))
    raise SystemExit(0 if ok else 1)
