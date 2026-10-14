# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._steps — R 族判据（W4-c 五拆）。

STEP_ORDER 清单一致性（R1-R3）+ STEP_LAYERS 语义声明（R4）。公共基础设施经 `._common`。
"""

from __future__ import annotations

import os

from gates.gate_declaration_executor import _step_order

from ._common import ROOT

_RPR_REL = ("tools", "run_production_refresh.py")
_STEP_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_:{}")
_QUOTES = ('"', "'")


def _quoted_after(text: str, idx: int, quotes=_QUOTES) -> tuple[str, int]:
    """`idx` 之后首个引号串 → (内容, 结束位置)；无可解析内容返回 ("", idx)。"""
    j = idx
    while j < len(text) and text[j].isspace():
        j += 1
    if j < len(text) and text[j] == "f" and j + 1 < len(text) and text[j + 1] in quotes:
        j += 1
    if j >= len(text) or text[j] not in quotes:
        return "", idx
    q = text[j]
    k = text.find(q, j + 1)
    if k < 0:
        return "", idx
    return text[j + 1 : k], k


def _run_step_literals(text: str) -> list[str]:
    """提取步骤调用点的字面量（过滤注释里的示例形如 `<step>`）。

    覆盖两种调用形态（N-53，2026-09-27）：
      · `_run("<step>", ...)`              —— 常规步骤；
      · `_run_conditional("<step>", ...)`  —— **条件步骤**（触发项入链，决策复用
        `common_lib.triggers`）。二者都必须登记进 `STEP_ORDER`，故此处同扫。
    """
    out: list[str] = []
    for marker in ("_run(", "_run_conditional("):
        start = 0
        while True:
            i = text.find(marker, start)
            if i < 0:
                break
            cand, j = _quoted_after(text, i + len(marker))
            if cand and all(c in _STEP_CHARS for c in cand):
                out.append(cand)
            start = (j if j > i else i + len(marker)) + 1
    return out


def _check_step_order() -> tuple[list[str], dict]:
    problems: list[str] = []
    fp = os.path.join(ROOT, *_RPR_REL)
    rel = "/".join(_RPR_REL)
    if not os.path.exists(fp):
        return [f"R1: 缺 {rel}（主链实现）"], {}
    text = open(fp, encoding="utf-8", errors="replace").read()
    # W1（2026-10-13）：`STEP_ORDER` 已改为 `STEP_LAYERS` 的**派生视图**，源码不再有字面量
    # 元组 ⇒ 复用判据 E1 的读取器（运行时导入优先 + 两级源码降级，单一实现不双写）。
    order = _step_order()
    used = _run_step_literals(text)
    if not order:
        return [f"R1: {rel} 未声明 STEP_ORDER（--from/--only 将失去判据来源）"], {}
    miss_in_order = sorted(set(used) - set(order))
    if miss_in_order:
        problems.append(
            f"R2: {rel} 的 `_run(...)` 调用点 {miss_in_order} 未登记进 STEP_ORDER"
            "（`--only/--from` 将静默漏选该步骤）"
        )
    miss_in_file = sorted(set(order) - set(used))
    if miss_in_file:
        problems.append(
            f"R2: STEP_ORDER 声明了 {miss_in_file} 但本文件无对应 `_run(...)` 调用"
            "（清单漂移：删步骤后未同步清单）"
        )
    detail = {
        "step_order": {
            "declared": len(order),
            "wired": len(used),
            "missing_in_order": miss_in_order,
            "missing_in_file": miss_in_file,
            "steps": order,
        }
    }
    # ---- R4（W1-3）：语义声明完整性（分层声明的唯一事实源 = `STEP_LAYERS`）----
    p4, d4 = _check_step_layers()
    problems.extend(p4)
    detail["step_layers"] = d4
    return problems, detail


_CHAIN_MOD = None


def _chain_module():
    """加载链模块（运行时单例；**先登记 sys.modules**——模块含 @dataclass，否则
    dataclasses 回查 `sys.modules[cls.__module__].__dict__` 命中 None → AttributeError，
    本判据会静默退化为「未声明 STEP_ORDER」）。"""
    global _CHAIN_MOD
    if _CHAIN_MOD is not None:
        return _CHAIN_MOD
    import importlib.util as _iu
    import sys as _sys

    from bootstrap import bootstrap

    bootstrap("all", include_tools=True)
    p = os.path.join(ROOT, *_RPR_REL)
    spec = _iu.spec_from_file_location("_rpr_for_r4", p)
    if spec is None or spec.loader is None:
        return None
    mod = _iu.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _CHAIN_MOD = mod
    return mod


def _check_step_layers() -> tuple[list[str], dict]:
    """R4：`STEP_LAYERS` 语义声明完整性——五子判据（每条都对应一类真实漂移）。

      R4-a **受控值**：`kind`/`fail_semantics` ∈ `config.enums` 受控域（构造时已校验，此处
           复核实际集合，防受控域被放宽后既有声明失守）；
      R4-b **触发器闭合**：条件步骤的 `trigger_id` ∈ `config/triggers.yaml` 声明的触发项 id；
      R4-c **拓扑无环**：`depends_on` 的步骤名/产物键引用不得成环；
      R4-d **血缘键闭合**：`produces` ∈ `GOV_ARTIFACTS`（声明了产物却不在血缘声明面 ⇒
           该产物无血缘可查——W1-3 即由此暴露 `relevance:views`/`timeliness:ledger` 两处缺口）；
      R4-e **计划完备**：`LAYER_EXEC_PLAN` 覆盖全部 `StepSpec` 且每步恰好一次（漏段=漏跑，
           重复=重复跑）。
    """
    problems: list[str] = []
    detail: dict = {}
    mod = _chain_module()
    if mod is None or not hasattr(mod, "STEP_LAYERS"):
        return ["R4: 链模块不可读或无 STEP_LAYERS（判据不可执行）"], detail

    from config import enums as _en
    from std_lib.common_lib import triggers as _trg  # 触发项唯一读取口（N-166：不经第二路径）

    specs = list(mod.step_specs())
    detail["steps"] = len(specs)

    # R4-a 受控值
    bad_kind = sorted({s.kind for s in specs} - set(_en.STEP_KINDS))
    bad_fail = sorted({s.fail_semantics for s in specs} - set(_en.FAIL_SEMANTICS))
    if bad_kind:
        problems.append(f"R4-a: 非受控 kind {bad_kind}（受控域 {sorted(_en.STEP_KINDS)}）")
    if bad_fail:
        problems.append(f"R4-a: 非受控 fail_semantics {bad_fail}")

    # R4-b 触发器闭合
    trig_ids = {str(t.get("id")) for t in (_trg.load_triggers().get("triggers") or [])}
    bad_trig = sorted(
        {s.trigger_id for s in specs if s.kind == "conditional"} - trig_ids
    )
    if bad_trig:
        problems.append(f"R4-b: 条件步骤 trigger_id 不在 triggers.yaml: {bad_trig}")
    # 反向：**链内声明口径**必须被步骤承接——`CHAIN_TRIGGER_IDS`（SSOT，`cli.py run --triggers`
        # 据此排除链内项以免双写）里的 id 若无对应步骤 ⇒ 该触发项**永不执行**（既被排除、又没跑）。
        # 注：不在该集合内的触发项（如 `supp_ingest`）属**链外**，由触发器执行器 `run_all` 承接，
        # 是设计而非缺陷，不得判为孤儿。
        used_trig = {s.trigger_id for s in specs if s.trigger_id}
        chain_internal = set(getattr(_trg, "CHAIN_TRIGGER_IDS", frozenset()) or frozenset())
        unwired = sorted(chain_internal - used_trig)
        if unwired:
            problems.append(
                f"R4-b: CHAIN_TRIGGER_IDS 声明为链内却无步骤承接: {unwired}"
                "（`run --triggers` 会排除它、链内也没人跑 ⇒ 永不执行）"
            )
        detail["triggers"] = {
            "declared": sorted(trig_ids),
            "by_step": sorted(used_trig),
            "chain_internal": sorted(chain_internal),
            "chain_internal_unwired": unwired,
        }

    # R4-c 依赖无环：**步骤 + 产物**统一建图——边 = 「本节点依赖谁」：
    #   · 步骤 → 其 `depends_on`（步骤名或产物键）；
    #   · 产物 → 其 `GOV_ARTIFACTS.inputs`（上游产物）+ **生产者步骤**（产物新鲜度取决于谁产出它）。
    # 环 = 声明自相矛盾（如 A 依赖 B 的产物，而 B 又依赖 A 的产物）。
    arts = getattr(mod, "GOV_ARTIFACTS", {}) or {}
    prod_steps: dict[str, list[str]] = {}
    for s in specs:
        for p in s.produces:
            prod_steps.setdefault(p, []).append(s.name)

    graph: dict[str, list[str]] = {}
    for s in specs:
        graph.setdefault(s.name, []).extend(d for d in s.depends_on if d)
    for key, meta in arts.items():
        deps = [x for x in (meta.get("inputs") or []) if x]
        deps.extend(prod_steps.get(key, []))  # 产物 → 生产者步骤
        graph.setdefault(key, deps)
    # 未声明的外部引用不参与环检测（避免把「未登记」误判为「成环」，两者由 R4-d 分判）
    for n in list(graph):
        graph[n] = [m for m in graph[n] if m in graph]
    cycle = _find_cycle(graph)
    if cycle:
        problems.append(f"R4-c: 依赖声明成环: {' → '.join(cycle)}")
    detail["graph_nodes"] = len(graph)

    # R4-d 血缘键闭合
    bad_prod = sorted({p for s in specs for p in s.produces} - set(arts))
    if bad_prod:
        problems.append(
            f"R4-d: produces 未登记进 GOV_ARTIFACTS: {bad_prod}"
            "（声明了产物却不在血缘声明面 ⇒ 无血缘可查）"
        )
    detail["gov_artifacts"] = len(arts)
    detail["produces"] = sorted({p for s in specs for p in s.produces})

    # R4-e 计划完备
    plan = [n for _k, names in mod.LAYER_EXEC_PLAN for n in names]
    if len(plan) != len(set(plan)):
        dup = sorted({n for n in plan if plan.count(n) > 1})
        problems.append(f"R4-e: LAYER_EXEC_PLAN 有重复步骤: {dup}")
    orphan_steps = sorted({s.name for s in specs} - set(plan))
    if orphan_steps:
        problems.append(f"R4-e: 步骤未进执行计划（永不执行）: {orphan_steps}")
    ghost = sorted(set(plan) - {s.name for s in specs})
    if ghost:
        problems.append(f"R4-e: 执行计划含未声明步骤: {ghost}")
    detail["plan_segments"] = len(mod.LAYER_EXEC_PLAN)
    detail["layers"] = sorted(mod.STEP_LAYERS)
    return problems, detail


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    """DFS 找一条环路径（无环返回 []；用于声明面拓扑校验）。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []

    def dfs(n: str) -> list[str]:
        color[n] = GREY
        stack.append(n)
        for m in graph.get(n, []):
            if m not in color:
                continue
            if color[m] == GREY:
                return stack[stack.index(m):] + [m]
            if color[m] == WHITE:
                got = dfs(m)
                if got:
                    return got
        stack.pop()
        color[n] = BLACK
        return []

    for n in list(graph):
        if color[n] == WHITE:
            got = dfs(n)
            if got:
                return got
    return []


# --------------------------------------------------------------------------- #
# U. 助手状态与记忆权威（v2 §3.13.1 M2 / §3.13.7，P3-1 + P3-4）
#    背景：仓内 `.codebuddy/`（执行历史 2 处 + 按日记忆）与**用户级仓外记忆**长期并存，
#    权威性不明；且它一度是"调度口径"的事实来源（M1/M3 二义）。
#    判据（对应 `.codebuddy/README.md` 的三条纪律）：
#      U1 代码/配置**不得把 `.codebuddy` 当输入**（路径构造/读取）；排除集里的纯字符串条目豁免
#      U2 `.codebuddy/README.md` 存在且声明"仓外记忆为权威"（防口径再次失传）
#      U3 调度/触发事实源（schedule.yaml / triggers.yaml）不得回指 `.codebuddy`
#      U4 仓内 `.codebuddy/**` 不得新增 `*.yaml` 声明（防记忆再次成为流程输入）
# --------------------------------------------------------------------------- #
