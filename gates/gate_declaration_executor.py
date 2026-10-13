# -*- coding: utf-8 -*-
"""门禁：**声明面「执行者存在性」**（批 63 / 遗留 W-AM/W-AJ/W-AK/AJ′ 的系统性判据）。

## 为什么需要（本门禁要解决的根因）
第六十批 CI 暴露、第五十九批审计确认：本仓的失实**统一根因是"声明面先进、执行面缺失"** ——
`config/schedule.yaml`/`config/triggers.yaml`/`tools/_manifest.json` 里写了名字，却**无人执行/无人消费** ✗。
既有门禁 S/T/J 只校验"名字/文件/字面量**存在**"，不校验"**谁执行它**" ⇒ **全绿仍失实**。

## 本门禁的四条判据（逐条对应一个真实缺陷）
| # | 判据 | 对应的真实缺陷 |
|---|---|---|
| **E1** | `schedule.yaml` 的 `kind=event` 作业必须有 `covered_by`，且其（去 `{src}` 模板后的）步骤名 ∈ `STEP_ORDER` | `timeliness_sync`/`gov_increment` 声明 event+after，但**全仓无分发器** ⇒ 永不触发 ✗ |
| **E2** | `notify.on` 列出的每个事件都必须**在代码里有发送方**（`notify("<event>"` 至少一处） | 声明 4 类告警，仅 `failed_step` 有产生方 ✗ |
| **E3** | `triggers.yaml` 的 `resume` 必须能**解析到真实脚本文件** | `resume: "run_timeliness_resume.py"` 曾是**纸面字段**（无人读取）✗ |
| **E4** | `tools/_manifest.json` 的每个条目必须**要么** `independent: true` **且** `independent_why` 非空，**要么** 声明 `step` | `gen_benchmark.py` 两者皆无 ⇒ 归属不明（audit_health 误报）✗ |

判据之间**互不重叠**，且都只读 config/清单与源码文本（零副作用）。
"""
from __future__ import annotations

import os
import re
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NL_JOIN = chr(10)  # 零转义换行（避免转义链路二次解释）


def _read_json(path: str) -> dict:
    import json

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _step_order() -> list[str]:
    """从链源码中提取 `STEP_ORDER` 字面量步骤名（模板形如 `collect:{src}` 原样保留）。"""
    p = os.path.join(ROOT, "tools", "run_production_refresh.py")
    try:
        txt = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    # 括号**配平**提取（非贪婪正则会停在内层 `]`（如嵌套列表）⇒ 漏掉后半步骤 ✗）
    # ⚠️ 必须锚在**赋值语句**上（注释里也会出现 "STEP_ORDER" ✗）；且本仓为
    # `STEP_ORDER: tuple[str, ...] = (` —— **元组形态**（批 63 实测：只认 `[` 会提取为空 ✗）。
    m = re.search(r"^STEP_ORDER[^=]*=\s*[\[(]", txt, re.M)
    if not m:
        return []
    j = m.end() - 1
    opener = txt[j]
    closer = "]" if opener == "[" else ")"
    depth = 0
    end = -1
    for k in range(j, len(txt)):
        if txt[k] == opener:
            depth += 1
        elif txt[k] == closer:
            depth -= 1
            if depth == 0:
                end = k
                break
    if end < 0:
        return []
    # ⚠️ 剔除注释行后再取字面量：注释里也会出现引号（实测把 31 项数成 33 ✗）
    body = NL_JOIN.join(ln for ln in txt[j:end].splitlines()
                        if not ln.lstrip().startswith('#'))
    return re.findall(r'"([^"]+)"', body)


def _runs_ok(step: str, order: list[str]) -> bool:
    """步骤名是否被链承接（支持 `{src}` 模板：字面相等，或把 `{src}` 还原为 `{...}` 后相等）。"""
    if step in order:
        return True
    tpl = re.sub(r":[a-z]+$", ":{src}", step) if ":" in step else step
    return tpl in order


