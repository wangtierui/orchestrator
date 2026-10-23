# -*- coding: utf-8 -*-
"""interfaces.relations_api — 依据/废止关系产物的**统一读取入口**（R-F01）

纪律（与 `base_api` 同范式）
--------------------------
- 产物路径与解析方式**只在本模块定义**，消费方（classifier / base / drafter / 报告）
  一律经此读取，禁止各自拼路径或复刻筛选逻辑；
- 事实源唯一：`modules/regulatory_classifier/data/relations/relations_index.jsonl`；
  `cross_basis.jsonl` 为派生视图（类别 3 纯依据边），本模块两者都可读，但语义显式区分。

三类关系（用户要求明确列明）
--------------------------
| # | 类别 | `kind` 参数 |
|---|------|-----------|
| 1 | 监管文件的依据关系与废止关系 | `regulatory` |
| 2 | 内部制度的依据关系与废止关系 | `internal` |
| 3 | 内部制度 → 监管文件的依据关系 | `cross` |

用法
----
    from interfaces.relations_api import load, load_cross_basis, stat, by_src, by_dst

    for r in load("cross"):                    # 类别 3（内部制度依据了哪些监管文件）
        print(r["src_ref"], "→", r["dst_ref"] or r["dst_name"])
    stat()                                     # 生成元信息与质量指标
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths

RELATIONS_DIR = os.path.join(_ROOT, "modules", "regulatory_classifier", "data", "relations")
INDEX_PATH = paths.relations_index()
CROSS_PATH = os.path.join(RELATIONS_DIR, "cross_basis.jsonl")
STAT_PATH = os.path.join(RELATIONS_DIR, "relations_stat.json")

KINDS = ("all", "regulatory", "internal", "cross")


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# --------------------------------------------------------------------------- #
# 阶段 3（2026-09-18）：读取路径升级 —— **优先查治理库 `relation` 表**（索引下推），
# 事实源 JSONL 退为回退路径。
#
# 动因（方案 §2.2 G7）：原实现下 `by_src()` / `by_dst()` **每次调用都重读整个 7 MB
# JSONL** 再在 Python 端线性过滤（O(N)/次，且 `load()` 全量进内存）。治理库 `relation`
# 表已按 src_ref/src_key/dst_ref/dst_key/relation/dst_class 建索引 → 单次查询 O(log N)。
#
# 一致性：治理库是**投影**（阶段 2，唯一写口 tools/governance_sync.py），与事实源
# 同源；`governance verify` 的比对断言保证二者不漂移。DB 不可用/表为空 → 回退读文件，
# 保证"库没建也能跑"（与 base_api 的显式报错不同：关系读取属分析路径，降级不阻断）。
# --------------------------------------------------------------------------- #
def _db_rows(where: str = "", args=(), limit: int = 0) -> list[dict] | None:
    """经治理库取关系行（`row_json` 还原原行）；不可用返回 None（调用方回退文件）。"""
    try:
        from std_lib.common_lib import governance_store as gs

        if not gs.enabled() or gs.table_count("relation") == 0:
            return None
        sql = "SELECT row_json FROM relation"
        if where:
            sql += f" WHERE {where}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with gs.connect(readonly=True) as c:
            rows = []
            for (rj,) in c.execute(sql, args):
                try:
                    rows.append(json.loads(rj))
                except ValueError:
                    continue
            return rows
    except Exception:  # noqa: BLE001  DB 异常不得阻断关系读取（回退文件）
        return None


def _all_rows() -> list[dict]:
    """关系全量行：优先治理库，回退事实源 JSONL。"""
    rows = _db_rows()
    return rows if rows is not None else _read_jsonl(INDEX_PATH)


def load(kind: str = "all") -> list[dict]:
    """读取关系（`kind` ∈ KINDS）。

    - `regulatory`：类别 1（`src_kind=regulatory`）
    - `internal`  ：类别 2（`src_kind=internal` 且 `dst_kind=internal`）
    - `cross`     ：类别 3（内部制度 → 监管文件；含依据与废止，纯依据边见 `load_cross_basis`）
    - `all`       ：事实源全量
    """
    if kind not in KINDS:
        raise ValueError(f"kind 须 ∈ {KINDS}，得到 {kind!r}")
    if kind == "all":
        return _all_rows()
    # 分类下推 SQL（避免全量进内存后在 Python 端过滤）
    cond = {
        "regulatory": ("src_kind='regulatory'", ()),
        "internal": ("src_kind='internal' AND dst_kind='internal'", ()),
        "cross": ("src_kind='internal' AND dst_kind='regulatory'", ()),
    }[kind]
    rows = _db_rows(cond[0], cond[1])
    if rows is not None:
        return rows
    rows = _read_jsonl(INDEX_PATH)
    if kind == "regulatory":
        return [r for r in rows if r.get("src_kind") == "regulatory"]
    if kind == "internal":
        return [
            r for r in rows if r.get("src_kind") == "internal" and r.get("dst_kind") == "internal"
        ]
    return [
        r for r in rows if r.get("src_kind") == "internal" and r.get("dst_kind") == "regulatory"
    ]


def load_cross_basis() -> list[dict]:
    """类别 3 的**纯依据**派生视图（`relation=basis`），供 drafter/base 高频消费。"""
    return _read_jsonl(CROSS_PATH)


def stat() -> dict:
    """生成元信息与质量指标（事实源缺失时返回 {}）。"""
    if not os.path.exists(STAT_PATH):
        return {}
    try:
        return json.load(open(STAT_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def by_src(ref: str) -> list[dict]:
    """按源实体取关系（RFN 或 IPN）。**库可用时走索引查询**（原每次重读 7 MB 全表）。"""
    if not ref:
        return []
    rows = _db_rows("src_ref=? OR src_key=?", (ref, ref))
    if rows is not None:
        return rows
    return [
        r for r in _read_jsonl(INDEX_PATH) if r.get("src_ref") == ref or r.get("src_key") == ref
    ]


def by_dst(ref: str) -> list[dict]:
    """按目标实体取关系（RFN 或 IPN）——"谁依据/废止了我"的反向查询。

    `dst_ref` 命中为强匹配；`dst_key` 命中属"已采集未登记 RFN"的弱线索（口径见
    方案 §5：两级解析语义不同，消费方须自行区分）。
    """
    if not ref:
        return []
    rows = _db_rows("dst_ref=? OR dst_key=?", (ref, ref))
    if rows is not None:
        return rows
    return [
        r for r in _read_jsonl(INDEX_PATH) if r.get("dst_ref") == ref or r.get("dst_key") == ref
    ]


def summary() -> dict:
    """轻量汇总（供 `relations status` / 报告复用；不重复计算事实）。"""
    s = stat()
    if not s:
        return {"available": False, "index_path": INDEX_PATH}
    return {
        "available": True,
        "relations": s.get("relations", {}),
        "resolution": s.get("resolution", {}),
        "resolved_to_entity_ratio": s.get("resolved_to_entity_ratio"),
        "located_in_corpus_ratio": s.get("located_in_corpus_ratio"),
        "generated_at": s.get("generated_at"),
        "extractor_version": s.get("extractor_version"),
    }


#: 关系类型 → 中文标签（唯一处；前端不再自建映射，避免"依据/引用"两套说法）
RELATION_LABELS = {"basis": "依据", "repeal": "废止"}


def ego(ref: str, *, limit: int = 200, include_weak: bool = False) -> dict:
    """某实体的**自我中心关系图**（1 跳）：出边 + 入边 → `{nodes, edges, counts}`。

    为什么需要（2026-10-23，确认台「关系图谱」页签）
    ------------------------------------------------
    关系事实早已存在（4197 条），但确认台只能看"某条待办"，看不到"这份文件
    依据了谁 / 被谁依据"。页签要的是**一张图**，即"以选中文件为中心的一跳邻域"，
    而 `by_src`/`by_dst` 各给一半（出边/入边）⇒ 需要在这里合成并**去重编号**。

    参数
    ----
    ref : RFN / IPN
    limit : 边数上限（默认 200）。**必须设上限**：个别上位法被上千份文件依据，
        全量返回会让前端一次画上千条边（不可读也没必要）。
    include_weak : 是否包含"仅 `dst_key` 命中"的弱线索（已采集未登记 RFN）。
        默认排除——弱线索的 `dst_ref` 为空，画成节点会把"未登记"伪装成"已定位"。

    返回::

        {"available": bool, "ref": str, "center": {…},
         "nodes": [{"id","kind","name","docno","center","is_weak"}…],
         "edges": [{"id","from","to","relation","label","confidence",
                    "matched_by","snippet","is_weak"}…],
         "counts": {"out": n, "in": n, "basis": n, "repeal": n,
                    "truncated": bool},
         "labels": {"basis": "依据", "repeal": "废止"}}
    """
    if not ref:
        return {"available": False, "reason": "缺 ref", "ref": "",
                "nodes": [], "edges": [], "counts": {}}

    def _keep(row: dict) -> bool:
        """弱线索（dst_ref 为空且靠 dst_key 命中）是否保留。"""
        if include_weak:
            return True
        return bool(row.get("dst_ref"))

    raw = [r for r in by_src(ref) if _keep(r)] + [r for r in by_dst(ref) if _keep(r)]
    truncated = len(raw) > limit
    raw = raw[:limit]

    nodes: dict[str, dict] = {}

    def _node(nid: str, kind: str, name: str, docno: str, *, center=False,
              weak=False) -> str:
        if not nid:
            return ""
        cur = nodes.get(nid)
        if cur is None:
            nodes[nid] = {"id": nid, "kind": kind or "", "name": name or "",
                          "docno": docno or "", "center": center, "is_weak": weak}
        else:
            # 同一节点在出/入边里可能只带片面信息（某边缺 dst_docno）⇒ 逐字段补全。
            # ⚠️ `kind` 也必须补：中心节点是先以空 kind 占位的（`_node(ref, "", …)`），
            #    若这里只补 name/docno，中心节点的 kind 会永远为空 —— 前端无法区分
            #    "监管文件"与"公司制度"（实测踩到）。
            if not cur.get("kind") and kind:
                cur["kind"] = kind
            if not cur.get("name") and name:
                cur["name"] = name
            if not cur.get("docno") and docno:
                cur["docno"] = docno
        return nid

    _node(ref, "", "", "", center=True)

    edges: list[dict] = []
    seen: set[str] = set()
    for r in raw:
        rid = str(r.get("relation_id") or "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        dst_ref = str(r.get("dst_ref") or "")
        dst_key = str(r.get("dst_key") or "")
        weak = not dst_ref and bool(dst_key)
        to_id = _node(dst_ref, str(r.get("dst_kind") or ""),
                      str(r.get("dst_name") or ""), str(r.get("dst_docno") or ""),
                      weak=weak)
        if weak:
            # 弱线索没有 RFN：用 `key:` 前缀造稳定 id（同名文件多次出现仍指向同一节点）
            to_id = "key:" + dst_key
            _node(to_id, "regulatory", str(r.get("dst_name") or ""),
                  str(r.get("dst_docno") or ""), weak=True)
        from_id = _node(str(r.get("src_ref") or ""), str(r.get("src_kind") or ""),
                        str(r.get("src_name") or ""), str(r.get("src_docno") or ""))
        if not to_id or not from_id:
            continue
        rel = str(r.get("relation") or "")
        edges.append({
            "id": rid or f"{from_id}->{to_id}:{rel}",
            "from": from_id, "to": to_id, "relation": rel,
            "label": RELATION_LABELS.get(rel, rel),
            "confidence": r.get("confidence", ""),
            "matched_by": r.get("matched_by", ""),
            "snippet": (r.get("source_snippet") or "")[:120],
            "is_weak": weak,
        })

    # 中心节点信息从任一条关系里补（relation 行同时带 src_name/src_docno）
    center = nodes.get(ref, {"id": ref, "kind": "", "name": "", "docno": "",
                             "center": True, "is_weak": False})
    for r in raw:
        if str(r.get("src_ref") or "") == ref and not center.get("name"):
            center["name"] = str(r.get("src_name") or "")
            center["docno"] = str(r.get("src_docno") or "")
            center["kind"] = str(r.get("src_kind") or "")
        if str(r.get("dst_ref") or "") == ref and not center.get("name"):
            center["name"] = str(r.get("dst_name") or "")
            center["docno"] = str(r.get("dst_docno") or "")
            center["kind"] = str(r.get("dst_kind") or "")
    nodes[ref] = center

    out_all = by_src(ref)
    in_all = by_dst(ref)
    drawn_out = sum(1 for e in edges if e["from"] == ref)
    drawn_in = sum(1 for e in edges if e["to"] == ref)
    return {
        "available": True, "ref": ref, "center": center,
        "nodes": list(nodes.values()), "edges": edges,
        "counts": {
            # 口径：**实际画出**的边数（不是原始查询条数）——
            # 否则"图上 4 条边、计数说 13"会让读者以为渲染丢了边。
            "out": drawn_out, "in": drawn_in,
            "basis": sum(1 for e in edges if e["relation"] == "basis"),
            "repeal": sum(1 for e in edges if e["relation"] == "repeal"),
            # 被排除的弱线索（仅 dst_key 命中、无 dst_ref）——**显式披露**，不静默丢
            "weak_skipped": (0 if include_weak else
                             sum(1 for r in out_all + in_all if not r.get("dst_ref"))),
            "raw_out": len(out_all), "raw_in": len(in_all),
            "truncated": truncated,
        },
        "labels": dict(RELATION_LABELS),
    }


def _nmaps(rows: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """从关系行里收集 `ref → (名称, 文号)`（同一实体在多行里可能只带片面信息）。"""
    name, docno = {}, {}
    for r in rows:
        for side in ("src", "dst"):
            ref = str(r.get(f"{side}_ref") or "")
            if not ref:
                continue
            if r.get(f"{side}_name") and ref not in name:
                name[ref] = str(r[f"{side}_name"])
            if r.get(f"{side}_docno") and ref not in docno:
                docno[ref] = str(r[f"{side}_docno"])
    return name, docno


def global_graph(*, top_hubs: int = 15, top_theme_docs: int = 12) -> dict:
    """**全局关系图谱**：四类关系各给一个可读视图（2026-10-23 重构）。

    为什么不是一个"全量图"
    ----------------------
    实测 5281 条关系里：监管↔监管 **5139**、制度↔制度 **24**、制度→监管 **118**。
    全量同屏 = 一张不可读的黑网（且多数是"某通知依据《保险法》"的中心辐射）。
    但"画不下"不等于"不显示"——**四类关系都必须能被看见**，只是呈现方式不同：

    | # | 视图 | 数据量 | 呈现策略 |
    |---|---|---|---|
    | 1 | 制度 → 监管 | 118（可定位 25） | **全画**（25 条不抽样）+ 未定位条数显式披露 |
    | 2 | 监管 ↔ 监管 | 5139 / 417 节点 | **枢纽星型**：Top N 枢纽按被引次数定大小，枢纽间直连边照画 |
    | 3 | 制度 ↔ 制度 | 24 | **全画**（弦图布局，24 条可读） |
    | 4 | 主题关系 | 10 主题 × 12 文件 | **二部图**：主题 → 被其制度引用最多的监管文件 |

    诚实纪律（不变）：`dst_ref` 为空的关系**不伪造节点**，但**条数必须显式给出**
    ——"未定位"与"不存在"是两件事，混为一谈会让读者得出错误结论。
    """
    from interfaces import base_api as _B

    reg_rows = load("regulatory")
    int_rows = load("internal")
    cross_rows = load("cross")

    reg_name, reg_docno = _nmaps(reg_rows)
    int_name, int_docno = _nmaps(int_rows)
    all_name = {**reg_name, **int_name}
    all_docno = {**reg_docno, **int_docno}

    def _uniq(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
        out: dict[tuple[str, str, str], dict] = {}
        for r in rows:
            s, d = str(r.get("src_ref") or ""), str(r.get("dst_ref") or "")
            if not s or not d:
                continue
            out.setdefault((s, d, str(r.get("relation") or "")), r)
        return out

    # ---- 视图 1：制度 → 监管（全画）----
    cross_edges: list[dict] = []
    left: dict[str, dict] = {}
    right: dict[str, dict] = {}
    for (s, d, rel), r in _uniq(cross_rows).items():
        left.setdefault(s, {"id": s, "kind": "internal",
                            "name": all_name.get(s, ""), "docno": all_docno.get(s, "")})
        right.setdefault(d, {"id": d, "kind": "regulatory",
                             "name": all_name.get(d, ""), "docno": all_docno.get(d, "")})
        cross_edges.append({"from": s, "to": d, "relation": rel,
                            "label": RELATION_LABELS.get(rel, rel),
                            "confidence": r.get("confidence", ""),
                            "matched_by": r.get("matched_by", "")})
    # 三桶口径（2026-10-23）：把「未定位」与「语料内但未登记 RFN」分开 ——
    # 前者是**找不到**（对方不在库里），后者是**找到了没登记**（可补登）。
    # 混在一起会让读者以为三千多条都无从下手，其实其中一部分只差一次登记。
    def _buckets(rows: list[dict]) -> dict[str, int]:
        ent = sum(1 for r in rows if r.get("dst_ref"))
        corp = sum(1 for r in rows if not r.get("dst_ref") and r.get("dst_key"))
        unres = sum(1 for r in rows if not r.get("dst_ref") and not r.get("dst_key"))
        return {"entity": ent, "corpus": corp, "unresolved": unres}

    cross_b, reg_b, int_b = _buckets(cross_rows), _buckets(reg_rows), _buckets(int_rows)
    cross_unresolved = cross_b["unresolved"] + cross_b["corpus"]

    # ---- 视图 2：监管 → 监管（枢纽星型）----
    deg: dict[str, int] = {}
    deg_kind: dict[str, dict[str, int]] = {}
    for r in reg_rows:
        d = str(r.get("dst_ref") or "")
        if not d:
            continue
        deg[d] = deg.get(d, 0) + 1
        k = deg_kind.setdefault(d, {"basis": 0, "repeal": 0})
        k[str(r.get("relation") or "basis")] = k.get(str(r.get("relation") or "basis"), 0) + 1
    hub_ids = [k for k, _ in sorted(deg.items(), key=lambda kv: -kv[1])[:top_hubs]]
    hub_set = set(hub_ids)
    hub_nodes = [{"id": h, "kind": "regulatory", "name": all_name.get(h, ""),
                  "docno": all_docno.get(h, ""), "degree": deg.get(h, 0),
                  "basis": deg_kind.get(h, {}).get("basis", 0),
                  "repeal": deg_kind.get(h, {}).get("repeal", 0)} for h in hub_ids]
    hub_edges = [{"from": s, "to": d, "relation": rel,
                  "label": RELATION_LABELS.get(rel, rel)}
                 for (s, d, rel) in _uniq(reg_rows) if s in hub_set and d in hub_set]
    reg_unresolved = reg_b["unresolved"] + reg_b["corpus"]

    # ---- 视图 3：制度 ↔ 制度（全画）----
    ii_edges: list[dict] = []
    ii_nodes: dict[str, dict] = {}
    for (s, d, rel), r in _uniq(int_rows).items():
        ii_nodes.setdefault(s, {"id": s, "kind": "internal",
                                "name": all_name.get(s, ""), "docno": all_docno.get(s, "")})
        ii_nodes.setdefault(d, {"id": d, "kind": "internal",
                                "name": all_name.get(d, ""), "docno": all_docno.get(d, "")})
        ii_edges.append({"from": s, "to": d, "relation": rel,
                         "label": RELATION_LABELS.get(rel, rel),
                         "confidence": r.get("confidence", "")})
    ii_unresolved = int_b["unresolved"] + int_b["corpus"]

    # ---- 视图 4：主题关系（主题 → 被其制度引用最多的监管文件）----
    # 口径：内部制度 `primary_theme`（T1/T9/… 或 UNALIGNED）→ 其"依据"关系指向的监管文件。
    # 只在**已定位**（dst_ref 非空）时计数；未定位的不计入主题边（否则主题间会被
    # 同一批"未定位"虚连，看着像真有关系）。
    theme_of: dict[str, str] = {}
    try:
        # ⚠️ `limit=0` 返回 **0 行**（不是"不限"）—— 实测踩到，主题视图整页空白。
        for p in _B.query_internal(limit=2000):
            ipn = str(p.get("ipn") or "")
            if ipn:
                theme_of[ipn] = str(p.get("primary_theme") or "") or "UNALIGNED"
    except Exception:  # noqa: BLE001  主题视图失败不影响其它三视图
        theme_of = {}
    theme_label_map: dict[str, str] = {}
    try:
        from interfaces.theme_api import THEME_MAP_P0

        theme_label_map = dict(THEME_MAP_P0)
    except Exception:  # noqa: BLE001
        theme_label_map = {}

    pair: dict[tuple[str, str], int] = {}
    for r in cross_rows:
        if not r.get("dst_ref"):
            continue
        t = theme_of.get(str(r.get("src_ref") or ""))
        if not t:
            continue
        pair[(t, str(r["dst_ref"]))] = pair.get((t, str(r["dst_ref"])), 0) + 1
    doc_freq: dict[str, int] = {}
    for (_t, d), n in pair.items():
        doc_freq[d] = doc_freq.get(d, 0) + n
    top_docs = [d for d, _ in sorted(doc_freq.items(), key=lambda kv: -kv[1])[:top_theme_docs]]
    keep_docs = set(top_docs)
    theme_edges = [{"theme": t, "ref": d, "count": n, "label": RELATION_LABELS["basis"]}
                   for (t, d), n in sorted(pair.items(), key=lambda kv: -kv[1])
                   if d in keep_docs]
    theme_counts: dict[str, int] = {}
    for t in theme_of.values():
        theme_counts[t] = theme_counts.get(t, 0) + 1
    themes = [{"code": t, "label": theme_label_map.get(t, t), "policies": n}
              for t, n in sorted(theme_counts.items(), key=lambda kv: -kv[1])]

    s = stat() or {}
    res = s.get("resolution") or {}
    rel = s.get("relations") or {}
    by_cross = rel.get("by_cross") or {}
    counts = {
        "documents": len(left) + len(right) + len(ii_nodes),
        "edges": len(cross_edges) + len(ii_edges),
        "hubs": len(hub_nodes),
        "unresolved": int(res.get("unresolved") or 0),
        "regulatory_relations": int(by_cross.get("regulatory->regulatory") or 0),
        "internal_relations": int(by_cross.get("internal->internal") or 0),
        "cross_relations": int(by_cross.get("internal->regulatory") or 0),
        "total_relations": int(rel.get("total") or 0),
        # 各视图的"未定位"条数（**必须逐视图披露**：总盘子的 2462 条对读者没意义）
        "unresolved_by_view": {
            "internal_to_regulatory": cross_b,
            "regulatory_to_regulatory": reg_b,
            "internal_to_internal": int_b,
        },
        # 口径：分母剔除 organ/generic（机关名/泛指词不是文件）
        "file_denominator": int(s.get("file_level_denominator") or 0),
        "file_resolved_ratio": s.get("file_resolved_ratio"),
        "file_located_ratio": s.get("file_located_ratio"),
        "resolved_to_entity_ratio": s.get("resolved_to_entity_ratio"),
        "located_in_corpus_ratio": s.get("located_in_corpus_ratio"),
    }
    views = [
        {"key": "internal_to_regulatory", "label": "公司制度 → 监管依据",
         "edges": len(cross_edges), "nodes": len(left) + len(right),
         "total": counts["cross_relations"], "unresolved": cross_unresolved,
         "note": "可定位的全部画出（不抽样）；未定位的只计数不伪造节点"},
        {"key": "regulatory_to_regulatory", "label": "监管文件 → 监管文件",
         "edges": len(hub_edges), "nodes": len(hub_nodes),
         "total": counts["regulatory_relations"], "unresolved": reg_unresolved,
         "note": f"5139 条里按被依据次数取 Top {len(hub_nodes)} 枢纽；"
                 f"枢纽间直连 {len(hub_edges)} 条照画，其余用度数（节点大小）表达"},
        {"key": "internal_to_internal", "label": "公司制度 → 公司制度",
         "edges": len(ii_edges), "nodes": len(ii_nodes),
         "total": counts["internal_relations"], "unresolved": ii_unresolved,
         "note": "全部画出（24 条可读）——含 16 条废止（新版废止旧版）"},
        {"key": "theme", "label": "主题关系",
         "edges": len(theme_edges), "nodes": len(themes) + len(keep_docs),
         "total": sum(n for (_t, d), n in pair.items() if d in keep_docs),
         "unresolved": sum(1 for r in cross_rows if not r.get("dst_ref")),
         "note": "主题 → 被该主题制度引用最多的监管文件（只计已定位）"},
    ]
    return {
        "available": True,
        "mode": "global",
        "ref": "",
        "views": views,
        "bipartite": {"left": sorted(left.values(), key=lambda x: x["name"]),
                      "right": sorted(right.values(), key=lambda x: x["name"]),
                      "edges": cross_edges},
        "regulatory": {"nodes": hub_nodes, "edges": hub_edges,
                       "total_edges": counts["regulatory_relations"]},
        "internal": {"nodes": sorted(ii_nodes.values(), key=lambda x: x["name"]),
                     "edges": ii_edges},
        "theme": {"themes": themes,
                  "docs": [{"id": d, "kind": "regulatory", "name": all_name.get(d, ""),
                            "docno": all_docno.get(d, ""), "degree": doc_freq.get(d, 0)}
                           for d in top_docs],
                  "edges": theme_edges},
        "hubs": hub_nodes,
        "counts": counts,
        "stats": {"resolution": res,
                  "resolved_to_entity_ratio": s.get("resolved_to_entity_ratio"),
                  "file_resolved_ratio": s.get("file_resolved_ratio")},
        "labels": dict(RELATION_LABELS),
        "note": (f"全库 {counts['total_relations']} 条关系：监管↔监管 "
                 f"{counts['regulatory_relations']}、制度↔制度 "
                 f"{counts['internal_relations']}、制度→监管 "
                 f"{counts['cross_relations']}。"
                 f"文件类目标定位率 {round((counts['file_resolved_ratio'] or 0) * 100, 1)}%"
                 f"（分母 {counts['file_denominator']}，已剔除机关名/泛指词）。"
                 f"未定位 {counts['unresolved']} 条在四个视图里逐项披露。"),
    }


if __name__ == "__main__":  # 离线自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
