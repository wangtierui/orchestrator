# -*- coding: utf-8 -*-
"""web.snapshots — **卡片数据层**（每个数据源一个 provider，全部带降级）

为什么有这一层：页面与 API 不直接取数（§8.2.2 的「不裸读」纪律），而是经本层：
  · 唯一取数口（interfaces/ 门面 + governance_store），便于审计"谁读了什么"；
  · **降级收口**：每个 provider 捕获自身异常 → 返回 `{"available": False, "reason": ...}`，
    页面据此显示「不可用 + 原因」而**不白屏**（旁路纪律）；
  · **快照缓存**：DB/文件读做 2s 防抖（§8.3 架构），避免多卡片各自打库。

每个 provider 返回统一形状：
    {"available": bool, "source": str, "fetched_at": str, ...payload}
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from bootstrap import bootstrap

bootstrap("all")

import paths

TZ8 = timezone(timedelta(hours=8))
CACHE_TTL_S = 2.0  # §8.3：2s 服务端 diff / 防抖 DB 读


def _now() -> str:
    return datetime.now(TZ8).replace(microsecond=0).isoformat()


class _Cache:
    """极简 TTL 缓存（线程安全）。缓存的是**派生结果**，不缓存异常。"""

    def __init__(self, ttl: float = CACHE_TTL_S) -> None:
        self._ttl = ttl
        self._d: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get_or_call(self, key: str, fn):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._d[key] = (now, val)
        return val

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = _Cache()


def _degrade(source: str, exc: BaseException) -> dict:
    return {"available": False, "source": source, "fetched_at": _now(),
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


# --------------------------------------------------------------------------- #
# P0 总览
# --------------------------------------------------------------------------- #
def run_health() -> dict:
    """运行健康卡：最近一次运行的步骤统计（治理库台账，旁路观测设施）。"""
    try:
        def _load():
            from std_lib.common_lib import governance_store as gs

            run_id, steps = gs.last_run_with_steps()
            if not run_id:
                return {"available": False, "source": "governance_store.last_run_with_steps",
                        "reason": "无运行台账（尚未跑过链？）", "fetched_at": _now()}
            counts: dict[str, int] = {}
            total_s = 0.0
            for row in steps:
                key = str(row.get("status") or "unknown")
                counts[key] = counts.get(key, 0) + 1
                for k in ("elapsed_s", "duration_s", "sec"):
                    if row.get(k) is not None:
                        total_s += float(row[k])
                        break
            started = [str(x.get("started_at")) for x in steps if x.get("started_at")]
            finished = [str(x.get("finished_at") or x.get("ended_at")) for x in steps
                        if x.get("finished_at") or x.get("ended_at")]
            return {"available": True, "source": "governance_store.last_run_with_steps",
                    "fetched_at": _now(), "run_id": run_id, "steps": len(steps),
                    "counts": counts, "total_elapsed_s": round(total_s, 1),
                    "run_at": max(started) if started else "",
                    "last_finished_at": max(finished) if finished else "",
                    "step_statuses": [{"step": x.get("step"), "status": x.get("status"),
                                       "rc": x.get("exit_code") or x.get("rc")}
                                      for x in steps]}
        return CACHE.get_or_call("run_health", _load)
    except Exception as e:  # noqa: BLE001 旁路：绝不让卡片异常冒到页面
        return _degrade("governance_store", e)


def gate_status() -> dict:
    """门禁状态卡：最近一次 gates 结果逐道通过情况。"""
    try:
        def _load():
            from interfaces import governance_api

            if not governance_api.enabled():
                return {"available": False, "source": "governance_api.gate_results",
                        "reason": "治理库未启用", "fetched_at": _now()}
            rows = governance_api.gate_results(limit=400)
            if not rows:
                return {"available": False, "source": "governance_api.gate_results",
                        "reason": "无门禁记录", "fetched_at": _now()}
            latest_run = str(rows[0].get("run_id") or "")
            same = [r for r in rows if str(r.get("run_id") or "") == latest_run]
            failed = [{"gate": r.get("gate"), "descr": r.get("descr")}
                      for r in same if not r.get("passed")]
            return {"available": True, "source": "governance_api.gate_results",
                    "fetched_at": _now(), "run_id": latest_run,
                    "total": len(same), "passed": len(same) - len(failed),
                    "failed": failed[:12],
                    "all_gates_hint": "门禁总数以 gates.ALL_GATES 为准（页面顶部展示）"}
        return CACHE.get_or_call("gate_status", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("governance_api", e)


def worklist_summary() -> dict:
    """待办卡：open 总数 + kind 分布 + 最老未处置天数。"""
    try:
        def _load():
            from std_lib.common_lib import governance_store as gs

            stats = gs.worklist_stats()
            items = gs.worklist_list(status="open", limit=0)
            by_kind: dict[str, int] = {}
            for it in items:
                k = str(it.get("kind") or "?")
                by_kind[k] = by_kind.get(k, 0) + 1
            return {"available": bool(stats.get("enabled")), "source": "governance_store.worklist",
                    "fetched_at": _now(), "stats": stats, "open_total": len(items),
                    "by_kind": by_kind,
                    "oldest_open_days": stats.get("oldest_open_days")}
        return CACHE.get_or_call("worklist_summary", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("governance_store.worklist", e)


def alerts(limit: int = 10) -> dict:
    """告警卡：最近 N 条 notify 落盘告警（reports/_alerts/*.json）。"""
    try:
        def _load():
            d = os.path.join(paths.REPORTS_DIR, "_alerts")
            rows: list[dict] = []
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d), reverse=True)[:limit * 3]:
                    if not fn.endswith(".json"):
                        continue
                    try:
                        obj = json.loads(open(os.path.join(d, fn), encoding="utf-8").read())
                    except (OSError, ValueError):
                        continue
                    rows.append(obj if isinstance(obj, dict) else {"event": str(obj)[:120]})
                    if len(rows) >= limit:
                        break
            return {"available": True, "source": "reports/_alerts/*.json",
                    "fetched_at": _now(), "count": len(rows), "alerts": rows}
        return CACHE.get_or_call("alerts", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("reports/_alerts", e)


def freshness() -> dict:
    """数据新鲜度卡：水位边状态统计 + stale 明细（附「重跑哪步」指引）。"""
    try:
        def _load():
            from interfaces import governance_api

            if not governance_api.enabled():
                return {"available": False, "source": "governance_api.edges",
                        "reason": "治理库未启用", "fetched_at": _now()}
            edges = governance_api.edges()
            by_status: dict[str, int] = {}
            stale: list[dict] = []
            for e in edges:
                st = str(e.get("status") or "?")
                by_status[st] = by_status.get(st, 0) + 1
                if st == "stale":
                    stale.append({"artifact": e.get("artifact"), "dep": e.get("dep"),
                                  "declared": e.get("declared_version"),
                                  "current": e.get("current_version")})
            return {"available": True, "source": "governance_api.edges",
                    "fetched_at": _now(), "by_status": by_status,
                    "stale_count": by_status.get("stale", 0), "stale": stale[:20],
                    "watermarks": governance_api.watermarks()}
        return CACHE.get_or_call("freshness", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("governance_api.edges", e)


def storage_usage() -> dict:
    """存储占用条：各资产实测体积（目录扫描，只读）。"""
    try:
        def _load():
            targets = {
                "raw": os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "raw"),
                "cleaned": os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned"),
                "clauses": os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "clauses"),
                "external_models": paths.EXTERNAL_DIR,
                "governance_db": os.path.join(paths.DATA_DIR, "governance.db"),
            }
            out: dict[str, int] = {}
            for name, p in targets.items():
                if os.path.isfile(p):
                    out[name] = os.path.getsize(p)
                elif os.path.isdir(p):
                    total = 0
                    for dirpath, dirnames, filenames in os.walk(p):
                        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                        for fn in filenames:
                            fp = os.path.join(dirpath, fn)
                            try:
                                total += os.path.getsize(fp)
                            except OSError:
                                pass
                    out[name] = total
            total_all = sum(out.values()) or 1
            return {"available": True, "source": "目录扫描（paths 常量）", "fetched_at": _now(),
                    "bytes": out,
                    "ratio": {k: round(100.0 * v / total_all, 2) for k, v in out.items()},
                    "total_bytes": total_all}
        return CACHE.get_or_call("storage_usage", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("paths 目录扫描", e)


def overview() -> dict:
    """P0 总览聚合。"""
    return {"available": True, "fetched_at": _now(), "source": "web.snapshots 多卡片聚合",
            "run_health": run_health(), "gate_status": gate_status(),
            "worklist": worklist_summary(), "alerts": alerts(),
            "freshness": freshness(), "storage": storage_usage()}


# --------------------------------------------------------------------------- #
# P1 数据流转（桑基图拓扑 + 边宽）
# --------------------------------------------------------------------------- #
def flow_graph() -> dict:
    """数据流转图：节点/边来自 STEP_ORDER + sources.yaml + triggers.yaml（前端零硬编码）。

    边宽 = 该步骤处理的**产物记录数**（来源：**产物侧权威** `clean_index_api.record_count`）。

    ⚠️ 2026-10-16 修正：此前读台账 `run_step` 的 `raw_size`/`record_count` 等列，
    而该表**根本没有这些列**（实测列：run_id/step/rc/exit_code/elapsed_s/skipped/
    note/recorded_at/started_at/finished_at/is_dry_run）⇒ `weights` 恒为空
    ⇒ **桑基图不画任何边**（用户报"P1 未呈现数据流转"）。
    依据 ADR-0014 的 C3 决策「产物侧才是权威，不扩 record_count 列」，改由
    `interfaces/clean_index_api.record_count(src)` 取（实测 gov=13178/nfra=1934）。
    """
    try:
        def _load():
            import importlib

            chain = importlib.import_module("tools.run_production_refresh")
            steps = list(getattr(chain, "STEP_ORDER", []))
            srcs: list[str] = []
            try:
                from config.loader import active_source_ids

                srcs = list(active_source_ids())
            except Exception:  # noqa: BLE001 配置不可用 → 桑基图退化为无源节点
                srcs = []
            triggers: list[dict] = []
            try:
                import yaml

                tg = yaml.safe_load(
                    open(os.path.join(paths.CONFIG_DIR, "triggers.yaml"), encoding="utf-8")
                ) or {}
                for item in (tg.get("triggers") or []):
                    triggers.append({"id": item.get("id"),
                                     "conditions": item.get("conditions") or {}})
            except Exception:  # noqa: BLE001
                triggers = []
            # 边宽：**产物侧**记录数（权威来源）。
            # ⚠️ 2026-10-16 修正：此前读台账行的大小类列（该表**根本没有那些列**，
            #    实测列：run_id/step/rc/exit_code/elapsed_s/skipped/note/recorded_at/
            #    started_at/finished_at/is_dry_run）⇒ `weights` 恒为空 dict
            #    ⇒ **P1 桑基图不渲染任何边**。
            #    依据 ADR-0014 的 C3 决策「产物侧才是权威，不扩record_count 列」，
            #    改由 `clean_index_api.record_count(src)` 取（实测 gov=13178/nfra=1934）。
            weights: dict[str, int] = {}
            rcodes: dict[str, dict] = {}
            try:
                from interfaces import clean_index_api as _cia

                for _src in srcs:
                    try:
                        n = int(_cia.record_count(_src) or 0)
                    except Exception:  # noqa: BLE001 单源不可用不影响其余
                        n = 0
                    if n > 0:
                        # 记到**展开形态**的步骤名（collect:gov / clean:gov…）
                        for _pfx in ("collect", "clean", "apply"):
                            weights[f"{_pfx}:{_src}"] = n
            except Exception:  # noqa: BLE001
                weights = {}
            try:
                from std_lib.common_lib import governance_store as gs

                _rid, steps_rows = gs.last_run_with_steps()
                for s in steps_rows:
                    nm = str(s.get("step") or "")
                    rcodes[nm] = {"status": s.get("status"),
                                  "rc": s.get("exit_code") or s.get("rc"),
                                  "argv": s.get("argv")}
            except Exception:  # noqa: BLE001 台账不可用 → 状态桑基（无流量语义）
                pass
            return {"available": True, "fetched_at": _now(),
                    "source": "STEP_ORDER + sources.yaml + triggers.yaml + 运行台账",
                    "steps": steps, "sources": srcs, "triggers": triggers,
                    "weights": weights, "step_status": rcodes,
                    "mode": "flow" if weights else "status"}
        return CACHE.get_or_call("flow_graph", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("STEP_ORDER/triggers", e)


#: 步骤日志目录（写入方 = tools/run_production_refresh.py 的 `_run`，命名
#: `{步骤名去冒号}_{时间戳}.{out|err}.log`；时间戳在**文件名尾部** ⇒ 按名排序即最新优先）
STEP_LOG_DIR = os.path.join(paths.REPORTS_DIR, "_tmp", "step_logs")


def latest_step_log(step: str, tail_lines: int = 40) -> tuple[str, str]:
    """取某步骤**最新一份**日志 ⇒ `(日志正文尾部, 文件相对路径)`；无则 `("", "")`。

    取证入口的唯一实现：确认台 P1 抽屉与后续诊断工具（step_triage）共用本函数，
    杜绝第二份日志取证逻辑与写入方命名规则漂移。
    """
    import glob
    import re as _re

    # 前缀归一规则**必须与写入方逐字一致**（`tools/run_production_refresh.py:611`：
    # `_safe = re.sub(r"[^0-9A-Za-z_.-]", "_", step)`）——全字符归一，不是只换冒号；
    # 两处规则漂移会让取证"安静地取不到"，故此处同写并由
    # `tests/test_step_log_evidence.py::test_sanitisation_rule_matches_writer` 绑定。
    prefix = _re.sub(r"[^0-9A-Za-z_.-]", "_", str(step))
    hits: list[str] = []
    for ext in ("out", "err"):
        hits += glob.glob(os.path.join(STEP_LOG_DIR, f"{prefix}_*.{ext}.log"))
    if not hits:
        return "", ""
    # 文件名以时间戳结尾 ⇒ 字典序即时间序
    path = sorted(hits)[-1]
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-tail_lines:]), path
    except OSError:
        return "(日志不可读)", path


def step_detail(step: str) -> dict:
    """P1 节点抽屉：某步骤的 rc/耗时/note + 日志 tail。"""
    try:
        def _load():
            from std_lib.common_lib import governance_store as gs

            _rid, rows = gs.last_run_with_steps()
            row = next((r for r in rows if str(r.get("step")) == step), None)
            log_tail, lp = latest_step_log(step)
            return {"available": True, "source": "governance_store + step_logs",
                    "fetched_at": _now(), "step": step, "row": row, "log_tail": log_tail,
                    "log_path": os.path.relpath(lp, paths.ROOT) if lp else ""}
        return CACHE.get_or_call(f"step_detail:{step}", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("step_logs", e)


# --------------------------------------------------------------------------- #
# P2 数据状态
# --------------------------------------------------------------------------- #
def assets() -> dict:
    """P2：产物清单（clean 索引）+ 水位矩阵 + 契约状态 + 发布件状态。"""
    try:
        def _load():
            from interfaces import clause_index_api, clean_index_api

            src_ids = clean_index_api.source_ids()
            files: list[dict] = []
            for sid in src_ids:
                d = clean_index_api.latest(sid) or {}
                files.append({"source": sid, "date": clean_index_api.latest_date(sid),
                              "records": clean_index_api.record_count(sid),
                              "index": d})
            pub: dict = {}
            try:
                from interfaces import base_api

                for base in ("external", "internal"):
                    pub[base] = base_api.manifest(base)
            except Exception as e:  # noqa: BLE001 发布件未生成属常态
                pub = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            contract: dict = {"consistent": None, "problems": []}
            try:
                contract = clause_index_api.validate_schema()
            except Exception:  # noqa: BLE001
                pass
            return {"available": True, "source": "clean_index_api + clause_index_api + base_api",
                    "fetched_at": _now(), "sources": src_ids, "files": files,
                    "publish": pub, "contract": contract}
        return CACHE.get_or_call("assets", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("clean_index_api", e)


# --------------------------------------------------------------------------- #
# P3 指标
# --------------------------------------------------------------------------- #
def metrics() -> dict:
    """P3 指标：记录数趋势 / 待办 aging / 门禁通过率 / 主题分布 / 时效三态 / 关系统计。"""
    try:
        def _load():
            out: dict = {"available": True, "fetched_at": _now(),
                         "source": "governance_store + theme_api + timeliness_api + relations_api"}
            # 门禁通过率趋势（按 run_id 分组）
            try:
                from interfaces import governance_api

                rows = governance_api.gate_results(limit=1000)
                by_run: dict[str, list[bool]] = {}
                for r in rows:
                    rid = str(r.get("run_id") or "?")
                    by_run.setdefault(rid, []).append(bool(r.get("passed")))
                out["gate_trend"] = [
                    {"run_id": rid, "rate": round(100.0 * sum(v) / len(v), 2)}
                    for rid, v in list(by_run.items())[-12:]
                ]
            except Exception as e:  # noqa: BLE001
                out["gate_trend_error"] = f"{type(e).__name__}"
            # 待办 aging
            try:
                from std_lib.common_lib import governance_store as gs

                items = gs.worklist_list(status="open", limit=0)
                buckets = {"0-3": 0, "4-7": 0, "8-14": 0, "15+": 0}
                for it in items:
                    ca = str(it.get("created_at") or "")
                    age = 0
                    try:
                        created = datetime.fromisoformat(ca)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=TZ8)
                        age = (datetime.now(TZ8) - created).days
                    except ValueError:
                        age = 0
                    if age <= 3:
                        buckets["0-3"] += 1
                    elif age <= 7:
                        buckets["4-7"] += 1
                    elif age <= 14:
                        buckets["8-14"] += 1
                    else:
                        buckets["15+"] += 1
                out["worklist_aging"] = buckets
            except Exception:  # noqa: BLE001
                out["worklist_aging"] = {}
            # 时效三态
            try:
                from interfaces import timeliness_api

                state = timeliness_api.load_state() or {}
                tri = {"valid": 0, "pending": 0, "uncertain": 0}
                for rec in state.values():
                    status_s = str((rec or {}).get("status") or "uncertain")
                    if status_s in tri:
                        tri[status_s] += 1
                    else:
                        tri["uncertain"] += 1
                out["timeliness_tri"] = tri
            except Exception:  # noqa: BLE001
                out["timeliness_tri"] = {}
            # 关系统计
            try:
                from interfaces import relations_api

                out["relations"] = relations_api.summary()
            except Exception:  # noqa: BLE001
                out["relations"] = {"available": False}
            # 主题分布
            # ⚠️ 2026-10-19 修：原实现从 `attr_rows()` 取 `主题` 列，但**归属表没有该列**
            #    （实测列：监管文件编号/文件名称/发文字号/发布日期/文件来源/时效状态/
            #      判定日期/编号备注）⇒ 1162 条全部落 `?` ⇒ 图表只有一个 `?` 柱
            #    （用户报「P3 指标渲染异常」即此）。
            #    正确来源：`theme_map()`（`监管文件编号 → 主题`，来自主题归属表）。
            try:
                from interfaces import rfn_api

                rows = rfn_api.attr_rows()
                # ⚠️ 必须用 `theme_by_doc()`（**编号→主题**，来自主题归属表），
                #    **不是** `theme_map()`（那是 `T0..T10 → 主题名` 的受控枚举，
                #    拿它查编号 ⇒ 0/1162 命中）。
                tmap = rfn_api.theme_by_doc()
                dist: dict[str, int] = {}
                unclassified = 0
                for r in rows:
                    key = str(r.get("监管文件编号") or "").strip()
                    t = str(r.get("主题") or r.get("theme")
                            or tmap.get(key) or "").strip()
                    if not t:
                        unclassified += 1
                        continue
                    dist[t] = dist.get(t, 0) + 1
                # 未归类单列（用可读名而非 `?`——`?` 会被误读成渲染错误）
                if unclassified:
                    dist["未归类"] = unclassified
                out["theme_dist"] = dist
            except Exception:  # noqa: BLE001
                out["theme_dist"] = {}
            return out
        return CACHE.get_or_call("metrics", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("metrics 多源", e)


def quality() -> dict:
    """P3 六维质量卡：直接读 `tools/gen_quality_metrics` 的最新产物（不重算）。"""
    try:
        def _load():
            p = os.path.join(paths.REPORTS_DIR, "quality", "quality_metrics_latest.json")
            if not os.path.exists(p):
                return {"available": False, "source": "reports/quality/quality_metrics_latest.json",
                        "reason": "尚未生成（python -m tools.gen_quality_metrics）",
                        "fetched_at": _now()}
            return {"available": True, "source": "reports/quality/quality_metrics_latest.json",
                    "fetched_at": _now(),
                    "doc": json.load(open(p, encoding="utf-8"))}
        return CACHE.get_or_call("quality", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("quality metrics", e)


def slo() -> dict:
    """P3 SLI 卡：读 `tools/check_slo` 的最新快照 + 重算当前值。"""
    try:
        def _load():
            hist = os.path.join(paths.REPORTS_DIR, "slo", "slo_history.jsonl")
            last = {}
            if os.path.exists(hist):
                lines = [ln for ln in open(hist, encoding="utf-8").read().splitlines() if ln]
                if lines:
                    last = json.loads(lines[-1])
            return {"available": bool(last), "source": "reports/slo/slo_history.jsonl",
                    "fetched_at": _now(), "last": last}
        return CACHE.get_or_call("slo", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("slo history", e)


# --------------------------------------------------------------------------- #
# P4 待办闭环台
# --------------------------------------------------------------------------- #
def worklist_items(status: str = "open", kind: str = "", limit: int = 0,
                   offset: int = 0) -> dict:
    """P4 待办列表（+ 每条的处置建议来自生产者写入的 suggestion）。"""
    try:
        def _load():
            from std_lib.common_lib import governance_store as gs

            # `limit=0` 取全部（`gs.worklist_list` 的 0 即无上限）；分页由**前端切片**做。
            # 实测：641 条 open 只返回 200 是因 `limit=200` 硬编码（2026-10-16 修正）。
            total_rows = gs.worklist_list(status=status, kind=kind, limit=0)
            rows = total_rows[offset:] if offset else total_rows
            if limit and limit > 0:
                rows = rows[:limit]
            # 2026-10-19（用户报「不同 kind 不能批量处置」）：回填
            # ① `filter.kind` 供前端回显选中项；② `kind_counts`（**全量口径**，
            # 不受当前 kind 筛选影响）供下拉显示「某项有多少条」——
            # 否则选中某 kind 后其它 kind 的计数会变成 0，用户会以为"没了"。
            all_rows = total_rows if not kind else gs.worklist_list(
                status=status, kind="", limit=0)
            counts: dict[str, int] = {}
            for r in all_rows:
                k = str(r.get("kind") or "")
                counts[k] = counts.get(k, 0) + 1
            # 2026-10-19（用户反馈）：`subject` 的写法各 kind 不统一（有的写文号、
            # 有的写名称）⇒ 业务无法判断"这条指哪份文件"。给每条附加 `fields`
            # （名称/文号/访问地址 + 各自来源），由 `interfaces.worklist_fields`
            # 统一派生（**单一事实源**，MCP/CLI 也可复用）。
            needs_url: list[str] = []
            try:
                from interfaces import worklist_fields as _wf

                rows = _wf.decorate(rows)
                needs_url = sorted(_wf.NEEDS_URL)
            except Exception as e:  # noqa: BLE001  派生失败不阻断列表（但**可辨**）
                for r in rows:
                    r["fields"] = {"error": f"{type(e).__name__}: {e}"}
            return {"available": True, "source": "governance_store.worklist_list",
                    "fetched_at": _now(), "status": status, "kind": kind,
                    "filter": {"status": status, "kind": kind},
                    "kind_counts": counts,
                    "fields_schema": {"needs_url": needs_url},
                    "count": len(rows), "total": len(total_rows),
                    "offset": offset, "limit": limit, "items": rows}
        return CACHE.get_or_call(f"worklist:{status}:{kind}:{limit}:{offset}", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("governance_store.worklist_list", e)


def worklist_resolve(item_id: str, resolution: str, *, by: str = "confirm_console",
                     status: str = "resolved") -> dict:
    """P4 闭环动作：处置一条待办（**唯一写路径**：governance_store.worklist_resolve）。

    纪律：status 受控（resolved/dismissed），非法值抛错而非静默；写后**清缓存**让
    页面立即反映新状态（处置后验证 = 前端重拉列表看到该条已移出 open）。
    """
    try:
        if status not in ("resolved", "dismissed"):
            raise ValueError(f"status 非法（受控 resolved|dismissed）：{status!r}")
        if not item_id:
            raise ValueError("缺 item_id")
        if not (resolution or "").strip():
            raise ValueError("缺 resolution（处置理由必填——无理由的处置不可审计）")
        from std_lib.common_lib import governance_store as gs

        ok = gs.worklist_resolve(item_id, resolution, by=by, status=status)
        CACHE.clear()
        return {"available": True, "ok": bool(ok), "item_id": item_id, "status": status,
                "by": by, "fetched_at": _now(),
                "source": "governance_store.worklist_resolve（唯一写路径）"}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "ok": False,
                "reason": f"{type(e).__name__}: {str(e)[:200]}", "fetched_at": _now(),
                "source": "governance_store.worklist_resolve"}


def worklist_kinds() -> dict:
    """P4 kind→action 注册表（§8.6 待办处置写路径：白名单，不做任意代码执行）。"""
    return {"available": True, "fetched_at": _now(), "source": "config.enums.WORKLIST_KIND",
            "kinds": _worklist_kind_list(),
            "action_policy": "确认台只做「处置登记（resolve/dismiss）」；业务回写仍走各模块"
                             "既有写入口（CLI），确认台**不执行任意动作**（安全核心，§8.6）。"}


def _worklist_kind_list() -> list[str]:
    try:
        from config.enums import WORKLIST_KIND

        return sorted(str(k) for k in (WORKLIST_KIND or ()))
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# P5 治理审计 / P6 文档检索 / P7 投放看板 / P8 发布事件流
# --------------------------------------------------------------------------- #
def governance() -> dict:
    """P5：最近运行列表 + 审计日志尾部。"""
    try:
        def _load():
            from interfaces import governance_api
            from std_lib.common_lib import governance_store as gs

            out = {"available": True, "fetched_at": _now(),
                   "source": "governance_store.list_runs + governance_api.audit"}
            try:
                out["runs"] = gs.list_runs(limit=20)
            except Exception:  # noqa: BLE001
                out["runs"] = []
            try:
                out["audit"] = governance_api.audit(limit=100) if governance_api.enabled() else []
            except Exception:  # noqa: BLE001
                out["audit"] = []
            try:
                out["status"] = governance_api.status()
            except Exception:  # noqa: BLE001
                out["status"] = {}
            return out
        return CACHE.get_or_call("governance", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("governance_store", e)


def search(q: str, *, kind: str = "records", limit: int = 20) -> dict:
    """P6 文档检索：走 base_api 的 FTS5 门面（不裸读 sqlite）。"""
    try:
        def _load():
            from interfaces import base_api

            if not (q or "").strip():
                return {"available": True, "source": "base_api.search_*", "fetched_at": _now(),
                        "query": q, "results": [], "note": "空查询"}
            out: dict = {"available": True, "fetched_at": _now(), "query": q,
                         "source": "base_api.search_external / search_internal"}
            try:
                out["results"] = base_api.search_external(q, limit=limit, kind=kind)
                out["backend"] = "external"
            except Exception as e:  # noqa: BLE001 external 不可用 → 回退 internal
                out["error_external"] = f"{type(e).__name__}: {str(e)[:120]}"
                try:
                    out["results"] = base_api.search_internal(
                        q, limit=limit, kind="policies" if kind == "records" else "clauses")
                    out["backend"] = "internal"
                except Exception as e2:  # noqa: BLE001
                    out["available"] = False
                    out["reason"] = f"{type(e2).__name__}: {str(e2)[:160]}"
                    out["results"] = []
            return out
        return CACHE.get_or_call(f"search:{q}:{kind}:{limit}", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.search", e)


def publish_board() -> dict:
    """P7 投放看板：双底座发布件 manifest + wiki 投放源状态。"""
    try:
        def _load():
            from interfaces import base_api

            out: dict = {"available": True, "fetched_at": _now(), "source": "base_api.manifest"}
            for base in ("external", "internal"):
                try:
                    out[base] = base_api.manifest(base)
                except Exception as e:  # noqa: BLE001 未发布属常态
                    out[base] = {"available": False,
                                 "reason": f"{type(e).__name__}: {str(e)[:120]}"}
            wiki = os.path.join(paths.REPORTS_DIR, "_wiki_sources")
            out["wiki_sources"] = {"dir": os.path.relpath(wiki, paths.ROOT),
                                   "exists": os.path.isdir(wiki),
                                   "files": len(os.listdir(wiki)) if os.path.isdir(wiki) else 0}
            return out
        return CACHE.get_or_call("publish_board", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.manifest", e)


def release_stream(limit: int = 20) -> dict:
    """P8 发布事件流：git 提交（近 N 条）+ SBOM + 版本契约，全部只读。"""
    try:
        def _load():
            import subprocess

            out: dict = {"available": True, "fetched_at": _now(),
                         "source": "git log + reports/_sbom + contract_manifest"}
            try:
                r = subprocess.run(["git", "log", f"-{limit}", "--date=short",
                                    "--pretty=%h|%ad|%s"],
                                   cwd=paths.ROOT, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=20)
                commits = []
                for ln in (r.stdout or "").splitlines():
                    parts = ln.split("|", 2)
                    if len(parts) == 3:
                        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
                out["commits"] = commits
            except Exception as e:  # noqa: BLE001 git 不可用（非 git 检出）
                out["commits"] = []
                out["git_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            sbom = os.path.join(paths.REPORTS_DIR, "_sbom", "sbom_latest.json")
            if os.path.exists(sbom):
                try:
                    doc = json.load(open(sbom, encoding="utf-8"))
                    out["sbom"] = {"components": len(doc.get("components") or []),
                                   "lock_sha256": next(
                                       (p.get("value") for p in
                                        ((doc.get("metadata") or {}).get("properties") or [])
                                        if p.get("name") == "lock:sha256"), "")}
                except (OSError, ValueError):
                    out["sbom"] = {}
            else:
                out["sbom"] = {}
            try:
                cm = json.load(open(os.path.join(paths.CONFIG_DIR, "schema",
                                                    "contract_manifest.json"), encoding="utf-8"))
                out["contract_version"] = cm.get("version")
            except (OSError, ValueError):
                out["contract_version"] = None
            return out
        return CACHE.get_or_call("release_stream", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("git/SBOM", e)
