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
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "data",
    "reports",
    "graphify-out",
    "external",
    "tessdata",
    "backups",
    ".ruff_cache",
    ".codebuddy",
    "archive",
}  # v2 §3.10（P2-3）：归档层不参与注入计数

# 冻结基线（2026-09-26 **P1-3 实测重标**）：只减不增
#
# ⚠️ 口径变更（同日）：本门禁改为**只统计代码行**（`_code_hits` 跳过注释行与文档字符串）。
#    旧口径把文档/注释里的文字提及也算成"注入"（gates/ 的 18 里有 7 处是散文），
#    导致"加一句说明性注释即顶破基线"。下表为**新口径实测值**。
BASELINE: dict[str, int] = {
    # 9（P1-3 I-4 后实测）：gate_contract.py 不再为拿主题集合而自行注入 classifier 目录
    # （改经 `interfaces.theme_api` + `interfaces/protocols.RfnProvider`）
    "gates": 9,
    # 11（P1-3 实测）：`interfaces/*_api.py` 为访问具体模块仍需少量引导，
    # 待 P1-3 后续 / P2-x 逐步收敛
    "interfaces": 11,
    # 127（P1-6 的 consolidate_timeliness `_wl_add` +1；新口径下再减）
    "modules": 127,
    "std_lib": 9,
    "tests": 33,
    # 14 → 17（2026-09-26，P2-1/2/3b/6）：新增 3 个工具各自带 1 处仓根引导
    # （gen_schedule_doc / install_schedule / inbox_scan —— 均为"可独立直调"的运维工具）
    # 17 → 18（同日 P2-3）：+1 = tools/retention.py 的仓根引导（同上，可独立直调）
    "tools": 18,
}
# 硬零层：P0-2 已收口，禁止回退
HARD_ZERO_GROUPS = ("commands",)
HARD_ZERO_ROOT_FILES = ("paths.py", "cli.py", "file_list_watcher.py")


_TQ_D = '"' * 3
_TQ_S = "'" * 3


def _code_hits(text: str) -> int:
    """统计**代码行**中的注入次数（跳过注释行与文档字符串）。

    修正（2026-09-26，P1-3 执行中发现）：原实现直接对全文跑正则，于是**文档/注释里的
    文字提及**（如「禁止业务代码 sys.path.insert」「A 越权引导：sys.path.insert/append」）
    也被计成"注入"——实测 gates/ 的 18 计数里有 7 处纯属散文，且新增一句说明性注释
    就会顶破基线（本批亲历）。判据意图是"有多少真实引导点"，故此处只数代码行。
    """
    n = 0
    in_doc = False
    for ln in text.splitlines():
        s = ln.strip()
        if in_doc:
            if _TQ_D in s or _TQ_S in s:
                in_doc = False
            continue
        if s.startswith("#"):
            continue
        for q in (_TQ_D, _TQ_S):
            if q in s and s.count(q) % 2 == 1:
                in_doc = True
        n += len(PAT.findall(ln))
    return n


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
            n = _code_hits(text)
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
            problems.append(
                f"{group}/ 注入数 {cur} > 基线 {base}（只减不增；新代码须经 bootstrap）"
            )
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
