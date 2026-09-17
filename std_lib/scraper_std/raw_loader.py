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

阶段 0 止血（2026-09-18）—— 本文件此前**两处失效**，均已修正：
  1) `RAW_FILES` 仍指向旧仓扁平形态（`nfra_regulations_scraper/data/raw/...`），
     而 2026-09 模块拍平后实际路径为 `modules/regulatory_scrapers/data/raw/...`
     → 调用即 FileNotFoundError（本模块当时**零消费方**，故未暴露）；
  2) `EXPECTED_COUNTS` 冻结 2026-08-28 基线（gov 预期 30271，实际已 13177），
     属"文本写死基准值"漂移源 → 改为**与 raw 文件自带 count/meta.count 自校验**，
     不再维护外部冻结常量（与 R23「数量以事实源为准，禁止文本写死」同口径）。

  兼容性：模块级 `EXPECTED_COUNTS` 符号保留（空 dict 语义），避免历史调用点
  `from raw_loader import EXPECTED_COUNTS` 直接 ImportError；新代码勿依赖。
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

# 五源 raw 文件相对路径（相对**仓库根**；2026-09-18 修正：模块拍平后真实位置）
RAW_FILES: dict[str, str] = {
    "gov": "modules/regulatory_scrapers/data/raw/gov_laws.json",
    "mof": "modules/regulatory_scrapers/data/raw/mof_laws.json",
    "nfra": "modules/regulatory_scrapers/data/raw/nfra_regulations.json",
    "pbc": "modules/regulatory_scrapers/data/raw/pbc_laws.json",
    "supp": "modules/regulatory_scrapers/data/raw/supplementary_regulations.json",
}

# raw 记录中发文机关字段名（各源不同，统一归一为 issue_organ）
AGENCY_FIELD = "issue_organ"
AGENCY_ALIASES = ("issuing_authority", "issue_org", "issue_organ")

# 兼容符号（空 dict = 不再维护外部冻结基线；完整性改由 raw 自带 count 自校验）
EXPECTED_COUNTS: dict[str, int] = {}


class RawLoaderError(RuntimeError):
    pass


def repo_root() -> str:
    """仓库根（std_lib/scraper_std/raw_loader.py → 上溯三级）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def raw_path(source: str, root: str | None = None) -> str:
    """单源 raw 绝对路径（缺失即抛 RawLoaderError，不做静默回退）。"""
    if source not in RAW_FILES:
        raise RawLoaderError(f"未知源 {source!r}（可用 {sorted(RAW_FILES)}）")
    return os.path.join(root or repo_root(), RAW_FILES[source].replace("/", os.sep))


def missing_sources(root: str | None = None) -> list[str]:
    """磁盘缺失的源清单（供调用方给出可执行指引；本模块只读不建）。"""
    r = root or repo_root()
    return [s for s in RAW_FILES if not os.path.exists(raw_path(s, r))]


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


def _declared_count(doc: Any) -> int | None:
    """raw 文件自带记录数（顶层 count / meta.count）；无声明返回 None。"""
    if not isinstance(doc, dict):
        return None
    for holder, key in ((doc, "count"), (doc.get("meta") or {}, "count")):
        if isinstance(holder, dict):
            v = holder.get(key)
            if isinstance(v, int):
                return v
    return None


def load_doc(source: str, root: str | None = None) -> Any:
    """读单源 raw 原始结构（只读）。"""
    with open(raw_path(source, root), encoding="utf-8") as fh:
        return json.load(fh)


def iter_records(source: str | None = None, root: str | None = None
                 ) -> Iterator[tuple[str, dict[str, Any]]]:
    """按源迭代 (source, record)。source=None 时遍历五源。只读。"""
    sources = [source] if source else list(RAW_FILES)
    for s in sources:
        for rec in _records_of(load_doc(s, root)):
            yield s, rec


def load_all(source: str | None = None, root: str | None = None
             ) -> list[tuple[str, dict[str, Any]]]:
    """一次性加载全部记录（探查统计用）。只读。"""
    return list(iter_records(source, root))


def verify_counts(source: str | None = None, root: str | None = None) -> dict[str, tuple[int, int]]:
    """完整性自校验：返回 {source: (实际记录数, raw 自带声明数)}。

    判据来自 **raw 文件自身**（count / meta.count），不依赖外部冻结基线；
    声明数缺失记为 -1（不判）。不一致即打印告警（不抛）。
    """
    sources = [source] if source else list(RAW_FILES)
    result: dict[str, tuple[int, int]] = {}
    for s in sources:
        doc = load_doc(s, root)
        actual = len(_records_of(doc))
        declared = _declared_count(doc)
        result[s] = (actual, declared if declared is not None else -1)
    for s, (a, d) in result.items():
        if d >= 0 and a != d:
            print(f"[raw_loader] ⚠ {s} 记录数 {a} != raw 自带 count {d}（raw 被改写？）")
    return result


def agency_of(rec: dict[str, Any]) -> str:
    """归一化发文机关（各源字段名不同）。"""
    for k in AGENCY_ALIASES:
        v = rec.get(k)
        if v:
            return str(v).strip()
    return ""


if __name__ == "__main__":
    miss = missing_sources()
    if miss:
        print(f"[raw_loader] ⚠ 缺失源 raw: {miss}（数据不入 git，异机需先恢复/抓取）")
    for s, (a, d) in verify_counts().items():
        print(f"  {s:<6} {a:>6} 条 (raw 自带 count={d if d >= 0 else '—'})")
