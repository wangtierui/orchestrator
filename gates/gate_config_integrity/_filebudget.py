"""J11. 单文件行数预算（§13.11；基线只减不增）

背景：方案 §7.8 的 D 类（>1200 行 4→0）是**一次性核对**，没有机器守门；此后
`tools/run_production_refresh.py` 随 W0–W5 持续新增步骤/判据又长到 2300+ 行 ⇒
**规模在无人盯防的情况下回涨**（实测本判据建立时：800 行以上 9 个文件、1200 以上 3 个）。

纪律（与 `gate_import_bootstrap` 的注入数基线同构）：
  · 判定口径 = **超限集合 ⊆ 基线集合**（不逐条比行数——重构导致的行数自然波动不该
    变成噪声；只有「新增超限文件」或「已拆分文件回到超限」才 FAIL）；
  · 基线单调收缩：拆完一个即从 `integrity_policy.yaml` 的 `file_budget.baseline` 删一条；
  · 不要求立刻清零：采集层拆分需真实抓取回归环境（`docs/tech_debt.md` TD-1），
    强拆的风险高于收益；本判据先止住**增长**，清零按债务台账节奏推进。
"""
from __future__ import annotations

import os

LAYERS_DEFAULT = ("std_lib", "modules", "tools", "config", "interfaces", "gates", "commands")
SKIP_DIRS = {"__pycache__", "retired", "external", "graphify-out", "refactor", ".venv"}


def _policy(section: str):
    """读策略段（唯一读取口 config.loader.load_integrity_policy）。"""
    from config.loader import load_integrity_policy

    return (load_integrity_policy() or {}).get(section) or {}


def _count_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _check_file_budget(root: str) -> tuple[list[str], dict]:
    """J11：超限文件集合 ⊆ 策略基线。"""
    problems: list[str] = []
    detail: dict = {}
    pol = _policy("file_budget")
    if not pol:
        return ["J11: 策略缺 file_budget 段（行数预算不可静默缺省）"], detail
    try:
        limit = int(pol.get("max_lines") or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return ["J11: file_budget.max_lines 非法"], detail
    scope = tuple(pol.get("scope") or LAYERS_DEFAULT)
    baseline = {str(x) for x in (pol.get("baseline") or [])}
    over: list[dict] = []
    for base in scope:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, base)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                n = _count_lines(p)
                if n > limit:
                    over.append({"file": os.path.relpath(p, root).replace("\\", "/"),
                                 "lines": n})
    over.sort(key=lambda d: -d["lines"])
    detail["max_lines"] = limit
    detail["over_limit_count"] = len(over)
    detail["baseline_size"] = len(baseline)
    detail["over_limit"] = over[:15]

    new_ones = [d for d in over if d["file"] not in baseline]
    if new_ones:
        problems.append(
            f"J11: 新增超 {limit} 行的文件 {len(new_ones)} 个（基线只减不增）："
            + "；".join(f"{d['file']}({d['lines']})" for d in new_ones[:5])
            + "——请拆分，或在证明必要后显式登记进 integrity_policy.file_budget.baseline")
    stale = sorted(baseline - {d["file"] for d in over})
    if stale:
        detail["stale_baseline"] = stale
        problems.append(
            f"J11: 基线中的 {stale} 已不超限（拆分完成？）——请从 baseline 移除"
            "（基线只减不增，且不得留已偿条目）")
    return problems, detail
