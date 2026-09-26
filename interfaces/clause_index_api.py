# -*- coding: utf-8 -*-
"""interfaces/clause_index_api — 条文抽取产物**唯一访问接口**（阶段 3，2026-09-18）

定位：`modules/regulatory_scrapers/clause_index` 的条文产物
（`data/clauses/{src}_clauses_{date}.jsonl|md`）此前被 classifier（主题报告）、
drafter（外部条款导出）、base_publish（发布件）三处**各自 `sys.path.insert` 直连**。
本接口把引导收敛到一处，消费方一律经此访问（方案 §6 阶段 3）。

纪律：**只读**。产物唯一写方 = `clause_index.build_clause_index`
（clean 管线尾部/显式调用）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths  # noqa: E402

_SCRAPERS = os.path.join(paths.MODULES_DIR, "regulatory_scrapers")
if _SCRAPERS not in sys.path:
    sys.path.insert(0, _SCRAPERS)

CLAUSE_DIR = os.path.join(_SCRAPERS, "data", "clauses")


def _impl():
    import clause_index as _ci  # noqa: PLC0415
    return _ci


def clauses_dir() -> str:
    """条款产物目录（`modules/regulatory_scrapers/data/clauses`；I-3 新增访问器）。"""
    return os.path.join(_SCRAPERS, "data", "clauses")


def latest_clause_path(source_id: str) -> str:
    """该源最新条文 jsonl 路径（无产物返回 ""）。"""
    return _impl().latest_clause_path(source_id)


def iter_file_clauses(source_id: str):
    """按源迭代条文行（每行 = 一份文件的条款结构）。"""
    return _impl().iter_file_clauses(source_id)


def find_clauses(docno: str = "", title: str = "", src: str = ""):
    """按文号/标题/源检索条文行。"""
    return _impl().find_clauses(docno=docno, title=title, src=src)


def build_clause_index(*, rebuild: bool = False) -> dict:
    """重建条文索引（写方语义；供运维/管线调用）。"""
    return _impl().build_clause_index(rebuild=rebuild)
