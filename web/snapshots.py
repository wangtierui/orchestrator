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
    """TTL 缓存（线程安全）。缓存**派生结果**，不缓存异常。

    2026-10-21（第一百零六批）增强三处，起因是 `/api/overview` **冷态 22s**
    让 `tests/test_confirm_console.py` 的 20s 超时**必然**被撞破（表现为"偶发"，
    实为冷/热态差异）：

      1. **per-key TTL**：默认 2s，但个别键（如扫 13.8GB 外部模型目录）允许更长；
      2. **stale-while-revalidate**：过期时**先返回旧值**、后台刷新
         ⇒ 请求永不因"正在重算"而阻塞（这是"可用性优先于毫米级新鲜度"的取舍：
         存储占用是**展示型**指标，最多旧 600s，但页面必须立刻出来）；
      3. **防重入**：同一键只允许一个后台刷新线程（否则并发请求会各起一个，
         反而放大 IO 压力 —— 这正是慢指标最怕的）。

    **不缓存异常**：`fn()` 抛错照常抛给调用方，由 `_degrade` 降级（可辨）。
    """

    def __init__(self, ttl: float = CACHE_TTL_S) -> None:
        self._ttl = ttl
        self._d: dict[str, tuple[float, object]] = {}
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    def get_or_call(self, key: str, fn, *, ttl: float | None = None):
        """取缓存或计算。`ttl=None` 用全局默认。"""
        eff = self._ttl if ttl is None else ttl
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
        if hit is not None:
            age = now - hit[0]
            if age < eff:
                return hit[1]
            # 过期：**先返回旧值**，后台刷新（不阻塞本次请求）。
            self._refresh_async(key, fn, eff)
            return hit[1]
        # 无旧值 ⇒ 只能同步算（首次）。慢键由 `warm()` 预热以避免走到这里。
        val = fn()
        with self._lock:
            self._d[key] = (time.time(), val)
        return val

    def _refresh_async(self, key: str, fn, ttl: float) -> None:
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)

        def _worker() -> None:
            try:
                val = fn()
            except Exception:  # noqa: BLE001  后台刷新失败就保留旧值（不污染缓存）
                return
            finally:
                with self._lock:
                    self._inflight.discard(key)
            with self._lock:
                self._d[key] = (time.time(), val)

        threading.Thread(target=_worker, name=f"cache-refresh:{key}",
                         daemon=True).start()

    def warm(self, key: str, fn, *, ttl: float | None = None) -> bool:
        """**预热**：后台算一次并写入缓存；已有值则不重复算。

        返回"是否已就绪"（供调用方决定要不要标 `warming`）。
        """
        with self._lock:
            if key in self._d:
                return True
            if key in self._inflight:
                return False
            self._inflight.add(key)

        def _worker() -> None:
            try:
                val = fn()
            except Exception:  # noqa: BLE001
                return
            finally:
                with self._lock:
                    self._inflight.discard(key)
            with self._lock:
                self._d[key] = (time.time(), val)

        threading.Thread(target=_worker, name=f"cache-warm:{key}",
                         daemon=True).start()
        return False

    def peek(self, key: str):
        """只看缓存（不触发计算）；无值返回 `None`。"""
        with self._lock:
            hit = self._d.get(key)
        return None if hit is None else hit[1]

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


#: 外部模型目录的**独立长 TTL**（秒）。
#:
#: 为什么单独定 600s（实测依据，2026-10-21）：`external/` 有 **19320 个文件 /
#: 13.8 GB**，扫一遍 `os.walk + getsize` 要 **4053 ms（冷态更高）**，
#: 而其余 4 个目录合计只 **8 ms**。它撑起了 `/api/overview` 的全部耗时
#: （冷态 22s ⇒ 撞破 `test_confirm_console` 的 20s 超时，表现为"偶发失败"）。
#: 而这个目录的变化频率以**天**计（模型/权重文件），600s 的新鲜度完全够用。
_STORAGE_HEAVY_TTL_S = 600.0

#: 外部模型目录的缓存键（`storage_usage` 与预热共用）
_STORAGE_HEAVY_KEY = "storage_usage:external"