def _notify_events_with_senders() -> set[str]:
    """扫描源码，找出**真有发送方**的告警事件（`notify("<event>"`）。"""
    found: set[str] = set()
    for dp, _dn, fn in os.walk(ROOT):
        d = dp.replace(os.sep, "/")
        if any(s in d for s in ("/.git", "__pycache__", "/refactor", "/reports", "/external",
                                "/graphify-out", "/.venv")):
            continue
        for f in fn:
            if not f.endswith(".py"):
                continue
            try:
                txt = open(os.path.join(dp, f), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r'notify\(\s*["\']([a-z_]+)["\']', txt):
                found.add(m.group(1))
    return found


def run() -> tuple[bool, dict]:
    problems: list[str] = []
    detail: dict[str, Any] = {}
    import yaml

    order = _step_order()
    detail["step_order_count"] = len(order)

    # ---- E1：event 作业的 covered_by 必须在 STEP_ORDER 里 ----
    try:
        sc = yaml.safe_load(open(os.path.join(ROOT, "config", "schedule.yaml"), encoding="utf-8")) or {}
        ev = [j for j in (sc.get("jobs") or []) if j.get("kind") == "event"]
        rows = []
        for j in ev:
            jid = j.get("id", "?")
            cov = str(j.get("covered_by") or "").strip()
            if not cov:
                problems.append(
                    f"E1: 事件作业 {jid} 缺 `covered_by`（声明 kind=event 必须写明**链内承接步骤**，"
                    "否则语义无执行者 ✗）")
                continue
            if not _runs_ok(cov, order):
                problems.append(f"E1: 事件作业 {jid} 的 covered_by={cov!r} 不在 STEP_ORDER（无执行者 ✗）")
            rows.append({"id": jid, "covered_by": cov, "in_step_order": _runs_ok(cov, order)})
        detail["event_jobs"] = rows
    except Exception as e:  # noqa: BLE001
        problems.append(f"E1: 读取 schedule.yaml 失败：{type(e).__name__}: {e}")

    # ---- E2：notify.on 的每个事件必须有发送方 ----
    try:
        sc = yaml.safe_load(open(os.path.join(ROOT, "config", "schedule.yaml"), encoding="utf-8")) or {}
        on = list((sc.get("notify") or {}).get("on") or [])
        senders = _notify_events_with_senders()
        missing = [e for e in on if e not in senders]
        detail["notify"] = {"declared": on, "with_sender": sorted(set(on) & senders), "missing": missing}
        if missing:
            problems.append(
                f"E2: notify.on 声明了无发送方的事件 {missing}（声明面先进、执行面缺失 ✗；"
                "补发送点或从 notify.on 移除）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"E2: 校验 notify.on 失败：{type(e).__name__}: {e}")

    # ---- E3：triggers 的 resume 必须解析到真实文件 ----
    try:
        import glob

        tgs = yaml.safe_load(open(os.path.join(ROOT, "config", "triggers.yaml"), encoding="utf-8")) or {}
        rows3 = []
        for t in (tgs.get("triggers") or []):
            name = str(t.get("resume") or "").strip()
            if not name:
                continue
            hits = glob.glob(os.path.join(ROOT, "modules", "**", os.path.basename(name)), recursive=True)
            rows3.append({"id": t.get("id", "?"), "resume": name, "found": bool(hits),
                          "path": hits[0].replace(ROOT + os.sep, "") if hits else ""})
            if not hits:
                problems.append(f"E3: 触发项 {t.get('id')} 的 resume={name!r} **解析不到文件** ✗")
        detail["resume"] = rows3
    except Exception as e:  # noqa: BLE001
        problems.append(f"E3: 校验 triggers.resume 失败：{type(e).__name__}: {e}")

    # ---- E4：manifest 条目归属必须明确（independent+why 或 step） ----
    try:
        man = _read_json(os.path.join(ROOT, "tools", "_manifest.json"))
        bad4 = []
        for ent in (man.get("tools") or []):
            f = ent.get("file", "?")
            ind = bool(ent.get("independent"))
            why = str(ent.get("independent_why") or "").strip()
            step = str(ent.get("step") or "").strip()
            if not (step or (ind and why)):
                bad4.append(f)
        detail["manifest_undeclared"] = bad4
        if bad4:
            problems.append(
                f"E4: manifest 条目归属不明（既非 independent+independent_why，也无 step）：{bad4}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"E4: 读取 _manifest.json 失败：{type(e).__name__}: {e}")

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":  # 便于单跑
    okk, det = run()
    print(("PASS" if okk else "FAIL"), det)
