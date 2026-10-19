# -*- coding: utf-8 -*-
"""mcp_server.tools — 首批 tool 面（**读先行、写后置**，W2 §4.3）。

纪律：
  · **全部只读消费既有门面**（`interfaces/*_api`）——不新增查询路径、不绕过 SSOT；
  · 不含任何写工具：写能力须逐个论证（`access="write"` + `side_effects` + 强制审计），
    本期**零写工具**（宁少勿滥——写工具是 agent 侧的破坏面）；
  · 每个 tool 的语义来自既有 API，schema 由 `registry` 自动派生（无手写副本）。
"""
from __future__ import annotations

from mcp_server.registry import tool

# --------------------------------------------------------------------------- #
# 链与调度（E-4 资源的数据源同源，见 resources.py）
# --------------------------------------------------------------------------- #


@tool(tags=("chain",))
def chain_status() -> dict:
    """当前链状态：最近一次运行的时间/耗时/成功失败步数 + 门禁通过率（治理库 + 台账）。"""
    import json
    import os

    import paths

    out: dict = {"run": None, "gates": None}
    led = os.path.join(paths.DATA_DIR, "run_state", "last_run_steps.json")
    if os.path.exists(led):
        try:
            with open(led, encoding="utf-8") as fh:
                d = json.load(fh)
            steps = d.get("steps") or []
            out["run"] = {
                "run_at": d.get("run_at"),
                "total_elapsed_s": d.get("total_elapsed_s"),
                "steps": len(steps),
                "failed": [str(s.get("step")) for s in steps
                           if int(s.get("rc") or 0) != 0],
                "degraded": [str(s.get("step")) for s in steps if s.get("degraded")],
            }
        except (OSError, ValueError):
            out["run"] = {"error": "台账不可解析"}
    from interfaces import governance_api as ga

    if ga.enabled():
        res = ga.gate_results(limit=200)
        if res:
            last = res[0].get("run_id")
            same = [r for r in res if r.get("run_id") == last]
            passed = sum(1 for r in same if int(r.get("passed") or 0) == 1)
            out["gates"] = {"run_id": last, "total": len(same), "passed": passed}
    return out


@tool(tags=("governance",))
def watermarks(limit: int = 50) -> list:
    """产物水位全量（治理库治理面）：版本/轮次/阶段——判断数据新鲜度的机器入口。"""
    from interfaces import governance_api as ga

    return ga.watermarks()[: max(1, int(limit))]


@tool(tags=("governance",))
def dependency_edges(status: str = "") -> list:
    """水位依赖边（ok / ok_within_round / stale / unregistered）——血缘与陈旧诊断。"""
    from interfaces import governance_api as ga

    edges = ga.edges()
    if status:
        edges = [e for e in edges if str(e.get("status")) == status]
    return edges


@tool(tags=("governance",))
def gate_results(limit: int = 20) -> list:
    """门禁历史结果（按 run_id 归档）——「为什么红了」的证据面。"""
    from interfaces import governance_api as ga

    return ga.gate_results(limit=max(1, int(limit)))


@tool(tags=("governance",))
def audit_log(limit: int = 20) -> list:
    """审计日志（append-only）：谁在何时对治理库做了什么（含 MCP 写级调用留痕）。"""
    from interfaces import governance_api as ga

    return ga.audit(limit=max(1, int(limit)))


# --------------------------------------------------------------------------- #
# 待办队列（worklist）
# --------------------------------------------------------------------------- #


@tool(tags=("worklist",))
def worklist_open(limit: int = 20) -> list:
    """未处置待办队列（open）：kind/主题/建议/置信度/年龄——agent 决策的输入面。"""
    from std_lib.common_lib import governance_store as gs

    if not gs.enabled():
        return []
    return gs.worklist_list(status="open", limit=max(1, int(limit)))


@tool(tags=("worklist",))
def worklist_stats() -> dict:
    """待办统计：按 kind/status 计数 + 最老未处置天数（治理健康度）。"""
    import collections

    from std_lib.common_lib import governance_store as gs

    if not gs.enabled():
        return {"enabled": False}
    rows = gs.worklist_list(status="open", limit=0)
    by_kind = collections.Counter(str(r.get("kind")) for r in rows)
    return {"enabled": True, "open": len(rows), "by_kind": dict(by_kind)}


# --------------------------------------------------------------------------- #
# 数据资产（双底座检索——只读）
# --------------------------------------------------------------------------- #


@tool(tags=("data",))
def base_search(text: str, base: str = "external", limit: int = 10) -> list:
    """双底座全文检索（FTS5）：按关键词查监管记录或内部制度，返回精简字段。"""
    from interfaces import base_api as ba

    if base == "internal":
        return ba.search_internal(text, limit=max(1, int(limit)))
    return ba.search_external(text, limit=max(1, int(limit)))


