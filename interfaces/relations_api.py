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
    from interfaces import external_api as _EXT_E
    from std_lib.common_lib.relations.patterns import TARGET_ORGAN as _TARGET_ORGAN_E

    if not ref:
        return {"available": False, "reason": "缺 ref", "ref": "",
                "nodes": [], "edges": [], "counts": {}}

    def _keep(row: dict) -> bool:
        """该行是否画进图。

        2026-10-23（第一百一十三批）：**语料外文件**（`dst_class=external`）现在
        有编号（`RFN-EXT-*`）⇒ 可以画；机关名仍然不画（它不是一个文件）。
        弱线索（只有 `dst_key`、无 `dst_ref`）保持原语义：默认不画（那是"已采集
        未登记"，画出来会把未登记伪装成已定位），`include_weak` 时才画。
        """
        if row.get("dst_ref"):
            return True
        if str(row.get("dst_class") or "") == _TARGET_ORGAN_E:
            return False
        if row.get("dst_key"):
            return bool(include_weak)
        return bool(_EXT_E.resolve(row.get("dst_name"), row.get("dst_docno")))

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
        external = False
        if not dst_ref and not dst_key:
            # 语料外文件：用 `RFN-EXT-*` 作节点 id（可重算、跨批稳定）
            ext_id = _EXT_E.resolve(r.get("dst_name"), r.get("dst_docno"))
            if not ext_id:
                continue
            dst_ref = ext_id
            external = True
        to_id = _node(dst_ref, "external" if external else str(r.get("dst_kind") or ""),
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
        if external:
            nodes[to_id]["external"] = True
            nodes[to_id]["guise"] = _EXT_E.guess_guise(r.get("dst_name"),
                                                       r.get("dst_docno"))
            nodes[to_id]["guise_label"] = _EXT_E.GUISE_LABELS.get(
                nodes[to_id]["guise"], "")
        rel = str(r.get("relation") or "")
        edges.append({
            "id": rid or f"{from_id}->{to_id}:{rel}",
            "from": from_id, "to": to_id, "relation": rel,
            "label": RELATION_LABELS.get(rel, rel),
            "confidence": r.get("confidence", ""),
            "matched_by": "external" if external else r.get("matched_by", ""),
            "snippet": (r.get("source_snippet") or "")[:120],
            "is_weak": weak,
            "external": external,
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
    # T2：中心与邻居的名称统一回填（缺名/名为编码都回查底座）
    _fill_node_names(list(nodes.values()))
    if nodes[ref].get("name"):
        center["name"] = nodes[ref]["name"]

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
            # 画出来的**外部法规**边（编号 RFN-EXT-*）：读者要能一眼知道
            # "这条依据指向的是库外文件"，而不是以为库里就有这份法。
            "external": sum(1 for e in edges if e.get("external")),
            # 被排除的弱线索（仅 dst_key 命中、无 dst_ref）——**显式披露**，不静默丢
            "weak_skipped": (0 if include_weak else
                             sum(1 for r in out_all + in_all
                                 if not r.get("dst_ref") and r.get("dst_key"))),
            # 机关名（"国务院"）不是文件 ⇒ 既不画也不叫"未定位"
            "organ_skipped": sum(1 for r in out_all + in_all
                                 if str(r.get("dst_class") or "")
                                 == _TARGET_ORGAN_E),
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


def _fill_node_names(nodes: list[dict]) -> None:
    """就地补齐节点名称（缺失/等于编码时回查底座）。

    判定"缺名"要连**名称等于编码**也算：关系抽取在无法确定标题时会写
    `dst_name = dst_ref`（早期实现如此），只判空会把它们漏掉。
    """
    need_rfn = [n["id"] for n in nodes
                if str(n.get("id", "")).startswith("RFN-")
                and not str(n.get("id", "")).startswith("RFN-EXT-")
                and (not str(n.get("name") or "").strip()
                     or str(n.get("name")) == str(n["id"]))]
    need_ipn = [n["id"] for n in nodes
                if str(n.get("id", "")).startswith("IPN-")
                and (not str(n.get("name") or "").strip()
                     or str(n.get("name")) == str(n["id"]))]
    if not need_rfn and not need_ipn:
        return
    from interfaces import base_api

    m = base_api.titles_by_refs(rfns=need_rfn, ipns=need_ipn)
    for n in nodes:
        title = m.get(str(n.get("id") or ""), "")
        if title and (not str(n.get("name") or "").strip()
                      or str(n.get("name")) == str(n["id"])):
            n["name"] = title
            n["name_from"] = "base"


def global_graph(*, top_hubs: int = 15, top_theme_docs: int = 12,
                 top_ext_n: int = 20) -> dict:
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
    from interfaces import external_api as _E
    from std_lib.common_lib.relations.patterns import (
        TARGET_CORPUS as _TARGET_CORPUS,
    )
    from std_lib.common_lib.relations.patterns import (
        TARGET_ORGAN as _TARGET_ORGAN,
    )

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

    # ---- 视图 1 补充：**语料外**目标也画出来（第一百一十三批）----
    # 原先这些边被整段丢弃（只计数）：读者看得到"未定位 93 条"，却不知道
    # 那 93 条到底指向谁。现在它们有编号（`RFN-EXT-*`）与标题，可以成为节点，
    # 只是**视觉上必须与语料内节点可分**（`external=True`，前端虚线/浅色）。
    ext_seen: dict[str, dict] = {}
    for r in cross_rows:
        if r.get("dst_ref") or r.get("dst_key"):
            continue
        if str(r.get("dst_class") or "") == _TARGET_ORGAN:
            continue                      # 机关名不是文件，不画
        ext_id = _E.resolve(r.get("dst_name"), r.get("dst_docno"))
        if not ext_id:
            continue
        src = str(r.get("src_ref") or "")
        if not src:
            continue
        ext_seen.setdefault(ext_id, {
            "id": ext_id, "kind": "external", "external": True,
            "name": str(r.get("dst_name") or ""),
            "docno": str(r.get("dst_docno") or ""),
        })
        left.setdefault(src, {"id": src, "kind": "internal",
                              "name": all_name.get(src, ""),
                              "docno": all_docno.get(src, "")})
        right.setdefault(ext_id, ext_seen[ext_id])
        cross_edges.append({"from": src, "to": ext_id,
                            "relation": str(r.get("relation") or ""),
                            "label": RELATION_LABELS.get(
                                str(r.get("relation") or ""), ""),
                            "confidence": r.get("confidence", ""),
                            "matched_by": "external", "external": True})
    # 四桶口径（2026-10-23 扩，第一百一十三批）：把"目标是什么"彻底分开 ——
    #   entity   已解析到语料实体（RFN/IPN）
    #   corpus   语料内已采集但未登记 RFN ⇒ **可补登记**（不是"找不到"）
    #   organ    **机关名**（"国务院"/"财政部"）⇒ 不是文件引用，不该计入文件覆盖率
    #   external **语料外文件**（法律法规…）⇒ 登记进 `RFN-EXT-*` 独立键空间
    # 前三批把 external 与 corpus 混算成"未定位 2632"，读者会以为全都是坏账；
    # 实际其中 509 条只差一次 RFN 补登记、1068 条根本不该算。
    def _buckets(rows: list[dict]) -> dict[str, int]:
        b = {"entity": 0, "corpus": 0, "organ": 0, "external": 0}
        for r in rows:
            if r.get("dst_ref"):
                b["entity"] += 1
                continue
            cls = str(r.get("dst_class") or "")
            if cls == _TARGET_ORGAN:
                b["organ"] += 1
            elif cls == _TARGET_CORPUS or r.get("dst_key"):
                b["corpus"] += 1
            else:
                # 兜底：无 class 字段的老数据 / 明确 external，都归外部文件
                b["external"] += 1
        return b

    cross_b, reg_b, int_b = _buckets(cross_rows), _buckets(reg_rows), _buckets(int_rows)
    cross_unresolved = cross_b["corpus"]   # 只剩"待补登记"，外部法规已另行登记

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
    reg_unresolved = reg_b["corpus"]

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
    # 制度↔制度 同样把"语料外"目标画出来（多为**历史版本**：新版废止旧版，
    # 而旧版已不在库内）。这批若仍藏起来，"制度间关系只有 24 条"就是假象。
    for r in int_rows:
        if r.get("dst_ref") or r.get("dst_key"):
            continue
        if str(r.get("dst_class") or "") == _TARGET_ORGAN:
            continue
        ext_id = _E.resolve(r.get("dst_name"), r.get("dst_docno"))
        src = str(r.get("src_ref") or "")
        if not ext_id or not src:
            continue
        ii_nodes.setdefault(src, {"id": src, "kind": "internal",
                                  "name": all_name.get(src, ""),
                                  "docno": all_docno.get(src, "")})
        ii_nodes.setdefault(ext_id, {"id": ext_id, "kind": "external",
                                     "external": True,
                                     "name": str(r.get("dst_name") or ""),
                                     "docno": str(r.get("dst_docno") or "")})
        rel = str(r.get("relation") or "")
        ii_edges.append({"from": src, "to": ext_id, "relation": rel,
                         "label": RELATION_LABELS.get(rel, rel),
                         "confidence": r.get("confidence", ""),
                         "matched_by": "external", "external": True})
    ii_unresolved = int_b["corpus"]

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

    # ---- 视图 5：外部法规权威（独立键空间 `RFN-EXT-*`）----
    # 这一屏回答的是"我们的文件到底在依据什么法"。它们是**语料外**的：
    # 编号只表示"被引用过、已登记"，**不表示库里能打开这份文件**。
    ext_stat = _E.load_stat()
    ext_all = _E.load()
    top_ext = sorted(ext_all, key=lambda x: -int(x.get("evidence_count") or 0))[:top_ext_n]
    ext_nodes = [{
        "id": str(x.get("ext_id") or ""),
        "kind": "external", "external": True,
        "name": str(x.get("title") or ""),
        "docno": str(x.get("docno") or ""),
        "degree": int(x.get("evidence_count") or 0),
        "guise": str(x.get("guise") or "unknown"),
        "guise_label": _E.GUISE_LABELS.get(str(x.get("guise") or ""), ""),
        "in_corpus": False,
    } for x in top_ext]

    # 注意：本函数前文的循环变量已用过 `s`/`rel`（str 型），
    # 这里若复用同名变量，mypy 会按首次赋值定成 str ⇒ 16 条假错误。
    st_all = stat() or {}
    res = st_all.get("resolution") or {}
    rel_all = st_all.get("relations") or {}
    by_cross = rel_all.get("by_cross") or {}
    counts = {
        "documents": len(left) + len(right) + len(ii_nodes),
        "edges": len(cross_edges) + len(ii_edges),
        "hubs": len(hub_nodes),
        "unresolved": int(res.get("unresolved") or 0),
        "regulatory_relations": int(by_cross.get("regulatory->regulatory") or 0),
        "internal_relations": int(by_cross.get("internal->internal") or 0),
        "cross_relations": int(by_cross.get("internal->regulatory") or 0),
        "total_relations": int(rel_all.get("total") or 0),
        # 各视图的"未定位"条数（**必须逐视图披露**：总盘子的 2462 条对读者没意义）
        "unresolved_by_view": {
            "internal_to_regulatory": cross_b,
            "regulatory_to_regulatory": reg_b,
            "internal_to_internal": int_b,
        },
        # 口径：分母剔除 organ/generic（机关名/泛指词不是文件）
        "file_denominator": int(st_all.get("file_level_denominator") or 0),
        "file_resolved_ratio": st_all.get("file_resolved_ratio"),
        "file_located_ratio": st_all.get("file_located_ratio"),
        "resolved_to_entity_ratio": st_all.get("resolved_to_entity_ratio"),
        "located_in_corpus_ratio": st_all.get("located_in_corpus_ratio"),
        # 外部法规权威（独立键空间，语料外）
        "external_total": int(ext_stat.get("total") or 0),
        "external_evidence": int(ext_stat.get("evidence_total") or 0),
        "external_by_guise": ext_stat.get("by_guise") or {},
        # **归属率**（含外部权威）：与"语料内定位率"分开报，不许合成一个数字
        "attributed_ratio": (round(
            (int(st_all.get("target_class", {}).get("entity") or 0)
             + int(st_all.get("target_class", {}).get("external") or 0))
            / int(st_all.get("file_level_denominator") or 1), 4)),
    }
    views = [
        {"key": "internal_to_regulatory", "label": "公司制度 → 监管依据",
         "edges": len(cross_edges), "nodes": len(left) + len(right),
         "total": counts["cross_relations"], "unresolved": cross_unresolved,
         "note": "可定位的全部画出（不抽样）；语料外的已登记为 RFN-EXT-* 并以虚线区分"},
        {"key": "regulatory_to_regulatory", "label": "监管文件 → 监管文件",
         "edges": len(hub_edges), "nodes": len(hub_nodes),
         "total": counts["regulatory_relations"], "unresolved": reg_unresolved,
         "note": f"5139 条里按被依据次数取 Top {len(hub_nodes)} 枢纽；"
                 f"枢纽间直连 {len(hub_edges)} 条照画，其余用度数（节点大小）表达"},
        {"key": "internal_to_internal", "label": "公司制度 → 公司制度",
         "edges": len(ii_edges), "nodes": len(ii_nodes),
         "total": counts["internal_relations"], "unresolved": ii_unresolved,
         "note": "全部画出 —— 含废止（新版废止旧版）与指向**历史版本**的边（虚线）"},
        {"key": "theme", "label": "主题关系",
         "edges": len(theme_edges), "nodes": len(themes) + len(keep_docs),
         "total": sum(n for (_t, d), n in pair.items() if d in keep_docs),
         "unresolved": sum(1 for r in cross_rows if not r.get("dst_ref")),
         "note": "主题 → 被该主题制度引用最多的监管文件（只计已定位）"},
        {"key": "external", "label": "外部法规（语料外）",
         "edges": int(ext_stat.get("evidence_total") or 0), "nodes": len(ext_all),
         "total": int(ext_stat.get("total") or 0), "unresolved": 0,
         "note": "编号 RFN-EXT-* = 被引用但**未收录**；按被引次数排序"},
    ]
    # ---- T2（2026-10-26）：节点名称统一回填 ----
    # 放在 return 之前对**所有**视图统一处理：节点名只有一个来源（底座标题），
    # 每个视图各写一遍正是"有的显示名字、有的显示编码"的根因。
    _need: list[dict] = []
    _need.extend(hub_nodes)
    _need.extend(ii_nodes.values())
    _need.extend(right.values())
    _need.extend(left.values())
    for _d in top_docs:
        _need.append({"id": _d, "name": all_name.get(_d, "")})
    _fill_node_names(_need)
    for _n in _need:
        if _n.get("name") and not str(all_name.get(_n["id"]) or "").strip():
            all_name[_n["id"]] = _n["name"]

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
        "external": {"nodes": ext_nodes,
                     "total": int(ext_stat.get("total") or 0),
                     "evidence": int(ext_stat.get("evidence_total") or 0),
                     "by_guise": ext_stat.get("by_guise") or {},
                     "guise_labels": dict(_E.GUISE_LABELS),
                     "note": ("外部法规是**语料外**文件的登记键空间：编号 `RFN-EXT-*` "
                              "表示「被引用但未收录」，不代表库里能打开这份文件。")},
        "hubs": hub_nodes,
        "counts": counts,
        "stats": {"resolution": res,
                  "resolved_to_entity_ratio": st_all.get("resolved_to_entity_ratio"),
                  "file_resolved_ratio": st_all.get("file_resolved_ratio")},
        "labels": dict(RELATION_LABELS),
        "note": (f"全库 {counts['total_relations']} 条关系：监管↔监管 "
                 f"{counts['regulatory_relations']}、制度↔制度 "
                 f"{counts['internal_relations']}、制度→监管 "
                 f"{counts['cross_relations']}。"
                 f"文件类目标**语料内**定位率 "
                 f"{round((counts['file_resolved_ratio'] or 0) * 100, 1)}%"
                 f"（分母 {counts['file_denominator']}，已剔除机关名）；"
                 f"另有 {counts['external_total']} 条外部法规已登记为独立键空间"
                 f"（`RFN-EXT-*`，覆盖 {counts['external_evidence']} 条关系）；"
                 f"语料内待补登记 {sum(1 for r in (cross_rows + reg_rows + int_rows) if not r.get('dst_ref') and r.get('dst_key'))} 条。"),
    }


if __name__ == "__main__":  # 离线自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
