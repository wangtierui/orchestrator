# -*- coding: utf-8 -*-
"""raw_loader —— **已退役**（2026-10-09，批 48 / T-D）。

退役理由（先证后迁）：
  ① **零仓内引用**：全仓 grep（`.py/.yaml/.toml/.md`）无任何 `import raw_loader` / `raw_loader.` 调用；
     本模块此前唯一的潜在用途（共享 raw 读取）已由 **`std_lib/scraper_std/pipeline.load_raw_records`**
     承接 —— 后者是**五源 clean 的唯一载入点**，且已支持 `.jsonl` 与 `.json` **双格式流式**（N-206/S-A）。
  ② **避免双实现分叉**：保留两份"读 raw"的实现（本模块 + pipeline）会随 raw 形态演进各自漂移
     （本仓已有同类教训：collector 缓存样板 N-83、校验双套实现 N-82）。
  ③ 保留而非删除：其"按源名 → 路径 → 逐条迭代"的封装对**未来**新增读取面仍有参考价值，
     且迁移成本为零（文件级移动）。

⚠️ 为何不在 `tools/retired/`：J1~J4 退役判据覆盖的是 **`tools/` 层**（`tools/_manifest.json` 双向相等），
本文件属 **共享库层** ⇒ 置于 `std_lib/scraper_std/retired/`。该目录名被 ruff / mypy 的 `retired/`
排除规则**按名覆盖**（自动生效），但**不**受 J3「零引用」门禁保护 —— 已登记待办，供后续
决定是否建立**库层退役判据**。

原实现（迁移前）保留在 git 历史：`git log --follow std_lib/scraper_std/retired/raw_loader.py`。
"""

# -*- 以下为退役时的原样代码（不再维护；如需复用请先核对其与 pipeline 的口径差异） -*-
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

from std_lib.common_lib.logging import get_logger

LOG = get_logger(__name__)

# 五源 raw 文件相对路径（相对**仓库根**；2026-09-18 修正：模块拍平后真实位置）
# ⚠️ N-206（2026-10-09，S-A）：gov 主库已改为 **JSONL**（`gov_laws.jsonl`，首行 `_meta` 信封）。
# 本模块随之支持双格式（`.jsonl` 逐行 / `.json` 逐条 raw_decode），并**不再整表 `json.load`**。
# 另注：本模块当前**全仓无调用方**（已登记待办核对是否退役），但语义仍保持正确。
RAW_FILES: dict[str, str] = {
    "gov": "modules/regulatory_scrapers/data/raw/gov_laws.jsonl",
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
    """读单源 raw 原始结构（只读；**仅 `.json` 形态**）。

    N-206：`gov` 已是 **JSONL**（无单一"文档"结构）⇒ 对本模块用 `iter_records()` 逐条读；
    误用本函数会得到 `RawLoaderError`（显式指引，而非静默给错结构）。
    """
    p = raw_path(source, root)
    if p.endswith(".jsonl"):
        raise RawLoaderError(
            f"{source} 已为 JSONL（{os.path.basename(p)}）：请用 iter_records()/count_records()，"
            "不要 load_doc()")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _iter_source_records(path: str) -> Iterator[dict[str, Any]]:
    """**流式**迭代单源 raw 记录（JSONL 逐行跳过 `_meta`；`.json` 交给 pipeline 的流式实现）。"""
    if path.endswith(".jsonl"):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict) and len(rec) == 1 and "_meta" in rec:
                    continue
                if isinstance(rec, dict):
                    yield rec
        return
    from std_lib.scraper_std.pipeline import load_raw_records  # 复用**唯一**流式载入实现

    yield from load_raw_records(path)


def count_records(source: str, root: str | None = None) -> int:
    """**流式**统计单源记录数（不整表载入；JSONL 亦适用）。"""
    return sum(1 for _ in _iter_source_records(raw_path(source, root)))


def iter_records(
    source: str | None = None, root: str | None = None
) -> Iterator[tuple[str, dict[str, Any]]]:
    """按源迭代 (source, record)。source=None 时遍历五源。只读、**流式**。"""
    sources = [source] if source else list(RAW_FILES)
    for s in sources:
        for rec in _iter_source_records(raw_path(s, root)):
            yield s, rec


def load_all(
    source: str | None = None, root: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """一次性加载全部记录（探查统计用）。只读。"""
    return list(iter_records(source, root))


def _jsonl_index_count(source: str, root: str | None = None) -> int | None:
    """JSONL 主库的**旁路索引** `count`（等价于旧信封的 `count`；缺失 → None）。"""
    p = raw_path(source, root).replace(".jsonl", ".index.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f).get("count")
        return int(v) if isinstance(v, int) else None
    except (OSError, ValueError, TypeError):
        return None


def verify_counts(source: str | None = None, root: str | None = None) -> dict[str, tuple[int, int]]:
    """完整性自校验：返回 {source: (实际记录数, raw 自带声明数)}。

    判据来自 **raw 文件自身**（`.json`：count / meta.count；`.jsonl`：**旁路索引 count**，见 N-200/N-206），
    不依赖外部冻结基线；声明数缺失记为 -1（不判）。不一致即打印告警（不抛）。
    """
    sources = [source] if source else list(RAW_FILES)
    result: dict[str, tuple[int, int]] = {}
    for s in sources:
        p = raw_path(s, root)
        actual = count_records(s, root)
        declared = _jsonl_index_count(s, root) if p.endswith(".jsonl") else _declared_count(load_doc(s, root))
        result[s] = (actual, declared if declared is not None else -1)
    for s, (a, d) in result.items():
        if d >= 0 and a != d:
            LOG.warning(f"[raw_loader] ⚠ {s} 记录数 {a} != raw 自带 count {d}（raw 被改写？）")
    return result
