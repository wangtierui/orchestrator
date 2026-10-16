# -*- coding: utf-8 -*-
"""tools.step_triage — **步级失败取证**（诊断第 2 波，2026-10-16）

定位（承接第 1 波门禁归因）
--------------------------
第 1 波解决「门禁 FAIL 归因」；本波解决「**某一步失败了，怎么快速定位**」。
实测数据条件（90 天）：
  · 失败记录 12 条，`exit_code` 只有两类（`FAIL` 9 / `TIMEOUT` 3）⇒ **失败语义清晰**；
  · `note` 字段内容**质量高**（含超时原因 / 报告路径 / 门禁归档摘要），非空 10/12；
  · 时间窗（`started_at`/`finished_at`）覆盖率仅 **3%**（第八十三批新增列，老数据按口径不回填）
    ⇒ 本波**不把时间窗当主力证据**，只在有数据时用。

**本工具不做「自动根因断言」**（这是与诊断原方案的关键取舍）
------------------------------------------------------------
原方案要"沿链自动归因 + 置信度"。本仓**无记录数列**（`run_step` 无 `record_count`），
"穿透深度"无法量化 ⇒ 自动断言会产出**看似精确实则臆测**的结论。
本波只做**证据聚合 + 怀疑排序**，并**显式列出未取到证据的项**——
不确定就标"待人工确认"，不给结论（沿用 `argument-strength-evaluation` 的纪律）。

取证包构成（每项都标注是否取到）
--------------------------------
  ① `run_step` 最近 N 次执行（rc / exit_code / elapsed_s / note 原文 / 时间窗若有）
  ② 步骤日志 tail（经 `web/snapshots.latest_step_log`，**归一规则与写入方逐字一致**）
  ③ 该步 `produces` 的水位状态（2026-10-16 起 19/19 产物全有归属 ⇒ 这项对所有步骤可用）
  ④ 耗时对比（对照第八十四批 F1 基线的 p50/p90 ⇒ 判「是否变慢」）
  ⑤ 拓扑邻接（上游链 / 受影响下游；经 `interfaces.topology_api` 的**双层匹配**）

用法
----
    python -m tools.step_triage --list-failures      # 近 N 天失败步骤概览
    python -m tools.step_triage <step>                # 取证某步（最近一次失败）
    python -m tools.step_triage <step> --run-id RUN-xxx --json
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode
from interfaces import topology_api as T

TZ8 = timezone(timedelta(hours=8))
BASELINE = os.path.join(paths.DATA_DIR, "run_state", "step_duration_baseline.json")

#: 失败模式的证据化分类（**只按 `exit_code` 事实归类**，不猜原因）
FAIL_MODE: dict[str, str] = {
    "TIMEOUT": "**超时**：撞上层硬超时被强杀（note 常含'子进程无已捕获输出'）"
               "⇒ 该步在链内**未跑完**，其产物不可信（下游可能已采用残缺产物）",
    "FAIL": "**判据/执行失败**：步骤自身返回非 0（note 常含具体判据摘要或报告路径）",
}


@dataclass
class Evidence:
    """单项证据（**必须带来源**；取不到也要显式记为 missing）。"""

    kind: str
    ok: bool
    detail: str
    data: dict = field(default_factory=dict)


@dataclass
class StepTriage:
    step: str
    found_failure: bool
    node: T.Node | None = None
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    suspicions: list[tuple[str, str]] = field(default_factory=list)  # (怀疑项, 依据)
    run_id: str = ""
    recorded_at: str = ""
    exit_code: str = ""
    note: str = ""

    @property
    def complete(self) -> bool:
        """取证是否**完整**（无 missing 项）——不完整时结论须标"待人工确认"。"""
        return not self.missing


def _open_conn() -> sqlite3.Connection | None:
    db = os.path.join(paths.DATA_DIR, "governance.db")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _is_failure(r: sqlite3.Row) -> bool:
    return (r["rc"] not in (0,)) or (str(r["exit_code"]) not in ("0", "OK", "SKIP"))


def _baseline_for(step: str) -> dict | None:
    """取该步的耗时基线（p50/p90）——判「是否变慢」的唯一量化依据。"""
    if not os.path.exists(BASELINE):
        return None
    try:
        d = json.load(open(BASELINE, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (d.get("per_step") or {}).get(step)


def triage(step: str, run_id: str = "", history: int = 5,
           con: sqlite3.Connection | None = None) -> StepTriage:
    """步级取证主入口（**只读**）。"""
    own = con is None
    con = con or _open_conn()
    t = StepTriage(step=step, found_failure=False)

    # ① 拓扑节点（双层匹配）
    node = T.get_node(step)
    t.node = node
    if node is None:
        t.missing.append(f"拓扑里找不到步骤 `{step}`（声明面无此名，也不是任何 "
                         f"`*.{{src}}` 模板的实例）⇒ 无法定位其依赖与产物")
        if own and con:
            con.close()
        return t
    t.evidence.append(Evidence(
        "topology", True,
        f"节点匹配方式 **{node.matched_as}**（台账名 `{node.name}` ↔ 声明名 "
        f"`{node.spec_name}`，层 `{node.layer}`，kind={node.kind}）",
        {"matched_as": node.matched_as, "spec_name": node.spec_name,
         "layer": node.layer, "kind": node.kind, "produces": list(node.produces)}))

    if con is None:
        t.missing.append("治理库不存在 ⇒ 无法取执行历史")
        return t

    # ② 执行历史
    if run_id:
        sql = ("SELECT run_id, rc, exit_code, elapsed_s, skipped, note, recorded_at, "
               "started_at, finished_at FROM run_step WHERE step=? AND run_id=? "
               "ORDER BY recorded_at DESC LIMIT 1")
        args = (step, run_id)
    else:
        sql = ("SELECT run_id, rc, exit_code, elapsed_s, skipped, note, recorded_at, "
               "started_at, finished_at FROM run_step WHERE step=? "
               "AND (rc != 0 OR exit_code NOT IN (0,'OK','SKIP')) "
               "ORDER BY recorded_at DESC LIMIT 1")
        args = (step,)
    row = con.execute(sql, args).fetchone()
    if row is None:
        t.missing.append(f"台账里 `{step}` 没有失败记录（最近失败可能已超出保留窗口，"
                         f"或该步从未失败）⇒ 无失败可取证")
        if own:
            con.close()
        return t

    t.found_failure = True
    t.run_id = str(row["run_id"] or "")
    t.recorded_at = str(row["recorded_at"] or "")
    t.exit_code = str(row["exit_code"] or "")
    t.note = str(row["note"] or "")
    t.evidence.append(Evidence(
        "run_step", True,
        f"最近失败：rc={row['rc']} exit={row['exit_code']} "
        f"耗时={row['elapsed_s']}s skipped={row['skipped']}",
        {"run_id": t.run_id, "elapsed_s": row["elapsed_s"],
         "note": t.note, "exit_code": t.exit_code}))
    if t.note:
        t.evidence.append(Evidence("note", True, "失败 note 原文：", {"note": t.note}))
    else:
        t.missing.append("该次 `note` 为空 ⇒ 无失败原因描述（须看日志 tail）")

    # ③ 时间窗（覆盖率低，只在有数据时给）
    if row["started_at"]:
        t.evidence.append(Evidence(
            "time_window", True,
            f"执行时间窗：{row['started_at']:.1f} ~ {row['finished_at']:.1f}"
            f"（{row['finished_at'] - row['started_at']:.1f}s）",
            {"started_at": row["started_at"], "finished_at": row["finished_at"]}))
    else:
        t.missing.append("该次无执行时间窗（`started_at` 为空）——第八十三批新增列，"
                         "老数据按口径不回填 ⇒ 时间窗类证据暂不可用")

    # ④ 步骤日志 tail（经统一取证入口，归一规则与写入方一致）
    try:
        from web.snapshots import latest_step_log

        tail, lp = latest_step_log(step, tail_lines=25)
        if lp:
            t.evidence.append(Evidence("step_log", True, f"日志尾部（{os.path.basename(lp)}）：",
                                       {"path": lp, "tail": tail}))
        else:
            t.missing.append(f"步骤日志目录里没有 `{step}` 的日志文件"
                             f"（命名按写入方的全字符归一规则查找）")
    except Exception as e:  # noqa: BLE001
        t.missing.append(f"步骤日志不可读：{type(e).__name__}: {str(e)[:80]}")

    # ⑤ 产出物的水位状态（依赖第 1 波补齐的 19/19 归属）
    if node.produces:
        from std_lib.common_lib import governance_store as gs

        # ⚠️ 水位键是**展开形态**（`raw:mof`），而 `produces` 是**模板形态**
        # （`raw:{src}`）——**拓扑双层标识问题在水位侧同样存在**。
        # 直查模板键恒为 None ⇒ 误报"产出物均无水位记录"。故按实例名展开后查
        # （展开规则与 `run_production_refresh.py:1301` 的 resume 判定一致）。
        src = step.split(":", 1)[1] if ":" in step else ""
        wm_parts: list[str] = []
        missing_art: list[str] = []
        for p in node.produces:
            cand = [p]
            if "{src}" in p and src:
                cand.insert(0, p.replace("{src}", src))
            for c in cand:
                w = gs.get_watermark(c) if gs.enabled() else None
                if w:
                    wm_parts.append(f"{c}@{str(w.get('version') or '?')[:12]}")
                    break
            else:
                missing_art.append(p)
        if wm_parts:
            t.evidence.append(Evidence("watermark", True,
                                       "产出物水位：" + "、".join(wm_parts),
                                       {"watermarks": wm_parts}))
        if missing_art:
            t.missing.append(
                f"产出物 {'、'.join(missing_art)} **无水位记录** ⇒ "
                f"无法判断该步产出是否已落地"
                + ("（该步超时，此项尤其关键：产物可能是残缺的）"
                   if t.exit_code == "TIMEOUT" else ""))
    else:
        t.missing.append("该步**未声明任何产物**（`StepSpec.produces` 为空）⇒ 无产物侧证据")

    # ⑥ 耗时基线对比（唯一可用的量化"是否变慢"）
    bl = _baseline_for(step)
    if bl:
        p50, p90 = float(bl.get("p50") or 0), float(bl.get("p90") or 0)
        el = float(row["elapsed_s"] or 0)
        n = int(bl.get("n") or 0)
        if n < 3:
            # 样本 <3 时 p90≈p50，"高于 p90"是**噪声**，不是信号
            verdict = f"**样本仅 n={n}，不足以做分位数对照**（本次 {el:.1f}s）"
        else:
            verdict = "持平"
            if p90 and el > p90:
                verdict = f"**显著高于 p90（{p90:.0f}s）**"
            elif p50 and el > p50 > 0:
                verdict = f"高于 p50（{p50:.0f}s）"
        t.evidence.append(Evidence(
            "duration", True,
            f"本次耗时 {el:.1f}s vs 基线 p50={p50:.1f}s / p90={p90:.1f}s ⇒ {verdict}"
            f"（基线样本 n={n}，跳过率 {bl.get('skip_rate', 0)}%）",
            {"elapsed_s": el, "p50": p50, "p90": p90, "n": n}))
    else:
        t.missing.append(f"`{step}` 不在耗时基线里（样本不足或该步极少执行）⇒ 无量化对照")

    # ⑦ 拓扑邻接
    nb = T.neighbors(step)
    up = T.upstream_chain(step)
    aff = T.affected_nodes(step)
    t.evidence.append(Evidence(
        "topology_adj", True,
        f"上游链（{len(up)} 层）：{' → '.join(up[:6]) or '（无）'}；"
        f"受影响下游（{len(aff)} 个）：{'、'.join(aff[:8]) or '（无）'}",
        {"upstream": up, "affected": aff, **nb}))

    # ── 怀疑排序（**有依据才排**，每条必须挂证据） ──────────────────────
    if t.exit_code == "TIMEOUT":
        t.suspicions.append((
            "该步撞硬超时被强杀", FAIL_MODE["TIMEOUT"]))
        # ⚠️ 上游为**空**（链首步骤，如 `collect:*`）时，该怀疑项不成立——
        #    说"上游 0 层（无）已完成"是自相矛盾。故仅在确有上游时给出。
        if node.kind == "main" and up:
            t.suspicions.append((
                "**上游产物可能已不完整**",
                f"该步 kind=main（失败即断），上游 {len(up)} 层"
                f"（{'、'.join(up[:4])}）已完成；重跑前建议先核对上游产物水位"))
        elif node.kind == "main":
            t.suspicions.append((
                "该步是**链首**（无上游）⇒ 上游侧无需排查，直接查本步自身",
                f"上游链为空；层={node.layer}"))
    if t.note and "dry-run" in t.note:
        t.suspicions.append(("记录来自 dry-run 演练", f"note 含 dry-run 标记：{t.note[:60]}"))
    if nb["produced_artifacts"] and node.produces:
        t.suspicions.append((
            "重跑该步即可修复其产出",
            f"产出物 {', '.join(node.produces)} 由本步生产"
            f"（建议 `cli.py run --resume --from {step}`）"))
    if aff:
        t.suspicions.append((
            "修复前先确认下游是否已消费残缺产物",
            f"受影响下游 {len(aff)} 个：{'、'.join(aff[:5])}"))
    if not t.suspicions:
        t.suspicions.append(("**证据不足，不给结论**", "；".join(t.missing[:3])))

    if own:
        con.close()
    return t


def recent_failures(days: int = 90, limit: int = 20,
                    con: sqlite3.Connection | None = None) -> list[dict]:
    """近 N 天失败步骤概览。"""
    own = con is None
    con = con or _open_conn()
    if con is None:
        return []
    rows = con.execute(
        "SELECT step, rc, exit_code, elapsed_s, note, recorded_at, run_id FROM run_step "
        "WHERE (rc != 0 OR exit_code NOT IN (0,'OK','SKIP')) "
        "AND recorded_at >= datetime('now', ?) ORDER BY recorded_at DESC LIMIT ?",
        (f"-{days} day", limit)).fetchall()
    out = []
    for r in rows:
        node = T.get_node(r["step"])
        out.append({"step": r["step"], "rc": r["rc"], "exit_code": r["exit_code"],
                    "elapsed_s": r["elapsed_s"], "recorded_at": r["recorded_at"],
                    "run_id": r["run_id"],
                    "spec_name": node.spec_name if node else None,
                    "matched_as": node.matched_as if node else None,
                    "note_excerpt": (r["note"] or "")[:80]})
    if own:
        con.close()
    return out


def render(t: StepTriage) -> str:
    L: list[str] = []
    L.append(f"[step-triage] {t.step}")
    if t.node:
        L.append(f"  拓扑：声明名 `{t.node.spec_name}`（匹配={t.node.matched_as}，"
                 f"层={t.node.layer}，kind={t.node.kind}，失败语义={t.node.fail_semantics}）")
    if not t.found_failure:
        L.append("  结论：**未找到该步的失败记录**")
        for m in t.missing:
            L.append(f"    ⚠ {m}")
        return "\n".join(L)

    L.append(f"  失败：run_id={t.run_id} exit={t.exit_code} at={t.recorded_at[:19]}")
    L.append(f"\n  取证（{len(t.evidence)} 项）：")
    for e in t.evidence:
        L.append(f"    · [{e.kind}] {e.detail}")
    if t.note:
        L.append(f"\n  note 原文：\n    {t.note[:400]}")
    L.append("\n  怀疑排序（**依据见括号**，非断言）：")
    for i, (item, why) in enumerate(t.suspicions, 1):
        L.append(f"    {i}. {item}\n       依据：{why}")
    if t.missing:
        L.append(f"\n  ⚠ 证据缺口（{len(t.missing)} 项 ⇒ 结论须标「待人工确认」）：")
        for m in t.missing:
            L.append(f"    · {m}")
    return "\n".join(L)


def main(argv: list[str] | None = None, con: sqlite3.Connection | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="步级失败取证（只读；怀疑排序非根因断言）")
    ap.add_argument("step", nargs="?", default="", help="步骤名（实例形态，如 clean:nfra）")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--history", type=int, default=5)
    ap.add_argument("--list-failures", action="store_true", help="近 N 天失败概览")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.list_failures or not a.step:
        rows = recent_failures(days=a.days, con=con)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return ExitCode.OK
        if not rows:
            print(f"[step-triage] 近 {a.days} 天无失败步骤")
            return ExitCode.OK
        print(f"[step-triage] 近 {a.days} 天失败步骤（{len(rows)} 条）：")
        for r in rows:
            print(f"  {r['recorded_at'][:19]}  {r['step'][:26]:26s} exit={r['exit_code']:8s} "
                  f"elapsed={r['elapsed_s']:8.1f}  ←声明 {r['spec_name'] or '?'}"
                  f"（{r['matched_as'] or '未匹配'}）")
        return ExitCode.OK

    t = triage(a.step, a.run_id, a.history, con=con)
    if a.json:
        print(json.dumps({
            "step": t.step, "found_failure": t.found_failure, "complete": t.complete,
            "run_id": t.run_id, "exit_code": t.exit_code, "note": t.note,
            "node": ({"name": t.node.name, "spec_name": t.node.spec_name,
                      "matched_as": t.node.matched_as, "layer": t.node.layer,
                      "kind": t.node.kind, "produces": list(t.node.produces)}
                     if t.node else None),
            "evidence": [{"kind": e.kind, "ok": e.ok, "detail": e.detail} for e in t.evidence],
            "missing": t.missing,
            "suspicions": [{"item": i, "basis": w} for i, w in t.suspicions],
        }, ensure_ascii=False, indent=2))
        return ExitCode.OK if t.found_failure else ExitCode.USAGE
    print(render(t))
    return ExitCode.OK if t.found_failure else ExitCode.USAGE


if __name__ == "__main__":
    raise SystemExit(main())
