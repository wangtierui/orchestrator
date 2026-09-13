# -*- coding: utf-8 -*-
"""gates/gate_relations — 依据/废止关系产物门禁（R-F01，2026-09-14）

背景
----
依据/废止关系已收敛为**唯一事实源** `modules/regulatory_classifier/data/relations/relations_index.jsonl`
（生成器 `tools/extract_relations.py`，文本抽取唯一实现 `std_lib/common_lib/relations.py`），
并承载**三类关系**（监管依据/废止、内部依据/废止、内部→监管依据）。这些关系将被
classifier 报告、base 关联（merged）、drafter 起草素材共同消费——一旦键集/枚举/引用漂移，
下游会静默产出错误结论（与 P7 引用门禁同类风险）。本门禁使该类漂移不可能静默存在。

判据
----
1. **产物缺失 → FAIL**（对齐「输入缺失不得空跑放行」，不静默通过）；
2. 每行键集 **⊇ `interfaces.contract.RELATION_FIELDS`**（允许加字段，缺必报）；
3. **受控枚举闭包**：`relation/src_kind/dst_kind/basis_type/action/scope/matched_by` ∈ `config.enums`；
4. **强引用可解析**：`dst_ref` 非空时——`RFN-*` 须在归属表索引、`IPN-*` 须在内部主索引；
5. **溯源字段非空**：`source_snippet` / `generated_by` / `generated_at`（关系须可回溯到正文出处）；
6. **源侧可识别**：`src_ref` 或 `src_key` 至少一个非空；
7. **统计一致性**：`relations_stat.json` 的 `total` 与 `by_kind` 计数须与实际行数一致。

处置入口
--------
    python cli.py relations gen        # 重新抽取与落盘（含三类视图）
    python cli.py relations status     # 查看生成元信息与解析率
"""
from __future__ import annotations

import json
import os

import paths
from config.enums import (
    BASIS_TYPE,
    RELATION_DOC_KIND,
    RELATION_KIND,
    RELATION_MATCH_METHOD,
    REPEAL_ACTION,
    REPEAL_SCOPE,
)

_REL_DIR = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data", "relations")
_INDEX = os.path.join(_REL_DIR, "relations_index.jsonl")
_STAT = os.path.join(_REL_DIR, "relations_stat.json")

_RESERVED_KEYS = {"relation_id"}
_NON_EMPTY = ("source_snippet", "generated_by", "generated_at")


def _load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _enum_ok(v: str, allowed, *, empty_ok: bool) -> bool:
    if v == "" and empty_ok:
        return True
    return v in allowed


