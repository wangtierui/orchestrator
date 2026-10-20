# -*- coding: utf-8 -*-
"""std_lib.scraper_std.local_paths — 把 raw 记录的 `local_path` 解析成**绝对路径**。

问题（2026-10-20 第一百零四批实测）
----------------------------------
`local_path` 由各采集器用 `os.path.relpath(文件, <基准>)` 生成，但**基准不统一**：

| 源 | 基准 | 示例 | 条数 |
|---|---|---|---|
| `mof` | **仓根** | `modules/regulatory_scrapers/data/docs/mof_…/x.pdf` | 246 |
| `pbc` | **仓根** | `modules/regulatory_scrapers/data/docs/pbc_…/x.docx` | 13 |
| `gov` | `modules/regulatory_scrapers` | `data/docs/gov_…/x.html` | 1189 |
| `supp` | `modules/regulatory_scrapers` | `data/docs/supplementary_…/x.txt` | 7 |
| `nfra` | （无该字段） | — | 0 |

**这是口径分裂，不是功能故障**：实测**没有任何代码**把 `local_path` 拼成路径去打开文件
（`contract.py` 只做字段映射、`build_fts.py` 只把它存进 SQLite 作元数据、前端不用它）。
所以历史数据**不需要大规模迁移**（1199 条迁移的收益不抵风险）。

但**外部消费者/后续开发者**若按单一规则拼接，会有一半找不到文件。本模块提供
**唯一的正确解析方式**：把两种基准统一认出来，返回绝对路径。

用法
----
    from std_lib.scraper_std.local_paths import resolve_local_path
    p = resolve_local_path(rec["local_path"])     # 绝对路径（str）
    if p and os.path.exists(p): ...

**不要**自己写 `os.path.join(paths.ROOT, lp)` —— 那只对仓根基准的源成立
（`gov`/`supp` 会拼错）。
"""
from __future__ import annotations

import os

from paths import ROOT as _WS_ROOT

__all__ = ["SCRAPERS_ROOT", "exists_local_path", "resolve_local_path"]

#: `modules/regulatory_scrapers`（第二个可能基准）
SCRAPERS_ROOT = os.path.join(_WS_ROOT, "modules", "regulatory_scrapers")


def resolve_local_path(local_path: str, *, must_exist: bool = False) -> str:
    """把 `local_path` 解析成**绝对路径**（自动兼容两种历史基准）。

    解析顺序（**先验存在性，再回退**，避免"看起来对但打不开"）：

      1. 已是绝对路径 ⇒ 原样返回（未来统一后可能出现）；
      2. 按**仓根**拼接 —— 命中则返回（`mof` / `pbc`）；
      3. 按 `modules/regulatory_scrapers` 拼接 —— 命中则返回（`gov` / `supp`）；
      4. 都不存在：`must_exist=False` 时返回**第 2 步**的结果（仓根基准，语义最通用）
         并在返回值里保持"它是猜的"这一事实（调用方应自行 `os.path.exists` 判断）；
         `must_exist=True` 时返回空串（明确"解析不到"，而不是给一个错路径）。

    Parameters
    ----------
    local_path:
        记录里的原值（可能是 `modules/…` 或 `data/…` 或绝对路径或空）。
    must_exist:
        是否要求解析结果**必须存在**。批处理/审计场景建议 `True`（宁可空，不给错路径）；
        仅展示场景可用默认 `False`。

    Note
    ----
    **不做任何字符串改写**：返回的是绝对路径，不回写记录。若将来要把某源归一为
    单一基准，应走 `tools/migrate_local_path.py`（带备份 + 逐条验证文件可达）。
    """
    lp = str(local_path or "").strip()
    if not lp:
        return ""
    norm = lp.replace("\\", "/")

    # 1) 已是绝对路径
    if os.path.isabs(lp):
        return lp

    # 2) 仓根基准（mof / pbc 的历史口径）
    cand_repo = os.path.join(_WS_ROOT, norm.replace("/", os.sep))
    if os.path.exists(cand_repo):
        return cand_repo

    # 3) SCRAPERS 基准（gov / supp 的历史口径）
    cand_scrapers = os.path.join(SCRAPERS_ROOT, norm.replace("/", os.sep))
    if os.path.exists(cand_scrapers):
        return cand_scrapers

    # 4) 都不存在
    if must_exist:
        return ""
    return cand_repo


def exists_local_path(local_path: str) -> bool:
    """`local_path` 是否指向一个**真实存在**的文件（两基准都试）。"""
    return bool(resolve_local_path(local_path, must_exist=True))
