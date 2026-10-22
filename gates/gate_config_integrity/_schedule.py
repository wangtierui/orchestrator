# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._schedule — S/T 族判据（W4-c 五拆）。

schedule.yaml 作业一致性（S 族）+ triggers 条件步骤（T 族，含 T4 条件观察对象）。
公共基础设施经 `._common`。
"""

from __future__ import annotations

import os

# N-166（2026-09-30）：`config.loader` 是**程序可读配置的唯一读取口（R24）**
from config import loader as _loader
from config.enums import SCHEDULE_JOB_KIND, SCHEDULE_TRIGGER

from ._common import ROOT, _load_module_from_path


def _policy(section: str | None, key: str, default=None):
    """取策略值（**缺失即 FAIL 方向**：返回 None → 调用方判据会报问题，不静默放宽）。

    `section=None` ⇒ 读**顶层共用键**（如 `python_aliases`：schedule 与 triggers 两条判据共用）。
    """
    scope = _POLICY if section is None else (_POLICY.get(section) or {})
    return scope.get(key, default)


_SCHED_YAML = _loader.SCHEDULE_YAML  # N-166：路径取自唯一读取口（原为自定常量）
_SCHED_TOOL = os.path.join(ROOT, "tools", "gen_schedule_doc.py")
# N-166：**策略清单已外置** → `config/integrity_policy.yaml`（经 `config.loader` 读取）。
# 为什么外置：这些是**可调策略**（值域/上限/别名），与判据逻辑无关 —— 原来「改策略」必须改
# 门禁源码（高风险动作），且 `_PYTHON_ALIASES` 在文件内**逐字重复两份**（改一漏一 → 同一
# 语义在两条判据下不一致）。判据**逻辑**仍在代码（YAML 无法表达读文件/AST/跨文件比对）。
_POLICY: dict = _loader.load_integrity_policy()
_ON_MISS = tuple(_policy("schedule", "on_miss") or ())
_NOTIFY_KINDS = tuple(_policy("schedule", "notify_kinds") or ())
# 通知事件受控域的**唯一事实源** = `common_lib.notify.NOTIFY_EVENTS`；
# 策略文件里的 `schedule.notify_on` 若声明，则作为**漂移检测**的对照（下方 S3 断言），
# 不再是判定基准——两份各自维护必然漂移（新增 degraded_step 时即触发）。
_NOTIFY_ON = frozenset(_policy("schedule", "notify_on") or ())
_PYTHON_ALIASES = tuple(_policy(None, "python_aliases") or ())  # 顶层共用键（原重复两份）


def _check_schedule() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    if not os.path.exists(_SCHED_YAML):
        return [f"S1: 缺调度唯一事实源 {os.path.relpath(_SCHED_YAML, ROOT)}"], {}
    if not os.path.exists(_SCHED_TOOL):
        return [f"S1: 缺生成器 {os.path.relpath(_SCHED_TOOL, ROOT)}"], {}
    try:
        gsd = _load_module_from_path("_gsd_for_gate", _SCHED_TOOL)
        data = gsd.load_schedule()
    except Exception as e:  # noqa: BLE001
        return [f"S1: schedule.yaml 解析失败：{type(e).__name__}: {e}"], {}

    jobs = data.get("jobs") or []
    ids = [j.get("id", "") for j in jobs]
    if not jobs or not all(ids) or len(set(ids)) != len(ids):
        problems.append(f"S2: jobs 为空或 id 缺失/重复：{ids}")
    for j in jobs:
        jid = j.get("id", "?")
        trigger = j.get("trigger")
        if trigger not in SCHEDULE_TRIGGER:
            problems.append(f"S2: {jid} trigger={trigger!r} 非法（{sorted(SCHEDULE_TRIGGER)}）")
        # E-3（W1-5）：`kind` 归位为**作业角色**——决定它在链里该由哪一层承接（E1 按层断言）
        jkind = j.get("kind")
        if jkind not in SCHEDULE_JOB_KIND:
            problems.append(f"S2: {jid} kind={jkind!r} 非法（{sorted(SCHEDULE_JOB_KIND)}）")
        if trigger == "cron":
            if len((j.get("when") or "").split()) != 5:
                problems.append(f"S2: {jid} when={j.get('when')!r} 非 5 段 cron 表达式")
        elif not j.get("after"):
            problems.append(f"S2: {jid} 为事件驱动但未写 `after`（触发上游）")
        if j.get("on_miss") not in _ON_MISS:
            problems.append(f"S2: {jid} on_miss={j.get('on_miss')!r} 非法（允许 {_ON_MISS}）")
        argv = j.get("argv") or []
        if not argv or not all(isinstance(a, str) and a for a in argv):
            problems.append(f"S2: {jid} argv 须为非空字符串列表")
            continue
        if argv[0] in _PYTHON_ALIASES:
            target = os.path.join(ROOT, argv[1]) if len(argv) > 1 else ""
        else:
            target = os.path.join(ROOT, argv[0])
        if not target or not os.path.exists(target):
            problems.append(
                f"S2: {jid} argv[0]={argv[0]!r} 目标不存在（解析为 {target or '<空>'}）"
            )

    notify = data.get("notify") or {}
    if notify.get("kind") not in _NOTIFY_KINDS:
        problems.append(f"S3: notify.kind={notify.get('kind')!r} 非法（允许 {_NOTIFY_KINDS}）")
    from std_lib.common_lib import notify as _nt

    bad_on = sorted(set(notify.get("on") or []) - set(_nt.NOTIFY_EVENTS))
    if bad_on:
        problems.append(
            f"S3: notify.on 含未登记触发项 {bad_on}"
            f"（受控域 {sorted(_nt.NOTIFY_EVENTS)}）")
    # 漂移检测：策略文件声明的 notify_on 与代码受控域必须一致（否则两份事实源各自演进）
    if _NOTIFY_ON and set(_NOTIFY_ON) != set(_nt.NOTIFY_EVENTS):
        problems.append(
            f"S3: 策略 notify_on {sorted(_NOTIFY_ON)} 与受控域 NOTIFY_EVENTS "
            f"{sorted(_nt.NOTIFY_EVENTS)} 不一致（双事实源漂移；策略项已非判定基准）")
    if notify.get("kind") == "file" and not notify.get("file_dir"):
        problems.append("S3: notify.kind=file 须给 file_dir")

    manual = gsd.MANUAL
    if os.path.exists(manual):
        text = open(manual, encoding="utf-8", errors="replace").read()
        try:
            want = gsd._replace_block(text, gsd.TABLE_START, gsd.TABLE_END, gsd.render_table(jobs))
            want = gsd._replace_block(want, gsd.CRON_START, gsd.CRON_END, gsd.render_cron(jobs))
        except LookupError as e:
            problems.append(f"S4: 运行手册缺自动段标记对：{e}")
            want = text
        if want != text:
            problems.append(
                "S4: 运行手册定时表与 config/schedule.yaml **不一致**"
                "（跑 `python tools/gen_schedule_doc.py` 重写；勿手改手册）"
            )
        detail["manual"] = os.path.relpath(manual, ROOT)
    else:
        detail["manual"] = "（缺运行手册：跳过 S4）"
    # N-186（2026-10-08）：README §5.2 是**同一张定时表**的另一处呈现，此前 S4 只守手册
    #   ⇒ README 静默漂移（实测：缺新增作业、且仍是 N-51 之前的 `--only 6.9 / 21.5` 旧 argv 形态）。
    #   读者据 README 会误判"哪个源没有采集"，故纳入**同一判据**（与手册同渲染源、同阻断口径）。
    readme = gsd.README
    if os.path.exists(readme):
        rtext = open(readme, encoding="utf-8", errors="replace").read()
        try:
            # ⚠️ 必须用 `render_table_block`（表头 + 表体），与 `write_readme` **同一个**
            # 函数：2026-10-22 给 README 的表头挪进受管区后，这里仍调 `render_table`
            # （只有表体）⇒ 两者渲染结果不同 ⇒ S4 恒报"不一致"（假 FAIL，实测踩到）。
            rwant = gsd._replace_block(rtext, gsd.TABLE_START, gsd.TABLE_END,
                                       gsd.render_table_block(jobs))
        except LookupError as e:
            problems.append(f"S4: README 定时表缺自动段标记对：{e}")
            rwant = rtext
        if rwant != rtext:
            problems.append(
                "S4: README 定时表与 config/schedule.yaml **不一致**"
                "（跑 `python tools/gen_schedule_doc.py` 重写；勿手改 README 该段）"
            )
        detail["readme"] = os.path.relpath(readme, ROOT)
    else:
        detail["readme"] = "（缺 README：跳过）"
    # S3b（W1-5 新发现修复）：`notify.on` 须经**唯一读取口**读到非空事件——
    #   直读 yaml 的 `on` 会被 YAML 1.1 解析为布尔 True ⇒ 读到空列表而真空通过。
    try:
        from std_lib.common_lib import notify as _nt

        ev = _nt.declared_events()
        if not ev:
            problems.append(
                'S3b: notify.on 声明为空（YAML 裸键 `on` → 布尔 True；须写 "on"）')
        bad_ev = sorted(set(ev) - set(_nt.NOTIFY_EVENTS))
        if bad_ev:
            problems.append(f"S3b: notify.on 含未登记事件 {bad_ev}（受控域 {sorted(_nt.NOTIFY_EVENTS)}）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"S3b: notify.on 校验失败：{type(e).__name__}: {e}")

    detail["schedule"] = {
        "jobs": len(jobs),
        "cron": sum(1 for j in jobs if j.get("trigger") == "cron"),
        "ids": sorted(ids),
        "notify": notify.get("kind", ""),
    }
    return problems, detail


# --------------------------------------------------------------------------- #
# T. 条件触发配置（v2 §3.5，P2-1）
#    `config/triggers.yaml` 是链外节点的唯一声明；本判据断言：
#    ① 每个 steps[].argv 的目标存在（防"声明了却指向已删脚本"）；
#    ② timeout 合法（正数、≤ 现有同类上限 7200）；
#    ③ on_fail ∈ 受控值（与 std_lib.common_lib.triggers.ON_FAIL 同源）；
#    ④ stage 形如主链阶段号（可选，缺失不阻断——触发项可早于阶段表存在）。
# --------------------------------------------------------------------------- #
_TRIGGERS_YAML = _loader.TRIGGERS_YAML  # N-166：路径取自唯一读取口
_TRIGGERS_LIB = os.path.join(ROOT, "std_lib", "common_lib", "triggers.py")
_MAX_TIMEOUT = int(_policy("triggers", "max_timeout") or 0)  # N-166：策略外置
# N-166：原 `_PYTHON_ALIASES_T` 与上方 `_PYTHON_ALIASES` **逐字重复**（同一语义两份定义）
# → 合并为**单一来源**（策略表 `triggers.python_aliases`）。


def _check_triggers() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    if not os.path.exists(_TRIGGERS_YAML):
        return [f"T1: 缺 {os.path.relpath(_TRIGGERS_YAML, ROOT)}（条件触发唯一事实源）"], {}
    if not os.path.exists(_TRIGGERS_LIB):
        return [f"T1: 缺执行器 {os.path.relpath(_TRIGGERS_LIB, ROOT)}"], {}
    try:
        trg = _load_module_from_path("_trg_for_gate", _TRIGGERS_LIB)
        data = trg.load_triggers()
    except Exception as e:  # noqa: BLE001
        return [f"T1: triggers.yaml 解析失败：{type(e).__name__}: {e}"], {}

    items = data.get("triggers") or []
    ids = [t.get("id", "") for t in items]
    if not items or not all(ids) or len(set(ids)) != len(ids):
        problems.append(f"T2: triggers 为空或 id 缺失/重复：{ids}")
    impls = set(getattr(trg, "CONDITION_IMPLS", {}) or {})
    n_steps = 0
    for t in items:
        tid = t.get("id", "?")
        cond = t.get("enabled_when") or {}
        if not cond:
            problems.append(f"T2: {tid} 未声明 enabled_when（拒绝无判据执行）")
        for name in cond:
            if name not in impls:
                problems.append(
                    f"T2: {tid} 条件 {name!r} 无实现"
                    f"（须登记进 CONDITION_IMPLS；当前 {sorted(impls)}）"
                )
        if t.get("on_fail") not in getattr(trg, "ON_FAIL", frozenset()):
            problems.append(f"T2: {tid} on_fail={t.get('on_fail')!r} 非法")
        steps = t.get("steps") or []
        if not steps:
            problems.append(f"T2: {tid} 无 steps")
        for s in steps:
            n_steps += 1
            argv = s.get("argv") or []
            if not argv or not all(isinstance(a, str) and a for a in argv):
                problems.append(f"T3: {tid} steps[].argv 须为非空字符串列表")
                continue
            head = argv[0]
            target = (
                (os.path.join(ROOT, argv[1]) if len(argv) > 1 else "")
                if head in _PYTHON_ALIASES
                else os.path.join(ROOT, head)
            )
            if not target or not os.path.exists(target):
                problems.append(f"T3: {tid} argv[0]={head!r} 目标不存在（{target or '<空>'}）")
            to = s.get("timeout")
            if not isinstance(to, int) or not (0 < to <= _MAX_TIMEOUT):
                problems.append(f"T3: {tid} timeout={to!r} 非法（1..{_MAX_TIMEOUT} 秒）")
    detail["triggers"] = {
        "count": len(items),
        "steps": n_steps,
        "ids": sorted(ids),
        "conditions": sorted(impls),
    }
    return problems, detail


# --------------------------------------------------------------------------- #
# R. 步骤清单一致性（v2 §3.13.3，P2-6 收尾）
#    `tools/run_production_refresh.STEP_ORDER` 是 `--from/--only/--list-steps` 的判据来源；
#    它与本文件实际 `_run("<step>")` 调用点漂移 → `--only` 会"静默不选中"（最坏的静默失败）。
#    故断言**双向一致**：每个调用点字面量在清单内，每个清单项有调用点。
#    实现刻意**零正则**（本仓写入链路会二次转义反斜杠，见本文件同名注释）。
# --------------------------------------------------------------------------- #
