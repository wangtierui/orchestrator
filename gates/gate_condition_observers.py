# -*- coding: utf-8 -*-
"""gate_condition_observers — 条件触发**观察对象**存在性（T4，W0-5，2026-10-13）。

为什么需要（D-1 的直接教训）
--------------------------
`wiki_sync` 触发条件观察 `modules/base_publish/published/publish_manifest.json`——
该文件**从未存在**（发布件落在两个模块的 `published/` 下）⇒ 条件恒返回「不存在 →
不判定」⇒ 触发项自接入起**从未执行**，且**无任何门禁发现**（条件静默失效是
「条件触发」最容易骗人的地方：看起来有判据，实际判据的对象不存在）。

本门禁把「观察对象」变成显式、可校验的声明面（事实源 = `common_lib.triggers.OBSERVATIONS`）：

  T4-1 **闭合**：`CONDITION_IMPLS` 的每个条件都有 `OBSERVATIONS` 登记（反之亦然）——
        条件实现新增文件观察必须同步登记（防「观察了但没登记」的暗管）；
  T4-2 **可达**：`required=True` 的观察路径必须存在（缺失 = D-1 病灶复发 → FAIL）；
        `required=False`（用户自备 token/从未核验等合法缺失）不判存在性，但要求
        登记表给出 `why`（缺失的合法理由必须成文，不允许空原因）；
  T4-3 **同源**：条件实现函数体引用的**模块级路径常量**必须已登记（AST 取 Name，
        常量值取已加载模块的真实值——含 tuple 形态如 `_PUBLISH_MANIFESTS`）——
        防「实现观察 X、登记表写 Y」的两处漂移；
  T4-4 **归属**：观察路径一律为绝对路径且落在 `paths` 注册目录族内（ROOT/MODULES_DIR/
        DATA_DIR/CONFIG_DIR）——防再次拼出指向不存在目录族的路径。
"""
from __future__ import annotations

import ast
import os

import paths


def _rel(p: str) -> str:
    """安全 relpath（跨盘符/异卷时退回绝对路径——`os.path.relpath` 在 Windows 跨盘抛
    ValueError；测试经 tmp_path（常在 C:）注入路径时必现，2026-10-13 W0-5 修复）。"""
    try:
        return os.path.relpath(p, paths.ROOT)
    except ValueError:
        return p


def _triggers_module():
    """加载 triggers 模块（经 bootstrap 引导；供单跑与门禁双路径使用）。"""
    from bootstrap import bootstrap

    bootstrap("all", include_tools=True)
    from std_lib.common_lib import triggers as trg

    return trg


def _module_path_consts(trg) -> dict[str, list[str]]:
    """模块级**路径形态**常量 → {名: [值...]}（str 或 str 序列；值须为绝对路径）。"""
    out: dict[str, list[str]] = {}
    for name, val in vars(trg).items():
        vals: list[str] = []
        if isinstance(val, str):
            vals = [val]
        elif isinstance(val, (tuple, list)) and all(isinstance(x, str) for x in val):
            vals = list(val)
        if vals and all(os.path.isabs(v) and os.sep in v for v in vals):
            out[name] = vals
    return out


def run() -> tuple[bool, dict]:
    trg = _triggers_module()
    problems: list[str] = []
    detail: dict = {"observations": {}, "conditions": sorted(trg.CONDITION_IMPLS)}

    obs = trg.OBSERVATIONS
    conds = set(trg.CONDITION_IMPLS)

    # ---- T4-1 闭合 ----
    unregistered = sorted(conds - set(obs))
    orphan = sorted(set(obs) - conds)
    if unregistered:
        problems.append(f"条件未登记观察对象: {unregistered}（D-1 病灶：观察面不可校验）")
    if orphan:
        problems.append(f"登记表有条件不存在的条目: {orphan}（幽灵登记）")

    # ---- T4-3 同源 ----
    import inspect as _inspect

    const_paths = _module_path_consts(trg)
    for name, fn in trg.CONDITION_IMPLS.items():
        if not callable(fn):
            continue
        try:
            src = _inspect.getsource(fn)
        except (OSError, TypeError):
            src = ""
        if not src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        registered = set(obs.get(name, {}).get("paths") or [])
        for uname in sorted(used & set(const_paths)):
            for v in const_paths[uname]:
                if v not in registered:
                    problems.append(
                        f"{name}: 实现引用路径常量 {uname}"
                        f"（{_rel(v)}）未登记进 OBSERVATIONS"
                        "（实现与登记表漂移）"
                    )

    # ---- T4-2 可达 + T4-4 归属 ----
    roots = (paths.ROOT, paths.MODULES_DIR, paths.DATA_DIR, paths.CONFIG_DIR)
    for name, spec in obs.items():
        plist = list(spec.get("paths") or [])
        detail["observations"][name] = {
            "required": bool(spec.get("required")),
            "why": spec.get("why", ""),
            "missing": [],
        }
        if spec.get("required") and not (spec.get("why") or "").strip():
            problems.append(f"{name}: required=True 但缺 why（合法理由必须成文）")
        for p in plist:
            if not os.path.isabs(p):
                problems.append(f"{name}: 观察路径非绝对路径（{p}）")
                continue
            if not any(p.startswith(r + os.sep) or p == r for r in roots):
                problems.append(
                    f"{name}: 观察路径不在 paths 注册目录族内（{_rel(p)}）"
                )
            if not os.path.exists(p):
                detail["observations"][name]["missing"].append(_rel(p))
                if spec.get("required"):
                    problems.append(
                        f"{name}: required 观察对象缺失（{_rel(p)}）"
                        "——D-1 病灶：条件将静默失效"
                    )

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":
    from gates import run_cli

    raise SystemExit(run_cli("condition_observers", run))
