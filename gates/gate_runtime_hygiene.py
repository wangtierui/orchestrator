# -*- coding: utf-8 -*-
"""gate_runtime_hygiene — 运行时卫生（v2 §3.8 表 A 第 5 项，判据 ①退出码 ②静默异常 ③接口空壳）

三组判据与当前状态（2026-09-26 引入，**首期只披露、不阻断**，v2 D-8 过渡策略）：

    ① 退出码：裸整数 `return N` 应改为 `config.exitcodes.ExitCode` 成员。
       现状：`cli.py` 与 `commands/` 已可直接采用；`tools/`、`modules/` 仍有历史值。
    ② 静默异常：`except ...: pass`（`pass` 独占行且紧邻 except）数量只减不增。
       现状：≥143 处（v2 §2.4.1）；本门禁按目录披露并冻结基线。
    ③ 接口空壳：`interfaces/**` 的 `raise NotImplementedError` 数量。
       现状：`theme_api.py` 3 处（v2 §2.2.3）；P1-3 实装后归零。

设计说明：本门禁**当前恒 PASS**，只把三项指标写入 detail（并冻结基线），
目的是让"只减不增"可机器观察；待 P1-3/P1-5 完成后把 `_STRICT` 置 True
即可转为阻断（每项均在下面留有 `_STRICT_*` 开关）。
"""
from __future__ import annotations

import os
import re

import paths

ROOT = paths.ROOT
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "data", "reports", "graphify-out",
             "external", "tessdata", "backups", ".ruff_cache", ".codebuddy", "retired"}

PAT_RETURN_INT = re.compile(r"^\s*return\s+[1-9]\d*\s*(?:#.*)?$", re.M)
PAT_BARE_PASS = re.compile(r"except[^\n]*:\s*\n\s*pass\b")

# 三项开关：True 即阻断（首期为 False，只披露）
_STRICT_EXITCODES = False
_STRICT_BARE_PASS = False
_STRICT_IFACE_STUBS = False

# 基线（只减不增；None = 尚未冻结）
BASELINE_BARE_PASS: int | None = None
BASELINE_RETURN_INT: int | None = None


def _iter_py(*, only_prefix: str = ""):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            if only_prefix and not rel.startswith(only_prefix):
                continue
            yield rel, fp


def run() -> tuple[bool, dict]:
    problems: list[str] = []
    detail: dict = {}

    # ① 退出码
    ints: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(PAT_RETURN_INT.findall(text))
        if n:
            ints[rel] = n
    total_ints = sum(ints.values())
    detail["exitcode_bare_int"] = {
        "total": total_ints,
        "files": dict(sorted(ints.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_RETURN_INT,
    }
    if BASELINE_RETURN_INT is not None and total_ints > BASELINE_RETURN_INT and _STRICT_EXITCODES:
        problems.append(f"裸整数退出码 {total_ints} > 基线 {BASELINE_RETURN_INT}")

    # ② 静默异常
    bare: dict[str, int] = {}
    for rel, fp in _iter_py():
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n = len(PAT_BARE_PASS.findall(text))
        if n:
            bare[rel] = n
    total_bare = sum(bare.values())
    detail["bare_except_pass"] = {
        "total": total_bare,
        "files": dict(sorted(bare.items(), key=lambda kv: -kv[1])[:8]),
        "baseline": BASELINE_BARE_PASS,
    }
    if BASELINE_BARE_PASS is not None and total_bare > BASELINE_BARE_PASS and _STRICT_BARE_PASS:
        problems.append(f"except-pass {total_bare} > 基线 {BASELINE_BARE_PASS}")

    # ③ 接口空壳
    stubs: dict[str, list[int]] = {}
    for rel, fp in _iter_py(only_prefix="interfaces/"):
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        locs = [text[:m.start()].count("\n") + 1
                for m in re.finditer(r"raise\s+NotImplementedError", text)]
        if locs:
            stubs[rel] = locs
    detail["interface_stubs"] = {"total": sum(len(v) for v in stubs.values()), "files": stubs}
    if stubs and _STRICT_IFACE_STUBS:
        problems.append(f"interfaces/ 存在 NotImplementedError 空壳：{stubs}")

    if problems:
        detail["problems"] = problems
    detail["note"] = "首期只披露不阻断（v2 D-8）；三项 _STRICT_* 开关待对应期次完成后置 True"
    return (not problems), detail


if __name__ == "__main__":
    import json

    passed, det = run()
    print("[runtime_hygiene]", "PASS" if passed else "FAIL", json.dumps(det, ensure_ascii=False, indent=1))
    raise SystemExit(0 if passed else 1)
