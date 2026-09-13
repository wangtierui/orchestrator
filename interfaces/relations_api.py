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


def load(kind: str = "all") -> list[dict]:
    """读取关系（`kind` ∈ KINDS）。

    - `regulatory`：类别 1（`src_kind=regulatory`）
    - `internal`  ：类别 2（`src_kind=internal` 且 `dst_kind=internal`）
    - `cross`     ：类别 3（内部制度 → 监管文件；含依据与废止，纯依据边见 `load_cross_basis`）
    - `all`       ：事实源全量
    """
    if kind not in KINDS:
        raise ValueError(f"kind 须 ∈ {KINDS}，得到 {kind!r}")
    rows = _read_jsonl(INDEX_PATH)
    if kind == "all":
        return rows
    if kind == "regulatory":
        return [r for r in rows if r.get("src_kind") == "regulatory"]
    if kind == "internal":
        return [r for r in rows if r.get("src_kind") == "internal"
                and r.get("dst_kind") == "internal"]
    return [r for r in rows if r.get("src_kind") == "internal"
            and r.get("dst_kind") == "regulatory"]


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
    """按源实体取关系（RFN 或 IPN）。"""
    return [r for r in _read_jsonl(INDEX_PATH)
            if ref and (r.get("src_ref") == ref or r.get("src_key") == ref)]


def by_dst(ref: str) -> list[dict]:
    """按目标实体取关系（RFN 或 IPN）——用于"谁依据/废止了我"的反向查询。"""
    return [r for r in _read_jsonl(INDEX_PATH)
            if ref and (r.get("dst_ref") == ref or r.get("dst_key") == ref)]


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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
