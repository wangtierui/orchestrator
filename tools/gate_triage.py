# -*- coding: utf-8 -*-
"""tools.gate_triage — **门禁失败归因**（诊断能力第 1 波，2026-10-16）

为什么做这个（诊断最优方案第 1 波，依据实测数据条件）
--------------------------------------------------------
近 90 天门禁 FAIL **21 次**（实测），且 `gate_result.detail_json` 的 `problems`
字段已是**结构化的中文判据描述 + `文件:行号`**（FAIL 样本 95% 带 `:` 指针）——
⇒ 归因的原料齐备，不需要建监控系统，也不需要 LLM 推理。

本工具做什么
------------
输入「门禁名 + 可选 run_id」⇒ 输出：
  ① **归因类别**（产物 / 结构 / 口径 / 环境）——按门禁身份的**显式注册表**判定
     （不从problems 文本猜类别：文本会变，身份是稳定的）；
  ② **问题清单**——`detail_json.problems` **原文**（不做改写，避免丢失判据编号）；
  ③ **指针抽取**——命中的产物键（`GOV_ARTIFACTS` 19 键）与 `文件:行号`；
  ④ **重跑建议**——沿 `GOV_ARTIFACTS.produced_by` / `StepSpec.produces` 反查生产步骤。

三条硬约束（沿用方案第三章「用强约束控制幻觉」，此处按门禁场景落地）
------------------------------------------------------------------
1. **门禁名有来源**：只接受 `ALL_GATES` 登记或治理库历史出现过的门禁名；
   查不到 ⇒ 明确报"未知门禁"并列出候选，**不臆测**。
2. **无证据不出结论**：每条结论必须携带 `problems` 原文片段；
   `problems` 为空 ⇒ 只报"判据未提供证据"，**不给类别判断**。
3. **降级可见**：门禁没写 `problems`（实测 3/30：`gate_import_bootstrap` /
   `gate_rfn_drift` / `gate_runtime_hygiene`）⇒ 显式标注"该门禁不产出 problems"，
   而不是伪装成"未发现问题"。

用法
----
    python -m tools.gate_triage list                      # 最近 FAIL 概览
    python -m tools.gate_triage <gate>                    # 归因某门禁（最近一次 FAIL）
    python -m tools.gate_triage <gate> --run-id RUN-xxx    # 归因指定运行的那次
    python -m tools.gate_triage --json                    # 机器可读输出
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

TZ8 = timezone(timedelta(hours=8))

# --------------------------------------------------------------------------- #
# 归因类别注册表（**按门禁身份判定，不猜problems 文本**）
#
# 类别含义：
#   artifact 产物问题 —— 重跑生产步骤可解（产物缺失/陈旧/水位不一致）
#   structure 结构问题 —— 改代码（目录/清单/重复定义/行数/引导）
#   semantics 口径问题 —— 走 ADR 调整判据或补登记（枚举/契约/语义配置）
#   environment 环境问题 —— 修环境（依赖/锁/SBOM/凭据/磁盘）
#   review 人工复核 —— 需业务判断（如水位一致性提示项）
# 键为 `ALL_GATES` 的 `module` 值（`gates.gate_xxx`）。
# --------------------------------------------------------------------------- #
GATE_CLASS: dict[str, tuple[str, str]] = {
    # ── 产物类（artifact）：产物本身不对 ⇒ 重跑生产者可解──────────────────
    "gates.gate_run_steps": ("artifact", "失败步骤的产物未���下游采用（跑完链仍缺产物）"),
    "gates.gate_watermark": ("artifact", "产物水位与声明不一致"),
    "gates.gate_relations": ("artifact", "依据/废止关系产物陈旧或键集/强引用不齐"),
    "gates.gate_rfn_drift": ("artifact", "RFN↔clean 漂移未处置"),
    "gates.gate_timeliness_ssot": ("artifact", "时效状态在多份产物间不同步"),
    "gates.gate_clean_schema": ("artifact", "清洗校验失败的记录仍进入交付"),
    "gates.gate_contract": ("artifact", "产物列头/键集偏离数据契约"),
    "gates.gate_original_resolvable": ("artifact", "内部制度索引与原件库对不上"),
    "gates.gate_provenance": ("artifact", "血缘 provenance 未覆盖"),
    "gates.gate_citations": ("artifact", "制度引用的原件/条款解析不到"),
    # ── 结构类（structure）：代码或仓结构不对 ⇒ 改代码────────────────────
    "gates.gate_flat_layout": ("structure", "目录拍平（data/docs 出现子目录）"),
    "gates.gate_no_duplicate_libs": ("structure", "重复工具或重名再定义"),
    "gates.gate_hardcoded_snapshots": ("structure", "硬编码按日快照文件名（跨日炸弹）"),
    "gates.gate_hardcoded_paths": ("structure", "盘符字面量"),
    "gates.gate_import_bootstrap": ("structure", "自写模块路径引导（应走唯一 bootstrap）"),
    "gates.gate_runtime_hygiene": ("structure", "退出码/静默异常/接口空壳"),
    "gates.gate_no_cross_module_import": ("structure", "跨模块直连（越权引导或裸 import）"),
    "gates.gate_config_integrity": ("structure", "配置与清单不一致（tools 清单/结构清单/行数预算）"),
    # ── 口径类（semantics）：判据或登记要调整 ⇒ 补登记或走 ADR────────────
    "gates.gate_enum_values": ("semantics", "受控枚举定义与使用不一致"),
    "gates.gate_field_aliases": ("semantics", "中文字段名未走受控注册"),
    "gates.gate_rfn_sync": ("semantics", "RFN 多文件间记录不一致"),
    "gates.gate_sources_config": ("semantics", "源目录配置与实际不符"),
    "gates.gate_mcp_schema": ("semantics", "MCP schema 与声明面不一致"),
    "gates.gate_reg_frameworks": ("semantics", "监管体系登记/受控域/置信纪律/映射未闭合"),
    "gates.gate_implementation_audit": ("semantics", "落地执行检视的规则表/登记/差距未闭合"),
    # ── 环境类（environment）：环境或供应链不对 ⇒ 修环境──────────────────
    "gates.gate_dependency_audit": ("environment", "锁文件/影子依赖/CVE/SBOM 不一致"),
    "gates.gate_secret_scan": ("environment", "密钥或敏感值硬编码"),
    # ── 复核类（review）：提示型判据 ⇒ 人工判断──────────────────────────
    "gates.gate_dynamic_step_coverage": ("review", "动态模板步骤的运行时覆盖情况"),
    "gates.gate_condition_observers": ("review", "条件触发观察对象的闭合/可达/同源"),
    "gates.gate_declaration_executor": ("review", "声明的事件承接/告警/resume/manifest 归属"),
}

#: 类别 → 建议动作（文案面向值班/合规工程师，不代劳执行）
ACTION_BY_CLASS: dict[str, str] = {
    "artifact": "**重跑生产者**：先按下方「重跑建议」定位步骤，再 `cli.py run --resume --from <step>`；"
                "若重跑后仍 FAIL，则是产物侧代码问题而非数据问题。",
    "structure": "**改代码**：按 problems 的 `文件:行号` 直接定位；这类问题重跑无效。",
    "semantics": "**走判据流程**：确认是「登记缺失」还是「判据过严」——前者补登记，后者提 ADR "
                 "调整判据（不要为了让门禁变绿而放宽判据）。",
    "environment": "**修环境**：按 problems 提示执行对应命令（如 `python -m tools.gen_lock`），"
                   "不要改门禁。",
    "review": "**人工复核**：本门禁是提示型判据，需业务判断；确认后可记入 worklist 留痕。",
}

#: `文件:行号` 指针（Windows 盘符路径也算）
_PTR = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:py|json|ya?ml|md|csv|jsonl|txt))(?::(\d+))?")


@dataclass(frozen=True)
class Problem:
    """单条判据问题（**原文保留**，不���写）。"""

    text: str
    files: tuple[str, ...] = ()
    lines: tuple[int, ...] = ()

    @property
    def evidence(self) -> str:
        return self.text


@dataclass
class Triage:
    gate: str
    found: bool
    category: str = ""
    category_desc: str = ""
    category_hint: str = ""   #: 无证据时的「若补齐证据则属哪类」（仅供人工参考）
    action: str = ""
    problems: list[Problem] = field(default_factory=list)
    artifacts: tuple[str, ...] = ()
    rerun_steps: tuple[str, ...] = ()
    artifact_modules: tuple[str, ...] = ()
    evidence_gaps: list[str] = field(default_factory=list)
    run_id: str = ""
    recorded_at: str = ""
    descr: str = ""
    detail_keys: tuple[str, ...] = ()

    @property
    def conclusion_ready(self) -> bool:
        """结论是否**可给出**：必须有 problems 证据（硬约束 2）。"""
        return bool(self.problems)


# --------------------------------------------------------------------------- #
# 数据接入（连接可注入 ⇒ 口径可测）
# --------------------------------------------------------------------------- #
def _open_conn() -> sqlite3.Connection | None:
    db = os.path.join(paths.DATA_DIR, "governance.db")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def known_gates(con: sqlite3.Connection) -> list[str]:
    """已知门禁名：`ALL_GATES` 登记 ∪ 治理库历史出现（硬约束 1：必须有来源）。"""
    names: set[str] = set(GATE_CLASS)
    try:
        import gates as G

        for g in getattr(G, "ALL_GATES", []) or []:
            if isinstance(g, dict) and g.get("module"):
                names.add(str(g["module"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        for (g,) in con.execute("SELECT DISTINCT gate FROM gate_result"):
            names.add(str(g))
    except sqlite3.Error:
        pass
    return sorted(names)


def _artifact_keys() -> dict[str, tuple[str, str]]:
    """产物键 → `(生产步骤名, 生产者模块路径)`。

    ⚠️ **两者语义不同，不可混用**：
      · `StepSpec.produces` 里声明的**步骤名**（如 `relations:gen`）才是
        `cli.py run --from` 接受的值；
      · `GOV_ARTIFACTS.produced_by` 是**模块路径**（如 `tools.extract_relations`），
        **不能当 `--from` 用**（会产出跑不起来的命令）。
    实测：19 个产物中只有 11 个在 `StepSpec.produces` 声明；其余 8 个只有模块路径
    ⇒ 对这 8 个**不给命令**，只报模块路径并标注步骤归属待确认（不臆造映射）。
    """
    try:
        from tools.run_production_refresh import GOV_ARTIFACTS, step_specs

        by_step = {p: s.name for s in step_specs() for p in s.produces}
        out: dict[str, tuple[str, str]] = {}
        for k, v in GOV_ARTIFACTS.items():
            producer = (v or {}).get("produced_by", "") if isinstance(v, dict) else ""
            out[k] = (by_step.get(k, ""), producer or "")
        return out
    except Exception:  # noqa: BLE001
        return {}


def _problems_of(detail: dict) -> list[Problem]:
    raw = detail.get("problems")
    if not isinstance(raw, list) or not raw:
        return []
    out: list[Problem] = []
    for item in raw:
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        files: list[str] = []
        lines: list[int] = []
        for m in _PTR.finditer(text):
            files.append(m.group(1))
            if m.group(2):
                lines.append(int(m.group(2)))
        out.append(Problem(text=text, files=tuple(files), lines=tuple(lines)))
    return out


def triage(gate: str, run_id: str = "", con: sqlite3.Connection | None = None) -> Triage:
    """门禁归因主入口（**只读**）。"""
    own = con is None
    con = con or _open_conn()
    if con is None:
        return Triage(gate=gate, found=False, evidence_gaps=["治理库不存在"])

    known = known_gates(con)
    # 允许短名（`gate_relations` ↔ `gates.gate_relations`）
    resolved = gate
    if gate not in known:
        cand = [k for k in known if k.split(".")[-1] == gate]
        if len(cand) == 1:
            resolved = cand[0]
    if resolved not in known:
        t = Triage(gate=gate, found=False,
                   evidence_gaps=[f"未知门禁（不在 ALL_GATES 与历史记录中）；"
                                  f"候选前 8：{known[:8]}"])
        if own:
            con.close()
        return t

    # 取最近一次 FAIL（指定 run_id 则取该运行的）
    if run_id:
        sql = ("SELECT run_id, gate, descr, detail_json, recorded_at FROM gate_result "
               "WHERE gate=? AND run_id=? ORDER BY recorded_at DESC LIMIT 1")
        args = (resolved, run_id)
    else:
        sql = ("SELECT run_id, gate, descr, detail_json, recorded_at FROM gate_result "
               "WHERE gate=? AND passed=0 ORDER BY recorded_at DESC LIMIT 1")
        args = (resolved,)
    row = con.execute(sql, args).fetchone()
    if row is None:
        t = Triage(gate=resolved, found=False,
                   evidence_gaps=["未找到 FAIL 记录（该门禁在台账里没有失败历史，"
                                  "或指定 run_id 下没有该门禁结果）"])
        if own:
            con.close()
        return t

    try:
        detail = json.loads(row["detail_json"] or "{}")
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}

    probs = _problems_of(detail)
    art_map = _artifact_keys()
    detail_raw = row["detail_json"] or ""
    hit_arts = sorted({a for a in art_map if a in detail_raw})
    #: 命中产物 → 生产步骤（**仅有事实源声明的才算**）
    step_of_arts = {a: art_map[a][0] for a in hit_arts if art_map.get(a, ("", ""))[0]}
    #: 命中产物 → 生产者模块路径（仅供人工定位，**不是 --from 的值**）
    module_of_arts = {a: art_map[a][1] for a in hit_arts if art_map.get(a, ("", ""))[1]}
    #: 有产物但**无步骤归属**（`StepSpec.produces` 未声明）
    no_step_arts = [a for a in hit_arts if not art_map.get(a, ("", ""))[0]]
    files: list[str] = []
    for p in probs:
        for f in p.files:
            if f not in files:
                files.append(f)

    cat, cat_desc = GATE_CLASS.get(resolved, ("", ""))
    if not cat:
        cat, cat_desc = "", f"该门禁未登记归因类别（新增门禁请补 `GATE_CLASS`：{resolved}）"

    gaps: list[str] = []
    hint = ""
    if not probs:
        # ⚠️ 无证据 ⇒ **不给类别**（否则报告自我否定：一边给结论一边说"不给结论"）。
        # 类别仍记在 `hint` 里供人工参考，但 `category` 留空 ⇒ 下游据此判定"结论未就绪"。
        hint = cat_desc
        cat, cat_desc = "", ""
        gaps.append("该门禁本次未产出 `problems` ⇒ 判据未提供可引用的证据，"
                    "**故不给归因类别**（实测 3/30 门禁属此类："
                    "`gate_import_bootstrap` / `gate_rfn_drift` / `gate_runtime_hygiene`）")

    rerun = tuple(dict.fromkeys(step_of_arts.values()))
    if no_step_arts:
        gaps.append(
            f"产物 {'、'.join(no_step_arts)} 在 `StepSpec.produces` **未声明生产步骤** ⇒ "
            f"不给 `--from` 命令（`produced_by` 只是模块路径，不是步骤名）；"
            f"生产模块：{'、'.join(module_of_arts.get(a, '?') for a in no_step_arts)}"
            "　※这是**治理缺口**（血缘声明面与链声明面未对齐），不是本工具可推断的")
    if cat == "artifact" and not rerun and hit_arts and not no_step_arts:
        gaps.append("命中产物但未能反查到生产步骤 ⇒ 重跑入口待人工确认")

    t = Triage(
        gate=resolved, found=True, category=cat, category_desc=cat_desc,
        action=ACTION_BY_CLASS.get(cat, ""), problems=probs, category_hint=hint,
        artifacts=tuple(hit_arts), rerun_steps=rerun,
        artifact_modules=tuple(dict.fromkeys(module_of_arts.values())),
        evidence_gaps=gaps, run_id=str(row["run_id"] or ""),
        recorded_at=str(row["recorded_at"] or ""), descr=str(row["descr"] or ""),
        detail_keys=tuple(sorted(detail.keys())),
    )
    if own:
        con.close()
    return t


def recent_failures(con: sqlite3.Connection | None = None, days: int = 90,
                    limit: int = 20) -> list[dict]:
    """近 N 天的门禁 FAIL 概览（供 `list` 子命令）。"""
    own = con is None
    con = con or _open_conn()
    if con is None:
        return []
    sql = ("SELECT gate, run_id, descr, recorded_at, detail_json FROM gate_result "
           "WHERE passed=0 AND recorded_at >= datetime('now', ?) "
           "ORDER BY recorded_at DESC LIMIT ?")
    out: list[dict] = []
    for r in con.execute(sql, (f"-{days} day", limit)):
        try:
            d = json.loads(r["detail_json"] or "{}")
        except ValueError:
            d = {}
        n = len(d.get("problems") or []) if isinstance(d, dict) else 0
        out.append({"gate": r["gate"], "run_id": r["run_id"], "descr": r["descr"],
                    "recorded_at": r["recorded_at"], "problems": n,
                    "has_problems": bool(n)})
    if own:
        con.close()
    return out


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def render(t: Triage) -> str:
    """人读报告（**每条结论都带problems 原文**）。"""
    L: list[str] = []
    if not t.found:
        L.append(f"[triage] {t.gate}：未找到可归因的记录")
        for g in t.evidence_gaps:
            L.append(f"          · {g}")
        return "\n".join(L)

    L.append(f"[triage] {t.gate}")
    L.append(f"  判据：{t.descr}")
    L.append(f"  记录：run_id={t.run_id}  recorded_at={t.recorded_at}")
    if t.category:
        L.append(f"  归因类别：**{t.category}** — {t.category_desc}")
    elif t.category_hint:
        L.append(f"  归因类别：（**未给出** — 证据不足；该门禁属`{t.category_hint}` 类，"
                 f"但无 problems 可引用）")
    else:
        L.append("  归因类别：（未给出）")
    if t.action:
        L.append(f"  建议动作：{t.action}")

    if t.problems:
        L.append(f"\n  判据证据（detail_json.problems 原文，{len(t.problems)} 条）：")
        for i, p in enumerate(t.problems, 1):
            L.append(f"    {i}. {p.text}")
            if p.files:
                loc = ", ".join(f"{f}:{ln}" if ln else f for f, ln in
                                zip(p.files, list(p.lines) + [None] * len(p.files)))
                L.append(f"       位置：{loc}")
    else:
        L.append("\n  判据证据：（无 problems）")

    if t.artifacts:
        L.append(f"\n  命中产物键：{', '.join(t.artifacts)}")
    if t.rerun_steps:
        L.append(f"  重跑建议：`cli.py run --resume --from {t.rerun_steps[0]}`"
                 f"（生产步骤：{', '.join(t.rerun_steps)}）")
    if t.artifact_modules and not t.rerun_steps:
        L.append(f"  生产模块（**不是 `--from` 的值**，需人工定位步骤）："
                 f"{', '.join(t.artifact_modules)}")
    if t.evidence_gaps:
        L.append("\n  ⚠ 证据缺口：")
        for g in t.evidence_gaps:
            L.append(f"    · {g}")
    return "\n".join(L)


def main(argv: list[str] | None = None, con: sqlite3.Connection | None = None) -> int:
    """CLI 入口。

    `con` 可注入（单元测试用内存库）⇒ **输出结构可被验证**，而不必依赖真库状态。
    运行期不传 ⇒ 打开治理库只读连接。
    """
    import argparse

    ap = argparse.ArgumentParser(
        description="门禁失败归因（只读；每条结论携带判据原文）")
    ap.add_argument("gate", nargs="?", default="", help="门禁名（可短名）")
    ap.add_argument("--run-id", default="", help="指定运行的该次结果")
    ap.add_argument("--list", action="store_true", help="列出近 N 天 FAIL 概览")
    ap.add_argument("--days", type=int, default=90, help="--list 的窗口（默认 90）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args(argv)

    if a.list or not a.gate:
        rows = recent_failures(days=a.days, con=con)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return ExitCode.OK
        if not rows:
            print(f"[triage] 近 {a.days} 天无门禁 FAIL")
            return ExitCode.OK
        print(f"[triage] 近 {a.days} 天门禁 FAIL 概览（{len(rows)} 条）：")
        for r in rows:
            flag = f"problems×{r['problems']}" if r["has_problems"] else "无 problems"
            print(f"  {r['recorded_at'][:19]}  {r['gate']:42s} {flag}")
        return ExitCode.OK

    t = triage(a.gate, a.run_id, con=con)
    if a.json:
        print(json.dumps({
            "gate": t.gate, "found": t.found, "category": t.category,
            "category_desc": t.category_desc, "action": t.action,
            "run_id": t.run_id, "recorded_at": t.recorded_at, "descr": t.descr,
            "conclusion_ready": t.conclusion_ready,
            "problems": [{"text": p.text, "files": list(p.files)} for p in t.problems],
            "artifacts": list(t.artifacts), "rerun_steps": list(t.rerun_steps),
            "artifact_modules": list(t.artifact_modules),
            "evidence_gaps": t.evidence_gaps,
        }, ensure_ascii=False, indent=2))
        return ExitCode.OK if t.found else ExitCode.USAGE
    print(render(t))
    return ExitCode.OK if t.found else ExitCode.USAGE


if __name__ == "__main__":
    raise SystemExit(main())
