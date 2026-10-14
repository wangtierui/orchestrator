# -*- coding: utf-8 -*-
"""modules.implementation_audit.store — 事实源 JSONL 存储（原子写 + 幂等 upsert）。

与 `reg_frameworks.store` 同款纪律（`io_atomic` SSOT 原语；JSONL 事实源 + 派生视图），
但**各自独立文件**——不跨模块共享写路径（单向依赖纪律：两个新模块彼此不 import）。
"""
from __future__ import annotations

import json
import os

import paths

F_RECORDS = "impl_records.jsonl"
F_EVIDENCE = "impl_evidence.jsonl"
F_GAPS = "impl_gaps.jsonl"

_FILES = (F_RECORDS, F_EVIDENCE, F_GAPS)


def data_dir() -> str:
    d = os.path.join(paths.MODULES_DIR, "implementation_audit", "data")
    os.makedirs(d, exist_ok=True)
    return d


def path_of(name: str) -> str:
    if name not in _FILES:
        raise KeyError(name)
    return os.path.join(data_dir(), name)


def read_rows(name: str) -> list[dict]:
    p = path_of(name)
    if not os.path.exists(p):
        return []
    out: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except ValueError:
                continue  # 单行损坏不阻断整体（旁路纪律）
    return out


def write_rows(name: str, rows: list[dict], *, key: str) -> dict:
    old = {str(r.get(key)): r for r in read_rows(name) if r.get(key)}
    added = updated = 0
    for r in rows:
        k = str(r.get(key) or "")
        if not k:
            continue
        if k in old:
            if old[k] != r:
                updated += 1
        else:
            added += 1
        old[k] = r
    ordered = [old[k] for k in sorted(old)]
    from std_lib.common_lib.io_atomic import atomic_write_text

    payload = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in ordered)
    atomic_write_text(path_of(name), payload + ("\n" if ordered else ""))
    return {"added": added, "updated": updated, "total": len(ordered)}


# ---- 读面 ----

def list_records(*, dept: str = "", period: str = "") -> list[dict]:
    rows = read_rows(F_RECORDS)
    if dept:
        rows = [r for r in rows if str(r.get("dept")) == dept]
    if period:
        rows = [r for r in rows if str(r.get("period")) == period]
    return rows


def list_evidence(*, ipn: str = "") -> list[dict]:
    rows = read_rows(F_EVIDENCE)
    return [r for r in rows if not ipn or str(r.get("ipn")) == ipn]


def list_gaps(*, ipn: str = "", severity: str = "", gap_class: str = "") -> list[dict]:
    rows = read_rows(F_GAPS)
    if ipn:
        rows = [r for r in rows if str(r.get("ipn")) == ipn]
    if severity:
        rows = [r for r in rows if str(r.get("severity")) == severity]
    if gap_class:
        rows = [r for r in rows if str(r.get("gap_class")) == gap_class]
    return rows


def stats() -> dict:
    recs, gaps, evs = read_rows(F_RECORDS), read_rows(F_GAPS), read_rows(F_EVIDENCE)
    return {
        "records": len(recs),
        "evidence": len(evs),
        "gaps": len(gaps),
        "gaps_high": len([g for g in gaps if g.get("severity") == "high"]),
    }
