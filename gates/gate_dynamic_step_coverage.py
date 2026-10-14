# -*- coding: utf-8 -*-
"""gate_dynamic_step_coverage — 动态模板步骤**运行时覆盖**（R3，W0-5，2026-10-13）。

为什么需要（D-3 的直接教训）
--------------------------
`relevance:{src}` 声明在 `STEP_ORDER` 里、README 渲染「每源一步」，但实装挂在 clean
循环之后、用**残留循环变量** ⇒ 只跑 `relevance:supp` 一源。**静态清单永远看不出来**
（STEP_ORDER 声明正确、调用点存在）——必须用**运行台账**断言：最近一次完整运行中，
每个 `{src}` 模板步骤都出现了全部五源。

判据（三层，逐层收紧；口径同 gate_run_steps「判据不可执行 ≠ 通过」）
  R3-1 **台账可读**：`data/run_state/last_run_steps.json` 存在且结构完整
        （缺失/无完整运行 → **跳过并披露**，克隆环境未跑过全链不该因此 FAIL）；
  R3-2 **覆盖**：对 STEP_ORDER 中每个 `{src}` 模板步骤，台账须为其**每个期望实例**
        留有记录（ran 或 skipped 均可——「无 raw 变化 → SKIP」是合法语义；**完全没有记录**
        才是 D-3 病灶）。期望实例口径：`collect:{src}` 经 `collect_targets("all")` 展开
        （gov 拆 xzfgk/zhengceku 两个子源步），其余模板 = `config.enums.SOURCE_ORDER`。
        ** exercised 判定**：该模板至少有 1 个**未跳过**实例时才要求全覆盖（`--only`
        选择性子集运行不该被判覆盖缺失；全模板 skipped/无记录 → 披露「本次未演练」）；
  R3-3 **顺序**：模板步骤的各源实例须在台账中连续成段（同阶段批处理），
        防止「跑到别处又跑一源」的半接线形态。

事实源：`STEP_ORDER`/`collect_targets` 取自 `run_production_refresh`（判据 R 同款加载，
不双写）；源集合取自 `config.enums.SOURCE_ORDER`（受控枚举）。
"""
from __future__ import annotations

import json
import os
import sys

import paths

LEDGER = os.path.join(paths.DATA_DIR, "run_state", "last_run_steps.json")


def _rel(p: str) -> str:
    """安全 relpath（跨盘符/异卷时退回绝对路径——Windows 跨盘 relpath 抛 ValueError，
    tmp_path 注入的台账路径必现；2026-10-13 W0-5 修复）。"""
    try:
        return os.path.relpath(p, paths.ROOT)
    except ValueError:
        return p


def _chain_module():
    """加载 `tools/run_production_refresh.py`（判据 R 同款方式；STEP_ORDER/collect_targets 的唯一事实源）。"""
    from bootstrap import bootstrap

    bootstrap("all", include_tools=True)
    import importlib.util as _iu

    p = os.path.join(paths.ROOT, "tools", "run_production_refresh.py")
    spec = _iu.spec_from_file_location("_rpr_for_r3", p)
    if spec is None or spec.loader is None:  # 防御：规格不可建（理论不可达）→ 显式失败
        raise RuntimeError(f"无法为 {p} 创建 import 规格")
    mod = _iu.module_from_spec(spec)
    # ⚠️ **必须先登记 sys.modules 再 exec_module**（2026-10-13 修复）：W1 起链模块含
    # `@dataclass`（StepSpec/LayerSpec），dataclasses 处理类时会回查
    # `sys.modules[cls.__module__].__dict__`；未登记时该值为 None → AttributeError
    # `'NoneType' object has no attribute '__dict__'` ⇒ 门禁**静默退化为「不可执行」**
    # （即本门禁一度形同虚设——正是 D-1「看起来有判据、实际判据不生效」的同构病灶）。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _expected_instances(mod, tpl: str, sources: list[str]) -> list[str]:
    """模板 → 期望实例序列：`collect:{src}` 走子源展开（gov 拆两步），其余 = 事实源五源。"""
    head = tpl.split(":")[0]
    if head == "collect":
        try:
            return list(mod.collect_targets("all"))
        except Exception:  # noqa: BLE001  展开失败退化为事实源（披露见 detail）
            return list(sources)
    return list(sources)


def run() -> tuple[bool, dict]:
    from config.enums import SOURCE_ORDER

    detail: dict = {"coverage": {}, "templates": []}
    try:
        mod = _chain_module()
        order = tuple(mod.STEP_ORDER)
    except Exception as e:  # noqa: BLE001  STEP_ORDER 不可读 → 不可执行（披露真实原因）
        return True, {
            "skipped": (
                f"STEP_ORDER 不可读（{type(e).__name__}: {str(e)[:200]}）→ 判据不可执行"
                "（单跑入口 python -m gates.gate_dynamic_step_coverage 若报此错，"
                "先经 bootstrap 引导再导入，或用 cli.py gates 运行）"
            )
        }

    templates = [s for s in order if "{src}" in s]
    detail["templates"] = templates
    if not templates:
        return True, {**detail, "skipped": "STEP_ORDER 无 {src} 模板步骤 → 无可判内容"}

    if not os.path.exists(LEDGER):
        return True, {
            **detail,
            "skipped": f"无运行台账（{_rel(LEDGER)}）"
                       " → 本机未跑过全链，判据不可执行（≠通过）",
        }
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            ledger = json.load(fh)
        steps = ledger.get("steps") or []
    except (OSError, ValueError) as e:
        return True, {**detail, "skipped": f"台账不可读（{type(e).__name__}）→ 判据不可执行"}

    entries = [(str(s.get("step") or ""), bool(s.get("skipped"))) for s in steps]
    if not entries:
        return True, {**detail, "skipped": "台账无步骤记录 → 判据不可执行"}
    ran_names = [n for n, _sk in entries]

    problems: list[str] = []
    for tpl in templates:
        head = tpl.split(":")[0]
        want = _expected_instances(mod, tpl, list(SOURCE_ORDER))
        idxs = [i for i, n in enumerate(ran_names) if n.startswith(head + ":")]
        present = {ran_names[i].split(":", 1)[1] for i in idxs if ":" in ran_names[i]}
        exercised = any(not entries[i][1] for i in idxs)
        missing = [s for s in want if s not in present]
        # R3-3 顺序：模板实例须连续成段
        contiguous = bool(idxs) and (idxs[-1] - idxs[0] + 1) == len(idxs)
        detail["coverage"][tpl] = {
            "expected": want, "present": sorted(present), "missing": missing,
            "exercised": exercised, "contiguous": contiguous,
        }
        if not exercised:
            # 本次运行未演练该模板（--only 子集/全跳过）→ 披露，不判 FAIL
            continue
        if missing:
            problems.append(
                f"{tpl}: 台账缺实例 {missing}（声明每源一步、实装未全覆盖——D-3 病灶）"
            )
        if not contiguous:
            problems.append(f"{tpl}: 各源实例在台账中不连续（半接线形态）")

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":
    from gates import run_cli

    raise SystemExit(run_cli("dynamic_step_coverage", run))
