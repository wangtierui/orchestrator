# -*- coding: utf-8 -*-
"""modules.reg_frameworks.store — 事实源 JSONL 存储（原子写 + 幂等 upsert）。

为何 JSONL（而非关系库）：与本仓既有事实源一致（raw/cleaned/clauses/relations/published
均为 JSONL 事实源 + SQLite 派生索引，见 ADR-0007 产物分级）；本模块数据量级
（体系 4 / 表 ~112 / 项 ~3053 / 映射）远未到需要关系库的规模，而**克隆可移植**要求
「文件即权威」。

写入纪律：`io_atomic.atomic_write_text`（SSOT 原语，不自造）；**先写临时文件再 os.replace**
→ 断电/中断不产生半截文件。读侧提供双向查询（条款→数据项 / 数据项→条款，方案 §5.2.3-⑤）。
"""
from __future__ import annotations

import json
import os

import paths

#: 四个事实源文件（命名口径与既有 `relations_index.jsonl` 等一致）
F_FRAMEWORKS = "frameworks.jsonl"
F_TABLES = "data_tables.jsonl"
F_ITEMS = "data_items.jsonl"
F_MAPS = "clause_item_map.jsonl"
F_EVIDENCE = "evidence_materials.jsonl"

_FILES = (F_FRAMEWORKS, F_TABLES, F_ITEMS, F_MAPS, F_EVIDENCE)


def data_dir() -> str:
    """模块数据面（`modules/reg_frameworks/data/`；受 gate_flat_layout 约束保持**拍平**）。"""
    d = os.path.join(paths.MODULES_DIR, "reg_frameworks", "data")
    os.makedirs(d, exist_ok=True)
    return d


def path_of(name: str) -> str:
    if name not in _FILES:
        raise KeyError(name)
    return os.path.join(data_dir(), name)


def _read(name: str) -> list[dict]:
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
                # 单行损坏不得让整体不可读（旁路纪律）：计数披露、内容跳过
                continue
    return out


def write_rows(name: str, rows: list[dict], *, key: str) -> dict:
    """**幂等 upsert**：按 `key` 合并新旧（后者覆盖），原子写。返回 `{added, updated, total}`。"""
    old = {str(r.get(key)): r for r in _read(name) if r.get(key)}
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


# --------------------------------------------------------------------------- #
# 读面（interfaces/reg_frameworks_api.py 的实现面）
# --------------------------------------------------------------------------- #


def list_frameworks() -> list[dict]:
    return _read(F_FRAMEWORKS)


def list_tables(fid: str = "") -> list[dict]:
    rows = _read(F_TABLES)
    return [r for r in rows if not fid or str(r.get("fid")) == fid]


def list_items(tid: str = "") -> list[dict]:
    rows = _read(F_ITEMS)
    return [r for r in rows if not tid or str(r.get("tid")) == tid]


def list_maps(*, rfn: str = "", iid: str = "", status: str = "") -> list[dict]:
    """映射查询：**双向追溯**（给 rfn 查数据项，或给 iid 查条款）。"""
    rows = _read(F_MAPS)
    if rfn:
        rows = [r for r in rows if str(r.get("rfn")) == rfn]
    if iid:
        rows = [r for r in rows if str(r.get("iid")) == iid]
    if status:
        rows = [r for r in rows if str(r.get("status")) == status]
    return rows


def list_evidence(ipn: str = "") -> list[dict]:
    rows = _read(F_EVIDENCE)
    return [r for r in rows if not ipn or str(r.get("ipn")) == ipn]


def stats() -> dict:
    """模块概览（确认台 P2/治理台账消费；计数全部来自事实源行数）。"""
    return {
        "frameworks": len(_read(F_FRAMEWORKS)),
        "tables": len(_read(F_TABLES)),
        "items": len(_read(F_ITEMS)),
        "maps": len(_read(F_MAPS)),
        "maps_review": len([r for r in _read(F_MAPS) if str(r.get("status")) != "confirmed"]),
        "evidence": len(_read(F_EVIDENCE)),
    }
