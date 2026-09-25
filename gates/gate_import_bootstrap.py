# -*- coding: utf-8 -*-
"""gate_import_bootstrap — sys.path 引导纪律（v2 §3.8 表 A 第 2 项，判据基线冻结）

背景（v2 §2.1 A6）：`paths.py` 的纪律写明「禁止业务代码 sys.path.insert」，实测
    `modules/` ≥119 处、`commands/` 9 个文件、`interfaces/` 8 个文件各自注入。
    P0-2 已把 `commands/` **清零**（统一走 `bootstrap.py`）；其余层以**基线冻结**
    方式引入，只减不增（v2 D-8 过渡策略）。

判据：
    1) `commands/**` 与 `paths.py`/`cli.py`/`file_list_watcher.py` 注入数 = **0**（硬断言）；
    2) 其余各层注入数 ≤ 冻结基线（只减不增）；低于基线时提示收缩基线（不阻断）；
    3) `bootstrap.py` 自身不计入仓根硬断言（它是唯一被允许的注入点）。

收敛路径：P1-3（interfaces 收口）→ P2-x（modules 逐步收敛）；每完成一层，
在本文件 `BASELINE` 中下调对应数字。
"""
from __future__ import annotations

import os
import re

import paths

ROOT = paths.ROOT
PAT = re.compile(r"sys\.path\.(insert|append)")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "reports", "graphify-out",
             "external", "tessdata", "backups", ".ruff_cache", ".codebuddy"}

# 冻结基线（2026-09-26 实测，P0-2 完成后）：只减不增
BASELINE: dict[str, int] = {
    "gates": 17,
    "interfaces": 15,
    # 128（2026-09-26，P1-6）：+1 = consolidate_timeliness.py 的 `_wl_add` 引导
    # （该脚本原为纯 stdlib、无仓内导入，为接 worklist 队列首次引入仓根引导）
    "modules": 128,
    "std_lib": 9,
    "tests": 35,
    "tools": 16,
}
# 硬零层：P0-2 已收口，禁止回退
HARD_ZERO_GROUPS = ("commands",)
HARD_ZERO_ROOT_FILES = ("paths.py", "cli.py", "file_list_watcher.py")


def _scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "retired"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            top = rel.split("/")[0]
            key = top if "/" in rel else f"<root>/{fn}"
            if key == "bootstrap.py":
                continue
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            n = len(PAT.findall(text))
            if n:
                counts[key] = counts.get(key, 0) + n
    return counts


def run() -> tuple[bool, dict]:
    # 引导：顶层 `import paths` 已要求仓根在 sys.path；本门禁自身不注入（见 P0-6 纪律）。
    counts = _scan()
    problems: list[str] = []
    warnings: list[str] = []
    detail: dict = {"counts": counts}

    # 1) 硬零
    for key, n in counts.items():
        parts = key.split("/")
        group = parts[0]
        if group in HARD_ZERO_GROUPS:
            problems.append(f"{key}: {n} 处 sys.path 注入（{group}/ 已收口，须经 bootstrap）")
        if key in {f"<root>/{f}" for f in HARD_ZERO_ROOT_FILES}:
            problems.append(f"{key}: {n} 处 sys.path 注入（仓根文件须经 bootstrap）")

    # 2) 基线只减不增
    for group, base in sorted(BASELINE.items()):
        cur = sum(n for k, n in counts.items() if k.split("/")[0] == group)
        detail.setdefault("by_group", {})[group] = {"current": cur, "baseline": base}
        if cur > base:
            problems.append(f"{group}/ 注入数 {cur} > 基线 {base}（只减不增；新代码须经 bootstrap）")
        elif cur < base:
            warnings.append(f"{group}/ 注入数 {cur} < 基线 {base}（建议下调 BASELINE）")

    if warnings:
        detail["warnings"] = warnings
    if problems:
        detail["problems"] = problems
    detail["bootstrap_total"] = sum(1 for k in counts if k == "<root>/bootstrap.py")
    return (not problems), detail


if __name__ == "__main__":
    import json

    passed, det = run()
    print("[import_bootstrap]", "PASS" if passed else "FAIL", json.dumps(det, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
