# -*- coding: utf-8 -*-
"""gate_run_steps — **运行台账完整性**（N-152，2026-09-30）

事故背景（为什么需要这道门禁）
----------------------------
真实全流程运行（2026-09-29，含抓取）中 `clean:gov` **超时被强杀**（1800.4s），
而它**已经写出的产物**（`gov_cleaned_*.jsonl` 1132MB）随后被 `apply:gov` / `classify:all`
**当作正常输入采用**，最终 `gates` 仍然报 **"全部门禁通过"** —— 失败被**无感吞掉**。
这不是"某条判据写错了"，而是**没有任何门禁知道"哪一步失败了"**：门禁只看产物形状，
而产物**存在**（虽未走完流程）。

判据（三层，逐层收紧）
--------------------
  ① **台账可读**：`data/run_state/last_run_steps.json` 存在且结构完整（缺失 → **跳过并披露**，
     因为克隆环境从未跑过全链，不该因此 FAIL；这正是"判据不可执行 ≠ 判据通过"的口径）。
  ② **危险组合（阻断）**：对每个失败步骤，若其**对应产物存在**，且产物 mtime **落在该步的
     时间窗 `[started_at, ended_at]` 内** → 说明"**失败步骤的产物已落盘、且必然被下游消费**"
     → **FAIL**（附步骤名、产物路径与处置指引）。
  ③ **人工确认闸门**：`data/run_state/acknowledged.json` 可登记"已知悉并处置"（须给出
     `reason` + `acked_at`）。有确认 → 降为 WARN（**不静默**：确认本身是留痕动作，
     与 `retention --apply` / `governance_sync --apply` 的人工闸门同哲学）。

为何用"时间窗"而非"有没有产物"
------------------------------
"有产物"不足以定罪：上一轮成功产出的产物当然存在。只有**产物 mtime 落在失败步骤的运行窗内**
才证明"它是被那次失败运行产生的"。这也是本判据能区分"历史产物"与"失败产物"的关键。
"""

from __future__ import annotations

import datetime
import json
import os

# 引导：**不得**自写 `sys.path.insert` —— `gate_import_bootstrap` 断言 gates 层计数**只减不增**
#   （基线 9）。本文件初版写了插入 → 成为第 10 个 → 门禁**立即 FAIL**（实测捕获，正是它该做的）。
#   门禁一律经 `cli.py gates` / `GatesRunner` 调用，仓根必已在 `sys.path` → 直接 import 即可。
import paths

RUN_STATE = os.path.join(paths.DATA_DIR, "run_state")
LEDGER = os.path.join(RUN_STATE, "last_run_steps.json")
ACK = os.path.join(RUN_STATE, "acknowledged.json")


def _product_of(step: str) -> list:
    """失败步骤 → 其**产物路径**（只映射"产物直接进下游"的步骤；其余返回空=不判定）。"""
    out: list = []
    kind, _, src = step.partition(":")
    if not src:
        return out
    cleaners = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned")
    raw_dir = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "raw")
    if kind == "clean" and os.path.isdir(cleaners):
        for fn in os.listdir(cleaners):
            if fn.startswith(f"{src}_cleaned_"):
                out.append(os.path.join(cleaners, fn))
    elif kind == "apply" and os.path.isdir(cleaners):
        for fn in os.listdir(cleaners):
            if fn.startswith(f"{src}_cleaned_"):
                out.append(os.path.join(cleaners, fn))
    elif kind == "collect" and os.path.isdir(raw_dir):
        for fn in os.listdir(raw_dir):
            if fn.startswith(src) and fn.endswith(".json"):
                out.append(os.path.join(raw_dir, fn))
    return out


def _ts(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str) and t:
        try:
            return datetime.datetime.fromisoformat(t).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def run() -> tuple:
    problems: list = []
    warnings: list = []
    detail: dict = {}

    if not os.path.exists(LEDGER):
        detail["run_steps"] = {
            "skipped": f"无运行台账（{os.path.relpath(LEDGER, paths.ROOT)}）→ 本机未跑过全链，判据不可执行",
        }
        return True, detail

    try:
        led = json.load(open(LEDGER, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"G-1: 运行台账不可解析：{type(e).__name__}: {e}")
        return False, {"run_steps": {"error": str(e)}}

    steps = led.get("steps") or []
    failed = [s for s in steps if isinstance(s, dict) and int(s.get("rc") or 0) != 0]
    acked: dict = {}
    if os.path.exists(ACK):
        try:
            acked = (json.load(open(ACK, encoding="utf-8")) or {}).get("acked") or {}
        except Exception:  # noqa: BLE001  确认件损坏不应掩盖真正的失败
            acked = {}

    dangerous: list = []
    for s in failed:
        name = str(s.get("step") or "")
        t0, t1 = _ts(s.get("started_at")), _ts(s.get("ended_at"))
        products = _product_of(name)
        inwindow = []
        if t0 and t1:
            for p in products:
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                if t0 - 1.0 <= mt <= t1 + 1.0:
                    inwindow.append(os.path.relpath(p, paths.ROOT))
        if inwindow:
            dangerous.append({"step": name, "rc": s.get("rc"), "exit_code": s.get("exit_code"),
                              "products": inwindow})

    detail["run_steps"] = {
        "run_at": led.get("run_at"),
        "total_elapsed_s": led.get("total_elapsed_s"),
        "steps": len(steps),
        "failed": [str(s.get("step")) for s in failed],
        "dangerous": dangerous,
        "acknowledged": sorted(acked),
    }

    for d in dangerous:
        if d["step"] in acked:
            warnings.append(
                f"G-2: 失败步骤 {d['step']} 的产物已落盘并被下游采用（产物 {d['products']}），"
                f"但已**人工确认**：{str(acked.get(d['step']) or '')[:120]}"
            )
        else:
            problems.append(
                f"G-2: **失败步骤的产物已落盘并被下游采用** —— `{d['step']}` "
                f"rc={d['rc']}({d.get('exit_code')})，产物 {d['products']}。"
                "该产物**未走完流程**（如超时被杀），不得视为可信输入；"
                "处置：① 修因后重跑该步；或 ② 若已确认影响可控，在 "
                f"`{os.path.relpath(ACK, paths.ROOT)}` 登记 `{{\"acked\": "
                f"{{\"{d['step']}\": {{\"reason\": \"…\", \"acked_at\": \"…\"}}}}}}`（留痕后才降级为告警）。"
            )

    if failed and not dangerous:
        warnings.append(
            f"G-3: 本轮有失败步骤 {[str(s.get('step')) for s in failed]}，但**未见其产物落盘**"
            "（下游应已明确缺数据）→ 不阻断。"
        )

    detail["run_steps"]["warnings"] = warnings
    return (not problems), {"problems": problems, **detail}
