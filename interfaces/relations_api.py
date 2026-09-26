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

RELATIONS_DIR = os.path.join(_ROOT, "modules", "regulatory_classifier", "data", "relations")
INDEX_PATH = os.path.join(RELATIONS_DIR, "relations_index.jsonl")
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
        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

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


if __name__ == "__main__":  # 离线自检
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