def _dir_size(p: str) -> int:
    """目录/文件的字节数（跳过 `__pycache__`；单个文件 stat 失败则跳过）。"""
    if os.path.isfile(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    if not os.path.isdir(p):
        return 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _scan_external() -> int:
    """扫外部模型目录（**4s 级**，只应经长 TTL 缓存或预热调用）。"""
    return _dir_size(paths.EXTERNAL_DIR)


def warm_storage() -> bool:
    """**预热**外部模型目录大小（后台线程，进程启动时调一次）。

    返回"是否已就绪"。未就绪时 `storage_usage()` 会返回 `heavy_ready=False`
    并在 `note` 里说明"统计中"——**绝不为了等它而阻塞首屏**。
    """
    return CACHE.warm(_STORAGE_HEAVY_KEY, _scan_external,
                      ttl=_STORAGE_HEAVY_TTL_S)


def storage_usage() -> dict:
    """存储占用条：各资产实测体积（目录扫描，只读）。

    2026-10-21 重构（**分级**）：把"轻量 4 目录 + DB"（8ms）与"外部模型 13.8GB"
    （4053ms）分开 —— 前者 2s TTL，后者 600s TTL + 启动预热。
    未就绪时 `heavy_ready=False`，前端显示"统计中…"而不是转圈等到超时。
    """
    try:
        def _light() -> dict[str, int]:
            targets = {
                "raw": os.path.join(paths.MODULES_DIR, "regulatory_scrapers",
                                    "data", "raw"),
                "cleaned": os.path.join(paths.MODULES_DIR, "regulatory_scrapers",
                                        "data", "cleaned"),
                "clauses": os.path.join(paths.MODULES_DIR, "regulatory_scrapers",
                                        "data", "clauses"),
                "governance_db": os.path.join(paths.DATA_DIR, "governance.db"),
            }
            return {name: _dir_size(p) for name, p in targets.items()}

        out = dict(CACHE.get_or_call("storage_usage:light", _light))
        # 重量部分：**只读缓存，不在此触发 4s 扫描**（首次交给 warm_storage 预热）。
        heavy = CACHE.peek(_STORAGE_HEAVY_KEY)
        heavy_ready = heavy is not None
        if heavy_ready:
            out["external_models"] = int(heavy)
        total_all = sum(out.values()) or 1
        ratio = {k: round(100.0 * v / total_all, 2) for k, v in out.items()}
        if not heavy_ready:
            # 预热：首次调用时顺手启动一次（幂等，已在跑则不重复）。
            CACHE.warm(_STORAGE_HEAVY_KEY, _scan_external,
                       ttl=_STORAGE_HEAVY_TTL_S)
        return {
            "available": True,
            "source": "目录扫描（paths 常量）",
            "fetched_at": _now(),
            "bytes": out,
            "ratio": ratio,
            "total_bytes": total_all,
            "heavy_ready": heavy_ready,
            "note": ("" if heavy_ready else
                     "外部模型目录（约 13.8GB / 1.9 万文件）统计中，"
                     "首次启动约需数秒；其余资产已统计"),
        }
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
            # ── 展开 `{src}`：模板步骤 → 每个源一个真实节点 ──────────────
            # 2026-10-22（用户报「P1 始终无任何显示」）：原实现把 `collect:{src}`
            # 这类**模板名**直接当节点画，而边匹配用 `srcs.includes(m[1])`
            # ⇒ `src` 不等于任何真实源 ⇒ **一条边都连不出来**；
            # 权重表里却存的是**展开后**的名字（`collect:gov`）⇒ 节点上也读不到数字。
            # 结果就是"两列灰方块、无流向、无数据量"。
            weights: dict[str, int] = {}
            try:
                from interfaces import clean_index_api as _cia

                for _src in srcs:
                    try:
                        n = int(_cia.record_count(_src) or 0)
                    except Exception:  # noqa: BLE001 单源不可用不影响其余
                        n = 0
                    if n > 0:
                        for _pfx in ("collect", "clean", "relevance", "apply"):
                            weights[f"{_pfx}:{_src}"] = n
            except Exception:  # noqa: BLE001
                weights = {}
            rcodes: dict[str, dict] = {}
            run_id = ""
            try:
                from std_lib.common_lib import governance_store as gs

                run_id, steps_rows = gs.last_run_with_steps()
                # 状态归一：台账里的 `status` 常为空、真正有语义的是 `exit_code`
                # （实测：36 条 OK 行的 status 全是 None ⇒ P1 全画成 unknown，
                #  看图的人只会觉得"没状态"）。按退出码补齐，未知才留 unknown。
                _MAP = {"OK": "ok", "SKIP": "skip", "FAIL": "failed",
                        "DATA": "failed", "ENV": "failed",
                        "NOT_SOURCE_TREE": "failed"}
                for s in steps_rows:
                    nm = str(s.get("step") or "")
                    _ex = str(s.get("exit_code") or "").upper()
                    _st = str(s.get("status") or "").strip().lower()
                    if _st in ("", "unknown", "none"):
                        _st = _MAP.get(_ex, "unknown")
                    rcodes[nm] = {"status": _st,
                                  "rc": s.get("exit_code") or s.get("rc"),
                                  "elapsed_s": s.get("elapsed_s"),
                                  "argv": s.get("argv")}
            except Exception:  # noqa: BLE001 台账不可用 → 状态图（无流量语义）
                pass

            # ── 真实 DAG：由 `StepSpec.produces / depends_on` **按产物连边** ──
            dags = _build_flow_dag(steps, srcs, weights, rcodes)
            return {"available": True, "fetched_at": _now(),
                    "source": "STEP_ORDER + StepSpec(produces/depends_on) + "
                              "clean_index_api + 运行台账",
                    "run_id": run_id,
                    "steps": steps, "sources": srcs, "triggers": triggers,
                    "weights": weights, "step_status": rcodes,
                    **dags,
                    "mode": "flow" if dags["edges"] else "status"}
        return CACHE.get_or_call("flow_graph", _load)
    except Exception as e:  # noqa: BLE001
        return _degrade("STEP_ORDER/triggers", e)


def _build_flow_dag(steps: list[str], srcs: list[str],
                    weights: dict[str, int],
                    rcodes: dict[str, dict]) -> dict:
    """把「模板步骤 + 产物依赖」展开成**可直接渲染的分层 DAG**。

    返回 `{layers, nodes, edges, totals}`：

      · `nodes[i] = {id, label, layer, layer_name, kind, status, rc, elapsed_s,
                     volume, degraded, purpose}`
      · `edges[i] = {from, to, artifact, volume}`

    连边依据是 **产物（artifact）** 而非"步骤名包含关系"：
    `StepSpec.produces` 声明产出的产物 id、`depends_on` 声明消费的产物 id，
    于是"某产物由谁产出、被谁消费"就能连出真实数据流。这比按名字猜边可靠得多
    （原来靠 `collect:` 前缀正则，只能连采集→清洗一条，其余 20+ 条全丢）。
    """
    import importlib

    chain = importlib.import_module("tools.run_production_refresh")
    specs = {s.name: s for s in chain.step_specs()}
    layer_order: list[str] = []
    layer_names: dict[str, str] = {}
    for s in specs.values():
        if s.layer and s.layer not in layer_order:
            layer_order.append(s.layer)
            layer_names[s.layer] = str(getattr(s, "layer_name", "") or s.layer)
    try:  # 层名优先取人工可读字典（若存在）
        from config.enums import STEP_LAYER_LABELS as _LBL  # type: ignore

        layer_names.update({k: str(v) for k, v in _LBL.items()})
    except Exception:  # noqa: BLE001 没有该字典就用 id
        pass

    # 模板 → 展开实例（含 `{src}` 的按源展开；其余原样）
    inst: list[dict] = []
    for name in steps:
        if "{src}" in name:
            for src in srcs:
                inst.append({"id": name.replace("{src}", src), "tpl": name,
                             "src": src})
        else:
            inst.append({"id": name, "tpl": name, "src": ""})

    def _expand(a: str, src: str) -> str:
        return a.replace("{src}", src) if src else a

    # 产物 → 生产者 / 消费者（同一产物按源隔离：raw:gov 只连 gov 的采集）
    prod: dict[str, str] = {}
    cons: dict[str, list[str]] = {}
    for it in inst:
        spec = specs.get(it["tpl"])
        if spec is None:
            continue
        for a in getattr(spec, "produces", ()) or ():
            prod.setdefault(_expand(a, it["src"]), it["id"])
        for a in getattr(spec, "depends_on", ()) or ():
            cons.setdefault(_expand(a, it["src"]), []).append(it["id"])

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for art, consumers in cons.items():
        maker = prod.get(art)
        if not maker:
            continue
        for c in consumers:
            if c == maker or (maker, c) in seen:
                continue
            seen.add((maker, c))
            edges.append({"from": maker, "to": c, "artifact": art,
                          "volume": int(weights.get(maker, 0) or 0)})
    # 采集 → 清洗这一跳：`raw:{src}` 既由 collect 产出又被 clean 消费，已由上循环连好。
    # 额外补「同源顺序跳」（relevance/apply 未声明 depends_on 的源级步骤）：
    for src in srcs:
        chain_seq = [f"collect:{src}", f"clean:{src}", f"relevance:{src}",
                     f"apply:{src}"]
        for a, b in zip(chain_seq, chain_seq[1:], strict=False):
            if (a, b) in seen:
                continue
            if any(n["id"] == a for n in inst) and any(n["id"] == b for n in inst):
                seen.add((a, b))
                edges.append({"from": a, "to": b, "artifact": f"src:{src}",
                              "volume": int(weights.get(a, 0) or 0)})

    nodes: list[dict] = []
    for it in inst:
        spec = specs.get(it["tpl"])
        st = rcodes.get(it["id"]) or rcodes.get(it["tpl"]) or {}
        status = str(st.get("status") or ("idle" if not st else "unknown"))
        rc = st.get("rc")
        if rc == "SKIP" or rc == "skip":
            status = "skip"
        elif rc not in (None, 0, "0", "OK") and status in ("", "unknown", None):
            status = "failed"
        nodes.append({
            "id": it["id"], "label": it["id"], "tpl": it["tpl"],
            "src": it["src"],
            "layer": (spec.layer if spec else ""),
            "kind": (spec.kind if spec else ""),
            "purpose": (str(getattr(spec, "purpose", "") or "")[:120] if spec else ""),
            "status": status, "rc": rc,
            "elapsed_s": st.get("elapsed_s"),
            "volume": int(weights.get(it["id"], 0) or 0),
            "degraded": (str(getattr(spec, "fail_semantics", "") or "")[:80]
                         if spec else ""),
        })
    idx = {n["id"]: n for n in nodes}
    edges = [e for e in edges if e["from"] in idx and e["to"] in idx]
    return {"layers": [{"id": lid, "name": layer_names.get(lid, lid)}
                       for lid in layer_order],
            "nodes": nodes, "edges": edges,
            "totals": {"nodes": len(nodes), "edges": len(edges),
                       "volume": sum(int(weights.get(f"{p}:{s}", 0) or 0)
                                     for s in srcs for p in ("collect",)),
                       "sources": len(srcs)}}


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
def _assets_load() -> dict:
    """`assets()` 的计算体（**提取为模块级**，以便 `warm_assets()` 预热）。

    冷态实测 **5965 ms**：主要耗时在 `clause_index_api.validate_schema()`
    要遍历 clauses 产物（约 959MB）做 schema 自检。
    """
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
    return {"available": True,
            "source": "clean_index_api + clause_index_api + base_api",
            "fetched_at": _now(), "sources": src_ids, "files": files,
            "publish": pub, "contract": contract}


def warm_assets() -> bool:
    """**预热** P2 产物清单（后台，进程启动时调一次）。返回是否已就绪。"""
    return CACHE.warm("assets", _assets_load)


def _assets_warming() -> dict:
    """`assets()` 预热中的**占位**（结构完整、明示未就绪）。

    为什么不直接返回 `available: False`：那会让前端显示"不可用"，把
    "正在统计"误报成"坏了"（本仓一贯要求降级可辨）。故保持 `available: True`
    + `warming: True` + 空集合 + `note`，前端据 `warming` 显示"统计中…"。
    """
    return {"available": True, "warming": True, "source": "预热中",
            "fetched_at": _now(), "sources": [], "files": [], "publish": {},
            "contract": {"consistent": None, "problems": []},
            "note": "产物清单统计中（首次约 6s，之后读缓存）"}


def assets() -> dict:
    """P2：产物清单（clean 索引）+ 水位矩阵 + 契约状态 + 发布件状态。

    2026-10-21：逻辑提取为 `_assets_load()` 并由 `build_server` **预热** ——
    冷态 6s 会拖慢首屏该卡片；预热后首次请求直接读缓存（0ms）。

    ⚠️ **必须用 `peek` 而非 `get_or_call`**：后者在"后台预热尚未完成"时会
    **自己再算一遍**，于是预热线程与请求线程同时扫 959MB clauses 产物
    ⇒ **缓存击穿**，实测反而从 5965ms 恶化到 **11667ms**（两份计算互相争抢 IO）。
    现改为：有缓存就用；没有就触发后台预热并立即返回占位（0ms）。
    """
    try:
        hit = CACHE.peek("assets")
        if hit is not None:
            return hit
        CACHE.warm("assets", _assets_load)
        return _assets_warming()
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
            schema: dict = {"needs_url": []}
            try:
                from interfaces import worklist_fields as _wf

                rows = _wf.decorate(rows)
                # 2026-10-22：列契约交给后端（**前端不硬编码 kind 语义**）。
                # 原来只给 `needs_url` 一个开关，前端遂以"本页出现过 needs_url 的 kind"
                # 决定**所有行**的列 ⇒ 混合列表里 rfn_clean_drift_c2 被渲染成
                # "名称=RFN / 文号=未查到 / 地址=—"（用户报的列内容错位）。
                schema = _wf.columns()
            except Exception as e:  # noqa: BLE001  派生失败不阻断列表（但**可辨**）
                for r in rows:
                    r["fields"] = {"error": f"{type(e).__name__}: {e}"}
            return {"available": True, "source": "governance_store.worklist_list",
                    "fetched_at": _now(), "status": status, "kind": kind,
                    "filter": {"status": status, "kind": kind},
                    "kind_counts": counts,
                    "fields_schema": schema,
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


def _theme_label(v: str) -> str:
    """主题码/全名 → 展示用名称（统一口径）。

    双体系（2026-10-23 实测）：**外部**底座存的是全名（`T1销售行为与消费者保护`），
    **内部**底座存的是代号（`T1`），另有 `UNALIGNED`/空。三处不归一，同一页签里
    就会出现"T1"和"T1销售行为与消费者保护"两种写法，读者会以为是两个主题。
    """
    v = (v or "").strip()
    if not v:
        return ""
    if v == "UNALIGNED":
        return "未对齐"
    try:
        from interfaces.theme_api import THEME_MAP_P0
    except Exception:  # noqa: BLE001  引导缺失时退化为原值（不阻断列表）
        return v
    return THEME_MAP_P0.get(v, v)


def _facets_with_labels(facets: dict) -> dict:
    """给 facet 的每个候选补 `label`（值仍原样保留，供筛选回传）。"""
    out = {}
    for key, items in (facets or {}).items():
        # `key=key` 显式绑定循环变量（否则闭包晚绑定，ruff B023：所有分支共用最后一个 key）
        def _label(v: str, key: str = key) -> str:
            v = v or ""
            if key == "themes":
                # 空主题不是"没有"，而是"还没定主题"（外部 15471 条 / 内部 UNALIGNED）
                return _theme_label(v) or "（未定主题）"
            if key == "timeliness":
                return v or "（未核验）"
            return v or "（未标注）"

        out[key] = [{"value": it.get("value", ""), "label": _label(it.get("value", "")),
                     "count": it.get("count", 0)} for it in items]
    return out


def records_page(*, q: str = "", q_title: str = "", q_docno: str = "",
                 q_rfn: str = "", source: str = "", theme: str = "",
                 timeliness: str = "", sort: str = "publish_date",
                 desc: bool = True, page: int = 1, size: int = 50) -> dict:
    """「政策法规」页签：外部五源监管文件的**分页列表**（经 `base_api`）。"""
    try:
        from interfaces import base_api

        d = base_api.list_external_page(q=q, q_title=q_title, q_docno=q_docno,
                                        q_rfn=q_rfn, source=source, theme=theme,
                                        timeliness=timeliness, sort=sort,
                                        desc=desc, page=page, size=size)
        for it in d.get("items") or []:
            it["theme_label"] = _theme_label(it.get("theme", ""))
            # 访问地址已在底座（`url` 列，16602/16607 有值）；空值由前端显示"未查到"
        d["fetched_at"] = _now()
        d["source_name"] = "base_api.list_external_page"
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.list_external_page", e)


def records_facets() -> dict:
    """「政策法规」页签的筛选候选（源/主题/时效，带计数）。"""
    try:
        from interfaces import base_api

        d = _facets_with_labels(base_api.external_facets())
        d["fetched_at"] = _now()
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.external_facets", e)


def rules_page(*, q: str = "", q_title: str = "", q_docno: str = "",
               q_ipn: str = "", theme: str = "", sort: str = "title",
               desc: bool = False, page: int = 1, size: int = 50) -> dict:
    """「公司制度」页签：内部制度列表（含**本地文件**访问地址）。"""
    try:
        from interfaces import base_api
        from interfaces import worklist_fields as wf

        d = base_api.list_internal_page(q=q, q_title=q_title, q_docno=q_docno,
                                        q_ipn=q_ipn, theme=theme, sort=sort,
                                        desc=desc, page=page, size=size)
        for it in d.get("items") or []:
            it["theme_label"] = _theme_label(it.get("primary_theme", ""))
            # 本地文件：复用 `worklist_fields.local_for`（IPN→仓内相对路径的唯一实现，
            # 内含 178ms 首次建索引 + mtime 缓存；不在这里另写一套路径拼接）
            it["local_path"] = wf.local_for(str(it.get("ipn") or ""))
            raw = it.get("associated_rfns") or "[]"
            try:
                it["associated_rfns"] = json.loads(raw)
            except (TypeError, ValueError):
                it["associated_rfns"] = []
        d["fetched_at"] = _now()
        d["source_name"] = "base_api.list_internal_page"
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.list_internal_page", e)


def rules_facets() -> dict:
    """「公司制度」页签的筛选候选（主题，带计数）。"""
    try:
        from interfaces import base_api

        d = _facets_with_labels(base_api.internal_facets())
        d["fetched_at"] = _now()
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.internal_facets", e)


def relation_graph(ref: str) -> dict:
    """「关系图谱」页签：以 `ref` 为中心的一跳关系图（经 `relations_api.ego`）。

    图数据全部来自 `interfaces.relations_api`（事实源 `relations_index.jsonl` /
    治理库 `relation` 表）；本函数只做**展示补全**（主题标签、时间线着色所需字段），
    不改变关系语义。
    """
    try:
        from interfaces import relations_api

        # 未指定中心 ⇒ **全局视图**（2026-10-23：不再显示"请从列表进入"的空页）
        d = relations_api.global_graph() if not ref else relations_api.ego(ref)
        if d.get("available"):
            for nd in d.get("nodes") or []:
                nd["kind_label"] = ("监管文件" if nd.get("kind") == "regulatory"
                                    else "公司制度" if nd.get("kind") == "internal"
                                    else "")
            bp = d.get("bipartite") or {}
            for side in ("left", "right"):
                for nd in bp.get(side) or []:
                    nd["kind_label"] = ("公司制度" if side == "left" else "监管文件")
            d["fetched_at"] = _now()
            d["source_name"] = ("relations_api.global_graph" if not ref
                                else "relations_api.ego")
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("relations_api", e)


def feedback_items(*, status: str = "", kind: str = "", limit: int = 200) -> dict:
    """「留言板」页签：列出使用者反馈（含计数与中文标签）。"""
    try:
        from interfaces import feedback_api as FB

        d = FB.items(status=status, kind=kind, limit=limit)
        d["fetched_at"] = _now()
        d["source_name"] = "interfaces.feedback_api"
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("interfaces.feedback_api.items", e)


def regstats() -> dict:
    """「监管统计（EAST）」页签：**只呈现资料指针**，不做字段级校验。

    设计立场（2026-10-23）：库内与 EAST 直接相关的文件只有 **1 条**
    （金非银检函〔2025〕82号，保险业监管数据标准化规范），"监管统计"口径 3 条、
    "统计指标与报送" 8 条、金融统计制度 26 条。
    资料这么少的情况下，**硬造图表只会误导**（把 1 条数据画成趋势图等于撒谎）。
    因此本页定位为：**清单 + 覆盖度披露 + 缺口说明**，并明确写"项目尚未接入
    EAST 报文/字段级校验"。
    """
    try:
        from interfaces import base_api

        d = base_api.regstats_docs()
        for cat in d.get("categories") or []:
            for it in cat.get("items") or []:
                it["theme_label"] = _theme_label(it.get("theme", ""))
        d["fetched_at"] = _now()
        d["source_name"] = "base_api.regstats_docs"
        return d
    except Exception as e:  # noqa: BLE001
        return _degrade("base_api.regstats_docs", e)


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
            # `q_len < 3` 时**直接走 LIKE 兜底**：FTS5 trigram 只索引 3 字窗口，
            # 双字词（保险/银行/监管）`MATCH` 必然 0 行 —— 实测确认。
            # 这不是"没数据"，而是"检索器不支持这么短的词"，必须换路径而不是显示空。
            # 判定必须按**词元**（2026-10-23 修）：`len(q) < 3` 只看整串长度，
            # 于是"保险公司 营销"（9 字）被判为可走 FTS，但切词后含 2 字词元时
            # FTS 仍 0 命中 ⇒ 用户看到"搜了没结果"。
            short = not base_api.fts_capable(q)
            try:
                if short:
                    out["results"] = base_api.search_external_like(q, limit=limit)
                    out["backend"] = "external"
                    out["mode"] = "like"
                    out["note"] = ("检索词含 2 字以内词元：FTS5 trigram 无法匹配，"
                                   "已改用标题/文号模糊匹配（多词按 AND，不扫正文）")
                else:
                    out["results"] = base_api.search_external(q, limit=limit, kind=kind)
                    out["backend"] = "external"
                    out["mode"] = "fts"
            except Exception as e:  # noqa: BLE001 external 不可用 → 回退 internal
                out["error_external"] = f"{type(e).__name__}: {str(e)[:120]}"
                try:
                    out["results"] = base_api.search_internal(
                        q, limit=limit, kind="policies" if kind == "records" else "clauses")
                    out["backend"] = "internal"
                    out["mode"] = "fts"
                except Exception as e2:  # noqa: BLE001
                    out["available"] = False
                    out["reason"] = f"{type(e2).__name__}: {str(e2)[:160]}"
                    out["results"] = []
            # 长词 FTS 0 命中时也给一次 LIKE 兜底（正文里没有、但标题里有）
            if (not short) and not out["results"]:
                try:
                    alt = base_api.search_external_like(q, limit=limit)
                    if alt:
                        out["results"] = alt
                        out["mode"] = "like"
                        out["note"] = "全文索引 0 命中，已回退标题/文号模糊匹配"
                except Exception:  # noqa: BLE001
                    pass
            # 2026-10-22（用户要求 P6 列表增加"访问地址"）：逐条补 `url`。
            # **复用** `worklist_fields._Bridge.lookup`（带一致性校验）——
            # 不另写一套匹配，否则同一份文件在 P4 与 P6 会给出不同链接。
            try:
                from interfaces import worklist_fields as _wf

                for r in out["results"]:
                    if not isinstance(r, dict):
                        continue
                    title = str(r.get("title") or "")
                    docno = str(r.get("document_number") or "")
                    r["theme_label"] = _theme_label(r.get("theme") or r.get("primary_theme") or "")
                    if r.get("url"):
                        # 底座自带官方地址（`records.url`，16602/16607 有值）——
                        # **优先用它**。原先一律走桥表反查，而桥表只覆盖已登记 RFN 的
                        # 一部分 ⇒ 大量结果明明有官方链接却显示"未查到"（实测复现）。
                        r["url_src"] = "base"
                    else:
                        res = _wf.lookup(title, docno)
                        r["url"] = res["url"]
                        r["url_src"] = res["how"]
                        if res.get("note"):
                            r["url_note"] = res["note"]
                    # 内部制度（无 source_url）⇒ 给本地文件
                    ipn = str(r.get("ipn") or "")
                    if not r["url"] and ipn:
                        lp = _wf.local_for(ipn)
                        if lp:
                            r["local_path"] = lp
            except Exception as e:  # noqa: BLE001  补地址失败不影响检索本身
                out["url_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            # 同标题多版标注（2026-10-23）：同一部法可能真有多版（如《保险公司偿付能力
            # 管理规定》2008/2021 两个文号），也可能同一文件被两源重复收录。
            # **不静默合并**（合并会藏掉版本），改为标 `title_variants=N`，
            # 前端显示"同标题 N 版" —— 让读者判断是"版本"还是"重复"，而不是
            # 自己面对两条一模一样的标题去猜"这是检索垃圾吗"。
            _cnt: dict[str, int] = {}
            for r in out["results"]:
                t = str(r.get("title") or "")
                if t:
                    _cnt[t] = _cnt.get(t, 0) + 1
            for r in out["results"]:
                t = str(r.get("title") or "")
                if t and _cnt.get(t, 1) > 1:
                    r["title_variants"] = _cnt[t]
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


def release_stream(limit: int = 0) -> dict:
    """P8 发布事件流：git 提交 + SBOM + 版本契约，全部只读。

    2026-10-22（用户要求"变更记录应展示全部，不应仅展示近 20 条"）：
    `limit=0`（**新默认**）= 不截断。原默认 20 是硬编码上限，历史提交看不到。
    前端仍可传 `limit` 做窗口化，但**默认给全量**——"少给"必须以显式参数表达，
    不能由后端替用户决定"看 20 条就够"。
    """
    try:
        def _load():
            import subprocess

            out: dict = {"available": True, "fetched_at": _now(),
                         "source": "git log + reports/_sbom + contract_manifest"}
            args = ["git", "log", "--date=short", "--pretty=%h|%ad|%s"]
            if limit and limit > 0:
                args.insert(2, f"-{limit}")
            try:
                r = subprocess.run(args, cwd=paths.ROOT, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=60)
                commits = []
                for ln in (r.stdout or "").splitlines():
                    parts = ln.split("|", 2)
                    if len(parts) == 3:
                        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
                out["commits"] = commits
                out["commit_total"] = len(commits)
            except Exception as e:  # noqa: BLE001 git 不可用（非 git 检出）
                out["commits"] = []
                out["commit_total"] = 0
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


def commit_detail(sha: str) -> dict:
    """P8 单次提交的**完整变更**（`git show`：元信息 + 逐文件增删 + 完整提交说明）。

    用户报「点击提交，对应的主题应展示完整变更」——原来点提交**什么都不发生**
    （前端根本没绑定事件），而列表只给 20 条。现在：全量列表 + 点开看完整变更。

    只读、限时（60s）、`--no-color`（避免 ANSI 污染 JSON）。
    """
    sha = (sha or "").strip()
    # 只接受 7~40 位十六进制（防把任意字符串拼进 argv）
    import re

    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        return {"available": False, "reason": f"非法 sha：{sha[:20]!r}"}
    try:
        def _load():
            import subprocess

            fmt = "%H%n%an%n%ad%n%s%n%b"
            meta_r = subprocess.run(
                ["git", "show", "-s", "--no-color", "--date=iso",
                 f"--pretty=format:{fmt}", sha],
                cwd=paths.ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            if meta_r.returncode != 0:
                return {"available": False,
                        "reason": (meta_r.stderr or "").strip()[:200] or "git show 失败"}
            lines = (meta_r.stdout or "").splitlines()
            meta = {"sha": lines[0] if lines else sha,
                    "author": lines[1] if len(lines) > 1 else "",
                    "date": lines[2] if len(lines) > 2 else "",
                    "subject": lines[3] if len(lines) > 3 else "",
                    "body": "\n".join(lines[4:]).strip()}
            # 文件清单**单独一次调用**：`--stat` 与 `--name-status` 同用时，
            # 输出段之间**没有** `---` 分隔（实测），靠切分隔符必然拿到空列表
            # （初版 file_count 恒为 0 就是这么来的）。分开取最稳。
            files_r = subprocess.run(
                ["git", "show", "--no-color", "--pretty=format:", "--name-status", sha],
                cwd=paths.ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            files = []
            for ln in (files_r.stdout or "").splitlines():
                parts = ln.split("\t")
                if len(parts) >= 2 and re.match(r"^[AMDRTCU]\d*$", parts[0]):
                    files.append({"status": parts[0][0], "path": parts[-1].strip()})
            raw_r = subprocess.run(
                ["git", "show", "--no-color", "--stat", "--date=iso",
                 f"--pretty=format:{fmt}", sha],
                cwd=paths.ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            return {"available": True, "fetched_at": _now(),
                    "source": f"git show {sha[:12]}", **meta,
                    "files": files, "file_count": len(files),
                    "raw": (raw_r.stdout or "")[:20000]}
        return CACHE.get_or_call(f"commit:{sha}", _load, ttl=600.0)
    except Exception as e:  # noqa: BLE001
        return _degrade("git show", e)


#: `/api/local/open` 允许打开的**根目录前缀**（仓内相对 POSIX 路径）。
#: 白名单而非黑名单：确认台能用系统默认程序打开文件，等于"以当前用户权限执行一次打开"，
#: 绝不能让它成为任意文件打开器。
_LOCAL_ALLOW = (
    "modules/internal_policy_base/",
    "modules/regulatory_scrapers/data/docs/",
    "data/docs/",
    "reports/",
)
#: 允许的扩展名（可执行/脚本类一律拒绝，避免"点开一个 .exe"）。
_LOCAL_EXT = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                        ".txt", ".md", ".csv", ".json", ".html"})

#: 允许**浏览器内联显示**的扩展名 → MIME。
#: ⚠️ 刻意**不含** `.html` / `.svg`：它们会在确认台**同源**下执行脚本（存储型 XSS），
#: 而确认台是"无鉴权、可写待办"的面板 ⇒ 一旦被注入就能借用户身份点处置按钮。
#: 这类文件一律走 `attachment` 强制下载（见 `local_file`）。
_LOCAL_INLINE = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def local_file(rel_path: str) -> dict:
    """读取仓内文件供**浏览器**打开/下载（**不**拉起本机程序）。

    为什么不用「系统默认程序打开」（2026-10-22 的关键取舍）
    ------------------------------------------------
    最初实现调用系统默认程序让 Word/PDF 阅读器直接弹出。但本仓
    `tests/test_no_desktop_interference.py` 把该类调用列入**黑名单**
    （与「抢前台窗口」「模拟键鼠」同类：**会夺走用户焦点**；具体名单见该测试的
    黑名单常量，此处刻意不复述）。
    该纪律是硬约束，且理由成立——确认台是"后台观测面"，不该在你打字时弹窗口。

    改为**由服务端提供字节、浏览器自己处理**：
      · `.pdf/.txt/.md/.csv/.json` → `inline`（浏览器原生查看器，新标签页打开）；
      · 其余（`.docx/.xlsx/.pptx` 等）→ `attachment`（下载到"下载"目录，用户自行点开）。
    既满足"点一下就能看内容"，又不夺焦点、不引入外部程序依赖。

    安全边界（四重，任一不满足即拒绝并**给出可读原因**）：
      ① 必须是**仓内相对路径**（拒绝绝对路径/盘符）；
      ② 解析后必须仍在**仓根之下**（`..` 逃逸拒绝）；
      ③ 必须命中 `_LOCAL_ALLOW` 白名单目录；
      ④ 必须存在、是普通文件、扩展名在 `_LOCAL_EXT` 内。

    返回 `{ok, body, ctype, filename, disposition, path}` 或 `{ok:false, reason}`。
    """
    import os as _os

    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw:
        return {"ok": False, "reason": "缺 path"}
    if raw.startswith("/") or ":" in raw.split("/")[0]:
        return {"ok": False, "reason": "只接受仓内相对路径"}
    root_abs = _os.path.abspath(paths.ROOT)
    abs_path = _os.path.abspath(_os.path.join(root_abs, raw))
    if _os.path.commonpath([abs_path, root_abs]) != root_abs:
        return {"ok": False, "reason": "路径越界（不在仓内）"}
    rel = _os.path.relpath(abs_path, root_abs).replace("\\", "/")
    if not any(rel.startswith(pre) for pre in _LOCAL_ALLOW):
        return {"ok": False, "reason": f"该目录不在允许范围内：{rel.split('/')[0]}/…"}
    if not _os.path.isfile(abs_path):
        return {"ok": False, "reason": "文件不存在（可能已被移动/清理）"}
    ext = _os.path.splitext(abs_path)[1].lower()
    if ext not in _LOCAL_EXT:
        return {"ok": False, "reason": f"不支持该类型：{ext or '(无扩展名)'}"}
    try:
        with open(abs_path, "rb") as fh:
            body = fh.read()
    except OSError as e:
        return {"ok": False, "reason": f"读取失败：{type(e).__name__}: {e}"}
    return {"ok": True, "body": body,
            "ctype": _LOCAL_INLINE.get(ext, "application/octet-stream"),
            "disposition": "inline" if ext in _LOCAL_INLINE else "attachment",
            "filename": _os.path.basename(abs_path), "path": rel}
