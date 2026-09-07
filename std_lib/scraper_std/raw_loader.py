# -*- coding: utf-8 -*-
"""
raw_loader.py —— 五源 data/raw 统一只读入口（探查/清洗解耦架构 L0）

设计铁律（《doc_type/category 清洗方案》最终版）：
  - data/raw 为唯一不可变分析对象与清洗输入，本模块只读，绝不写入；
  - 探查层（probe_doc_type_category）与清洗层（clean_doc_type_category）
    均经本模块读取 raw，保证「分析对象恒为原始抓取结果」；
  - 完整性断言：raw 顶层 meta.count / count 与记录数不一致即告警。

结构识别（实测 2026-08-28）：
  - nfra: {meta:{...}, records:[...]}
  - pbc:  list[...]
  - mof:  {source, count, items:[...]}
  - gov:  {source, category, count, records:[...]}
  - supp: list[...]
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

# 五源 raw 文件相对路径（相对 regulatory_scrapers/）
RAW_FILES: dict[str, str] = {
    "nfra": "nfra_regulations_scraper/data/raw/nfra_regulations.json",
    "pbc": "pbc_regulations_scraper/data/raw/pbc_laws.json",
    "mof": "mof_regulations_scraper/data/raw/mof_laws.json",
    "gov": "gov_regulations_scraper/data/raw/gov_regulations_all_20260819_132858.json",
    "supp": "supplementary_regulations_scraper/data/raw/supplementary_regulations.json",
}

# raw 记录中发文机关字段名（各源不同，统一归一为 issue_organ）
AGENCY_FIELD = "issue_organ"
AGENCY_ALIASES = ("issuing_authority", "issue_org", "issue_organ")

# 记录数断言表（探查/清洗入口核对用；与 2026-08-28 实测一致）
EXPECTED_COUNTS: dict[str, int] = {"nfra": 1929, "pbc": 569, "mof": 870, "gov": 30271, "supp": 59}


class RawLoaderError(RuntimeError):
    pass


def _records_of(doc: Any) -> list[dict[str, Any]]:
    """识别 dict+records/items 或 list 结构，返回记录列表（只读引用）。"""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for key in ("records", "items"):
            v = doc.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    raise RawLoaderError(f"无法识别的 raw JSON 结构: {type(doc).__name__}")


def iter_records(source: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """按源迭代 (source, record)。source=None 时遍历五源。只读。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sources = [source] if source else list(RAW_FILES)
    for s in sources:
        path = os.path.join(root, RAW_FILES[s])
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        for rec in _records_of(doc):
            yield s, rec


def load_all(source: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """一次性加载全部记录（探查统计用）。只读。"""
    return list(iter_records(source))


def verify_counts(source: str | None = None) -> dict[str, tuple[int, int]]:
    """完整性断言：各源记录数与 EXPECTED_COUNTS 比对。返回 {source: (实际, 预期)}。"""
    actual: dict[str, int] = {}
    for s, _ in iter_records(source):
        actual[s] = actual.get(s, 0) + 1
    result = {s: (actual.get(s, 0), EXPECTED_COUNTS.get(s, -1)) for s in actual}
    for s, (a, e) in result.items():
        if e >= 0 and a != e:
            print(f"[raw_loader] ⚠ {s} 记录数 {a} != 预期 {e}（raw 已更新？请同步 EXPECTED_COUNTS）")
    return result


def agency_of(rec: dict[str, Any]) -> str:
    """归一化发文机关（各源字段名不同）。"""
    for k in AGENCY_ALIASES:
        v = rec.get(k)
        if v:
            return str(v).strip()
    return ""


if __name__ == "__main__":
    v = verify_counts()
    for s, (a, e) in v.items():
        print(f"  {s:<6} {a:>6} 条 (预期 {e})")
    total = sum(a for a, _ in v.values())
    print(f"  合计 {total} 条")
