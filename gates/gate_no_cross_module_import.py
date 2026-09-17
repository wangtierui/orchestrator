# -*- coding: utf-8 -*-
"""gate_no_cross_module_import — 跨模块直连扫描（第 18 道门禁，阶段 3，2026-09-18）

背景（`reports/数据流转与存储交互优化方案_20260917.md` §2.2 G1 / §6 阶段 3）
-----------------------------------------------------------------------------
本仓纪律是「modules/* 之间一律经 `interfaces/` 互访」，但实装长期为"契约层已实装 /
网关层多数空壳"：classifier 有 9 处、ipb 有 2 处、drafter 有 3 处、base_publish 有 3 处
各自 `sys.path.insert` + 裸 import 兄弟模块。一旦有人再引入一条直连，没有任何机制发现。

本门禁把该纪律**变成可执行断言**：静态扫描 `modules/**/*.py`，命中三类模式即报。

判据（三类模式）
----------------
- **A 越权引导**：`sys.path.insert/append` 注入**兄弟模块**目录（字面量或经同文件内
  以兄弟模块名赋值的变量）。同模块自身的引导（如 scrapers 内部引 `clean_index`）不算。
- **B 跨模块裸 import**：`from <pkg> import` / `import <pkg>` 且 `<pkg>` 属于**另一模块**
  的包（映射见 `PKG_OWNER`）。本模块内自引不算。
- **C 全路径跨模块 import**：`from modules.<other> import …`。

**扫描范围刻意只含 `modules/`**：`gates/`（门禁）与 `tools/`（运维/编排）是**横切治理层**，
其职责就是读取全仓事实源做断言与生成，不参与模块依赖图（方案 §1.3 的矩阵亦只含 modules）。
把它们纳入会在语义上把"治理层读数据"误判为"模块耦合"。

基线（只减不增）
----------------
`BASELINE` 冻结**已知且已论证**的残留（当前为空 = 全仓 modules/ 零跨模块直连）。
规则：**新增违规 → FAIL**；基线条目消失 → 报告 `baseline_stale`（提示收缩基线，不阻断）。
新增残留必须**显式登记进基线**并写明理由，使"放宽纪律"永远是一次有记录的决策。
"""
from __future__ import annotations

import os
import re

import paths

# 包/模块名 → 归属模块（用于判定"是否跨模块"）
PKG_OWNER: dict[str, str] = {
    "regulatory_scrapers": "regulatory_scrapers",
    "regulatory_classifier": "regulatory_classifier",
    "internal_policy_base": "internal_policy_base",
    "internal_policy_drafter": "internal_policy_drafter",
    "base_publish": "base_publish",
    # 模块内部包（裸 import 形态）
    "clean_index": "regulatory_scrapers",
    "clause_index": "regulatory_scrapers",
    "rfn": "regulatory_classifier",
    "verification_state": None,       # 见 §已知例外：scrapers 内部子包，非跨模块
}

MODULES = ("regulatory_scrapers", "regulatory_classifier", "internal_policy_base",
           "internal_policy_drafter", "base_publish")

# 冻结基线（`相对路径:行号`）；当前为空 —— 阶段 3 已把 30 处直连全部收口。
# 新增残留须在此登记并注明理由（"只减不增"）。
BASELINE: frozenset[str] = frozenset()

_RE_INSERT = re.compile(r"sys\.path\.(?:insert|append)\s*\(")
_RE_ASSIGN = re.compile(r"^(\w+)\s*=\s*.+$")
_RE_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")


def _own_module(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "modules" else ""


def _scan_file(fp: str, rel: str, own: str) -> list[tuple[int, str, str]]:
    """返回 [(lineno, pattern, line)]。"""
    try:
        lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return []
    # 预扫：同文件内以"兄弟模块名"赋值的变量名（供 A 类识别 `sys.path.insert(0, _X)`）
    sibling_vars: set[str] = set()
    for ln in lines:
        m = _RE_ASSIGN.match(ln.strip())
        if not m:
            continue
        name = m.group(1)
        for mod in MODULES:
            if mod != own and mod in ln:
                sibling_vars.add(name)
    out: list[tuple[int, str, str]] = []
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        # A 越权引导
        if _RE_INSERT.search(ln):
            hit_lit = any(mod != own and mod in ln for mod in MODULES)
            hit_var = any(re.search(rf"\b{v}\b", ln) for v in sibling_vars)
            if hit_lit or hit_var:
                out.append((i, "A越权引导", s[:110]))
                continue
        # B 跨模块裸 import
        m = _RE_IMPORT.match(ln)
        if m:
            pkg = (m.group(1) or m.group(2) or "").split(".")[0]
            owner = PKG_OWNER.get(pkg)
            if owner and owner != own:
                out.append((i, "B跨模块裸import", s[:110]))
                continue
            # C 全路径跨模块 import
            full = m.group(1) or ""
            if full.startswith("modules."):
                tgt = full.split(".")[1]
                if tgt != own:
                    out.append((i, "C全路径跨模块import", s[:110]))
    return out


def run():
    root_mods = paths.MODULES_DIR
    findings: list[dict] = []
    for name in MODULES:
        base = os.path.join(root_mods, name)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "data", "published", "backups", "docs")]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, paths.ROOT).replace("\\", "/")
                for lineno, pattern, text in _scan_file(fp, rel, name):
                    findings.append({"file": rel, "line": lineno, "pattern": pattern, "text": text,
                                     "key": f"{rel}:{lineno}"})

    new = [f for f in findings if f["key"] not in BASELINE]
    stale = sorted(set(BASELINE) - {f["key"] for f in findings})
    detail = {
        "checked_modules": list(MODULES),
        "findings": len(findings),
        "new": [f"{f['key']} [{f['pattern']}] {f['text']}" for f in new][:20],
        "new_count": len(new),
        "baseline": len(BASELINE),
        "baseline_stale": stale,
        "note": "判据=modules/ 内不得出现跨模块 sys.path 引导 / 裸 import / modules.<other> 导入；"
                "基线只减不增（新增违规即 FAIL）。gates/ 与 tools/ 为横切治理层，不在扫描范围。",
    }
    return (not new), detail


if __name__ == "__main__":
    import json
    passed, d = run()
    print("[no_cross_module_import]", "PASS" if passed else "FAIL",
          json.dumps(d, ensure_ascii=False, indent=1))
    raise SystemExit(0 if passed else 1)