def run():
    if not os.path.exists(_INDEX):
        return False, {"error": f"关系事实源不存在：{_INDEX}"
                                "；门禁未实检，不得视为通过（先运行 `cli.py relations gen`）"}
    try:
        rows = _load_jsonl(_INDEX)
        stat = json.load(open(_STAT, encoding="utf-8")) if os.path.exists(_STAT) else {}
    except (OSError, ValueError) as e:  # noqa: BLE001
        return False, {"error": f"产物不可读：{e!r}"}

    from interfaces.contract import RELATION_FIELDS  # noqa: PLC0415

    required = set(RELATION_FIELDS) - _RESERVED_KEYS
    problems: list[str] = []
    missing_keys: dict[str, int] = {}
    enum_bad: dict[str, int] = {}
    trace_missing = 0
    src_unidentified = 0
    refs: list[tuple[str, str]] = []

    for r in rows:
        miss = required - set(r)
        for k in miss:
            missing_keys[k] = missing_keys.get(k, 0) + 1
        checks = (
            ("relation", r.get("relation", ""), RELATION_KIND, False),
            ("src_kind", r.get("src_kind", ""), RELATION_DOC_KIND, False),
            ("dst_kind", r.get("dst_kind", ""), RELATION_DOC_KIND, False),
            ("basis_type", r.get("basis_type", ""), BASIS_TYPE, True),
            ("action", r.get("action", ""), REPEAL_ACTION, True),
            ("scope", r.get("scope", ""), REPEAL_SCOPE, True),
            ("matched_by", r.get("matched_by", ""), RELATION_MATCH_METHOD, False),
        )
        for name, val, allowed, empty_ok in checks:
            if not _enum_ok(val, allowed, empty_ok=empty_ok):
                enum_bad[f"{name}={val!r}"] = enum_bad.get(f"{name}={val!r}", 0) + 1
        if any(not (r.get(k) or "").strip() for k in _NON_EMPTY):
            trace_missing += 1
        if not (r.get("src_ref") or r.get("src_key")):
            src_unidentified += 1
        if r.get("dst_ref"):
            refs.append((r["dst_kind"], r["dst_ref"]))

    # 强引用可解析性（RFN↔归属表 / IPN↔内部主索引）
    unresolvable: list[str] = []
    if refs:
        rfns, ipns = set(), set()
        try:
            import sys  # noqa: PLC0415

            for p in (paths.MODULES_DIR,
                      os.path.join(paths.MODULES_DIR, "regulatory_classifier")):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from rfn import get_index  # noqa: PLC0415

            rfns = set(get_index().all_rfns())
        except Exception as e:  # noqa: BLE001
            problems.append(f"归属表索引不可读，强引用无法校验：{e!r}")
        ipb = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data",
                           "internal_policy_index.json")
        if os.path.exists(ipb):
            ipns = {x.get("ipn") for x in json.load(open(ipb, encoding="utf-8")).get("records", [])}
        for kind, ref in refs:
            if kind == "regulatory" and rfns and ref not in rfns:
                unresolvable.append(f"{ref}(regulatory)")
            elif kind == "internal" and ipns and ref not in ipns:
                unresolvable.append(f"{ref}(internal)")

    # 统计一致性
    by_kind_actual: dict[str, int] = {}
    for r in rows:
        by_kind_actual[r.get("relation", "")] = by_kind_actual.get(r.get("relation", ""), 0) + 1
    stat_total = (stat.get("relations") or {}).get("total")
    stat_kind = (stat.get("relations") or {}).get("by_kind") or {}
    stat_ok = (stat_total == len(rows)) and all(stat_kind.get(k, 0) == v
                                                for k, v in by_kind_actual.items())

    if missing_keys:
        problems.append(f"键集缺字段：{dict(sorted(missing_keys.items()))}")
    if enum_bad:
        problems.append(f"受控枚举越界：{dict(sorted(enum_bad.items(), key=lambda kv: -kv[1])[:8])}")
    if trace_missing:
        problems.append(f"溯源字段为空的关系 {trace_missing} 条（source_snippet/generated_* 须非空）")
    if src_unidentified:
        problems.append(f"源侧不可识别的行 {src_unidentified} 条（src_ref 与 src_key 皆空）")
    if unresolvable:
        problems.append(f"强引用不可解析 {len(unresolvable)} 条（示例 {unresolvable[:5]}）")
    if not stat_ok:
        problems.append(f"统计不一致：stat.total={stat_total} vs 实际 {len(rows)}，"
                        f"by_kind {stat_kind} vs {by_kind_actual}")

    detail = {
        "rows": len(rows),
        "contract_fields": len(RELATION_FIELDS),
        "by_kind": by_kind_actual,
        "dst_ref_rows": len(refs),
        "unresolvable_refs": len(unresolvable),
        "stat_consistent": stat_ok,
        "extractor_version": stat.get("extractor_version"),
        "resolved_to_entity_ratio": stat.get("resolved_to_entity_ratio"),
        "located_in_corpus_ratio": stat.get("located_in_corpus_ratio"),
        "problems": problems,
        "note": "判据=事实源存在 + 键集⊇契约 + 受控枚举闭包 + 强引用可解析 + 溯源非空 + 统计一致",
    }
    return (not problems), detail