@tool(tags=("data",))
def base_record(record_id: str) -> dict:
    """按 record_id 取单条外部监管记录（含正文）——检索结果的详情面。"""
    from interfaces import base_api as ba

    r = ba.get_external(record_id)
    if not r:
        return {"found": False, "record_id": record_id}
    return {"found": True, "record": r}


@tool(tags=("data",))
def timeliness_summary() -> dict:
    """时效核验三态汇总（success/partial/unavailable）+ 最近核验时间——R13 口径。"""
    import glob
    import json
    import os

    import paths

    d = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "timeliness_review")
    cands = sorted(glob.glob(os.path.join(d, "verify_summary_*.json")))
    if not cands:
        return {"found": False}
    try:
        with open(cands[-1], encoding="utf-8") as fh:
            return {"found": True, "latest": os.path.basename(cands[-1]), **json.load(fh)}
    except (OSError, ValueError):
        return {"found": False, "error": "摘要不可解析"}


# --------------------------------------------------------------------------- #
# 配置事实源（只读回显，供 agent 自证「事实源在哪」）
# --------------------------------------------------------------------------- #


@tool(tags=("config",))
def sources_list() -> list:
    """源清单（config/sources.yaml 唯一事实源）：源 id/采集器/启用状态。"""
    from config.loader import load_sources

    return [
        {"id": sid, "enabled": bool(cfg.get("enabled", True)),
         "collector": cfg.get("collector") or cfg.get("collect_cmd")}
        for sid, cfg in sorted(load_sources().items())
    ]


@tool(tags=("diagnosis",))
def diagnose_gate(module: str = "") -> dict:
    """门禁失败归因（诊断第 1 波；复用 tools.gate_triage，不复制判据）。

    `module` 留空 ⇒ 近 90 天失败清单；给出 `gates.gate_xxx` ⇒ 归因结果。
    **无证据不出结论**：`problems` 为空时 `category` 留空、只给 `category_hint`。
    """
    from tools import gate_triage as T

    con = T._open_conn()
    if con is None:
        return {"available": False, "note": "治理库不可读"}
    try:
        if not module:
            rows = T.recent_failures(con=con)
            out = []
            for r in rows:
                keys = r.keys() if hasattr(r, "keys") else []
                out.append({k: (r[k] if k in keys else "")
                            for k in ("gate", "run_id", "recorded_at", "descr")})
            return {"available": True, "count": len(out), "recent_failures": out}
        tr = T.triage(module, con=con)
        return {
            "available": True,
            "found": tr.found,
            "gate": tr.gate,
            "category": tr.category,
            "category_desc": tr.category_desc,
            "category_hint": tr.category_hint,
            "action": tr.action,
            "evidence": [p.evidence for p in tr.problems],
            "problem_count": len(tr.problems),
            "artifacts": list(tr.artifacts),
            "rerun_steps": list(tr.rerun_steps),
            "evidence_gaps": list(tr.evidence_gaps),
            "conclusion_ready": tr.conclusion_ready,
            "rendered": T.render(tr),
        }
    finally:
        con.close()


@tool(tags=("diagnosis",))
def diagnose_step(step: str, run_id: str = "") -> dict:
    """步级失败取证（诊断第 2 波；复用 tools.step_triage）。

    含**拓扑双层标识**：台账里是展开实例（`clean:nfra`），声明面可能是模板
    （`clean:{src}`）⇒ `matched_as` 如实说明匹配到哪一层。
    """
    from tools import step_triage as S

    con = S._open_conn()
    if con is None:
        return {"available": False, "note": "治理库不可读"}
    try:
        tr = S.triage(step, run_id=run_id, con=con)
        node = tr.node
        return {
            "available": True,
            "step": tr.step,
            "found_failure": tr.found_failure,
            "matched_as": getattr(node, "matched_as", "") if node else "",
            "spec_step": getattr(node, "spec_step", "") if node else "",
            "evidence": [{"kind": e.kind, "ok": e.ok,
                          "detail": e.detail, "data": e.data} for e in tr.evidence],
            "suspicions": [{"claim": c, "basis": b} for c, b in tr.suspicions],
            "missing": list(tr.missing),
            "run_id": tr.run_id,
            "recorded_at": tr.recorded_at,
            "rendered": S.render(tr),
        }
    finally:
        con.close()


@tool(tags=("config",))
def step_layers() -> list:
    """链分层声明（STEP_LAYERS 派生）：层/步骤/kind/超时/产物——「现在该跑什么」。"""
    from gates.gate_config_integrity import _chain_module

    mod = _chain_module()
    if mod is None or not hasattr(mod, "step_specs"):
        return []
    return [
        {"name": s.name, "layer": s.layer, "kind": s.kind, "fail_semantics": s.fail_semantics,
         "timeout": s.timeout, "trigger_id": s.trigger_id, "depends_on": list(s.depends_on),
         "produces": list(s.produces), "purpose": s.purpose}
        for s in mod.step_specs()
    ]
