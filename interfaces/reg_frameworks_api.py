# -*- coding: utf-8 -*-
"""interfaces/reg_frameworks_api — 多监管数据体系读取契约（W3，方案 §5.2）。

与既有 `interfaces/*_api.py` 同构：**只读消费面 + 归一**，写路径不在此暴露
（写只经 `modules/reg_frameworks/store.py`）。下游（确认台/MCP/门禁/报告）一律经此取数，
避免各自拼 SQL/读 JSONL。
"""
from __future__ import annotations

# 引导纪律：唯一引导点是 bootstrap.py（gate_import_bootstrap 断言基线只减不增）——
# 本模块由链/CLI 经 bootstrap 后调用，故**不自写 sys.path.insert**。
from modules.reg_frameworks import regfw_store as _store

__all__ = [
    "enabled", "frameworks", "tables", "items", "maps_by_rfn", "maps_by_item",
    "maps_by_status", "stats", "register", "extract_dir", "map_clauses",
]


def enabled() -> bool:
    """模块数据面是否已有内容（未抽取过 → False；调用方可据此提示「先跑 extract」）。"""
    return bool(_store.stats().get("tables"))


def frameworks() -> list[dict]:
    """全部监管数据体系（Framework）。"""
    return _store.list_frameworks()


def tables(fid: str = "") -> list[dict]:
    """数据表清单（可按体系过滤）。"""
    return _store.list_tables(fid)


def items(tid: str = "") -> list[dict]:
    """数据项清单（可按表过滤）。"""
    return _store.list_items(tid)


def maps_by_rfn(rfn: str) -> list[dict]:
    """反向追溯：条款（RFN）→ 数据项映射。"""
    return _store.list_maps(rfn=rfn)


def maps_by_item(iid: str) -> list[dict]:
    """正向追溯：数据项 → 条款映射（方案 §5.2.3-⑤ 双向）。"""
    return _store.list_maps(iid=iid)


def maps_by_status(status: str = "") -> list[dict]:
    """按状态取映射（review = 待人工闸门）。"""
    return _store.list_maps(status=status)


def stats() -> dict:
    """模块概览（体系/表/项/映射/待复核计数）。"""
    return _store.stats()


# --------------------------------------------------------------------------- #
# 写侧透传（**唯一写口**；CLI 与链内步骤经此调用，模块内 store 不再被旁路引用）
# --------------------------------------------------------------------------- #


def register(fid: str, name: str, regulator: str, cadence: str,
             deadline_day: int | None = None, schema_version: str = "1") -> dict:
    """登记/更新一个 Framework（幂等 upsert）。"""
    from modules.reg_frameworks.regfw_model import Framework

    fw = Framework(fid=fid, name=name, regulator=regulator, cadence=cadence,
                   deadline_day=deadline_day, schema_version=schema_version)
    return _store.write_rows(_store.F_FRAMEWORKS, [fw.to_dict()], key="fid")


def extract_dir(fid: str, src_dir: str, **kw) -> dict:
    """素材目录 → DataTable/DataItem 结构化（透传 extract）。"""
    from modules.reg_frameworks import materialize as _ex

    return _ex.extract_dir(fid, src_dir, **kw)


def map_clauses(**kw) -> dict:
    """条款×数据项映射（透传 mapping；低置信入待办）。"""
    from modules.reg_frameworks import mapping as _mp

    return _mp.map_from_clauses(**kw)
