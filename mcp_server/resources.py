# -*- coding: utf-8 -*-
"""mcp_server.resources — MCP **资源**面（E-4，W2 §4.3.1）。

三类消费场景（架构草图「agent 层看见处理全链」的机器可读来源）：
  1. `chain_steps`  —— 「现在该跑什么」：执行顺序 + 每步语义（派生自 `STEP_LAYERS`）；
  2. `schedule_jobs` —— 「什么时候会自己跑」：调度作业与其角色（派生自 `schedule.yaml`）；
  3. `governance_snapshot` —— 「数据新鲜度与待办」（派生自治理库 + 台账）。

**版本联动**：任一事实源变化 → 资源 `version` 递增（内容 sha 前 12 位）——agent 可据此
判断缓存是否过期；`resources/list` 的 `version` 与 `resources/read` 的必须一致（门禁守护）。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable

#: **易变字段**：它们描述"本次生成时刻"，不是"事实源状态"。
#:
#: 为什么必须排除（2026-10-21 实测的**时序 flake**）：
#: `_backlog_health()` 的 payload 里含 `generated_at = datetime.now().isoformat(
#: timespec="seconds")`。`resources/list` 与 `resources/read` 各调一次计算函数，
#: **若两次跨过秒边界**，payload 就差一个字段 ⇒ `_sha12` 不同 ⇒
#: `tests/test_mcp_server.py::test_list_and_read_versions_agree` 偶发失败
#: （实测：`assert '51d83b29df1e' == '7c02881bef71'`，uri=orch://backlog_health）。
#:
#: 语义上"版本"回答的是"**事实源**变了吗"（agent 据此判断缓存是否过期）：
#: 时间戳每次都在变，把它算进去会让版本永远不稳，缓存机制失去意义。
_VOLATILE_KEYS = frozenset({
    "generated_at", "as_of", "fetched_at", "computed_at", "now", "timestamp",
    "read_at", "updated_at",
    # `cutoff`：`backlog_health.metrics.last_30d.cutoff = now - 30d` —— 同样是
    # **派生自当前时刻**的边界值，每秒都变（实测漂移：19:13:50 → 19:13:51）。
    # 它不反映"待办数据变了"，只反映"查询发生在什么时候"⇒ 不算事实源状态。
    "cutoff",
})


def _stable_for_version(obj):
    """递归剔除易变字段，得到**只反映事实源状态**的结构（供版本哈希）。"""
    if isinstance(obj, dict):
        return {k: _stable_for_version(v) for k, v in obj.items()
                if k not in _VOLATILE_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_stable_for_version(v) for v in obj]
    return obj


def _sha12(obj) -> str:
    payload = json.dumps(_stable_for_version(obj), ensure_ascii=False,
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 资源定义与读取
# --------------------------------------------------------------------------- #


def _chain_steps() -> dict:
    """E-4 资源 1：链步骤全量（层/顺序/kind/超时/触发器/依赖/产物）。"""
    from gates.gate_config_integrity import _chain_module

    mod = _chain_module()
    if mod is None or not hasattr(mod, "step_specs"):
        return {"available": False, "steps": [], "reason": "链声明不可读"}
    steps = [
        {
            "order": i,
            "name": s.name,
            "layer": s.layer,
            "kind": s.kind,
            "fail_semantics": s.fail_semantics,
            "timeout": s.timeout,
            "trigger_id": s.trigger_id,
            "depends_on": list(s.depends_on),
            "produces": list(s.produces),
            "purpose": s.purpose,
            "degrade_note": s.degrade_note,
        }
        for i, s in enumerate(mod.step_specs(), 1)
    ]
    return {"available": True, "count": len(steps), "steps": steps}


def _schedule_jobs() -> dict:
    """E-4 资源 2：调度作业（cron 表达式/命令/角色/漏跑策略）+ 触发项。"""
    import yaml

    import paths

    p = os.path.join(paths.CONFIG_DIR, "schedule.yaml")
    with open(p, encoding="utf-8") as fh:
        sc = yaml.safe_load(fh) or {}
    jobs = [
        {
            "id": j.get("id"),
            "trigger": j.get("trigger"),
            "kind": j.get("kind"),
            "when": j.get("when"),
            "after": j.get("after"),
            "covered_by": j.get("covered_by"),
            "argv": list(j.get("argv") or []),
            "on_miss": j.get("on_miss"),
            "desc": j.get("desc"),
        }
        for j in (sc.get("jobs") or [])
    ]
    tg_p = os.path.join(paths.CONFIG_DIR, "triggers.yaml")
    triggers: list = []
    if os.path.exists(tg_p):
        with open(tg_p, encoding="utf-8") as fh:
            tg = yaml.safe_load(fh) or {}
        triggers = [
            {"id": t.get("id"), "condition": t.get("condition"), "on_fail": t.get("on_fail")}
            for t in (tg.get("triggers") or [])
        ]
    return {
        "available": True,
        "jobs": jobs,
        "triggers": triggers,
        "notify": (sc.get("notify") or {}).get("kind"),
    }


def _governance_snapshot() -> dict:
    """E-4 资源 3：治理快照（水位陈旧项 + 待办计数 + 最近运行）。"""
    from interfaces import governance_api as ga

    if not ga.enabled():
        return {"available": False, "reason": "治理库未启用"}
    st = ga.status()
    stale = [e for e in ga.edges() if str(e.get("status")) == "stale"]
    wl = 0
    try:
        from std_lib.common_lib import governance_store as gs

        wl = len(gs.worklist_list(status="open", limit=0))
    except Exception:  # noqa: BLE001  快照是观测面，局部失败不影响其余字段
        wl = -1
    return {"available": True, "status": st, "stale_edges": stale, "worklist_open": wl}


# 类型：`(描述, 读取函数)`——函数须显式标注为 Callable，否则 mypy 视为 `object` 不可调用。
def _backlog_health() -> dict:
    """待办积压健康度（诊断第 3 波；复用 tools.backlog_governance）。"""
    from tools import backlog_governance as B

    con = B._connect()
    try:
        g = B.collect_growth(con)
    finally:
        con.close()
    return {"metrics": g,
            "verdict": B.assess_growth(g, B.DEFAULT_WARN_OPEN,
                                       B.DEFAULT_WARN_NET_PER_MONTH)}


_RESOURCES: dict[str, tuple[str, Callable[[], dict]]] = {
    "chain_steps": ("链步骤全量（派生自 STEP_LAYERS；改一步 → 版本递增）", _chain_steps),
    "schedule_jobs": ("调度作业与触发项（派生自 schedule.yaml/triggers.yaml）", _schedule_jobs),
    "governance_snapshot": ("治理快照（水位陈旧/待办/台账）", _governance_snapshot),
    "backlog_health": ("待办积压健康度（增长/处置率/净增/阈值判定）", _backlog_health),
}


def list_resources() -> list[dict]:
    """`resources/list`：`uri` + `name` + `description` + `version`（内容派生）。"""
    out = []
    for uri, (desc, fn) in _RESOURCES.items():
        try:
            payload = fn()
        except Exception as e:  # noqa: BLE001  资源不可读也要在 list 中**显式**呈现
            payload = {"available": False, "error": f"{type(e).__name__}: {e}"}
        out.append(
            {
                "uri": f"orch://{uri}",
                "name": uri,
                "description": desc,
                "mimeType": "application/json",
                "version": _sha12(payload),
                "available": bool(payload.get("available", True)),
            }
        )
    return out


def read_resource(uri: str) -> dict:
    """`resources/read`：按 uri 返回内容 + 版本（版本与 list 一致——门禁守护）。"""
    name = uri.split("://", 1)[-1].strip("/")
    if name not in _RESOURCES:
        return {"error": f"未知资源：{uri}"}
    desc, fn = _RESOURCES[name]
    try:
        payload = fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "uri": uri}
    return {
        "uri": f"orch://{name}",
        "description": desc,
        "mimeType": "application/json",
        "version": _sha12(payload),
        "contents": payload,
    }


