# -*- coding: utf-8 -*-
"""bootstrap.py — 唯一 sys.path 引导点（v2 方案 §3.1.2）

背景（v2 §2.1 A6）：改造前无统一引导点，`commands/` 9 个文件、`interfaces/` 8 个文件
各自 `sys.path.insert`，`modules/` 内另有 ≥119 处；而 `paths.py` 的纪律明文
「禁止业务代码 sys.path.insert」——纪律与实现长期背离。

用法（唯一形式）：
    from bootstrap import bootstrap
    bootstrap("regulatory_scraper"...)      # 按需声明所需模块（包名或短键）

    bootstrap()                             # 仅注入仓根（默认）
    bootstrap(include_tools=True)           # 额外注入 tools/
    bootstrap("all")                        # 注入全部模块（横切治理层用；如 gates/tools）

纪律：
  - 新增引导点必须先在本文件扩能，不得在各文件另写 `sys.path.insert`；
  - `gate_import_bootstrap` 断言该纪律（基线冻结、只减不增）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_INJECTED: set[str] = set()


def _add(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
    _INJECTED.add(s)


def bootstrap(*modules: str, include_tools: bool = False, extra=()) -> None:
    """把仓根（及按需的模块目录 / tools / 子目录）注入 `sys.path`（幂等）。

    modules : 模块包名或短键（`config.constants`）；传 `"all"` 注入全部模块。
    extra   : 额外子目录（相对仓根的 POSIX 路径，或绝对路径）。
              用于模块内的**非包目录**（如 `modules/regulatory_classifier/scripts`、
              `modules/regulatory_scrapers/timeliness_review`、`collectors`），
              这类目录历史上由各调用方自行 `sys.path.insert`。
    """
    from config.constants import (  # noqa: PLC0415
        MODULE_KEYS,
        MODULE_PKGS,
        module_by_key,
        module_by_pkg,
    )

    _add(ROOT)
    wanted: list[str]
    if "all" in modules:
        wanted = list(MODULE_PKGS)
    else:
        wanted = []
        for name in modules:
            try:
                wanted.append(module_by_pkg(name).pkg)
            except KeyError:
                if name in MODULE_KEYS:
                    wanted.append(module_by_key(name).pkg)
                else:
                    raise KeyError(
                        f"未知模块: {name!r}（允许包名 {sorted(MODULE_PKGS)} "
                        f"或短键 {sorted(MODULE_KEYS)}）"
                    ) from None
    # ⚠️ 两类注入都必要（2026-09-26 实测缺口，P1-3 执行中发现）：
    #   · `modules/<pkg>`（**子目录**）→「子包形态」导入可用（`from clean_index import …`、
    #     `import rfn.registry`）——本仓 modules/ 内的主流写法；
    #   · `modules`（**父目录**）→「包形态」导入可用（`import internal_policy_base.align`）
    #     ——`interfaces/internal_policy_api`、`interfaces/theme_api` 等使用该形态。
    # 缺后者时症状极隐蔽：`bootstrap("all")` 后 `import internal_policy_base` 仍
    # `ModuleNotFoundError`（目录在 sys.path 上但缺的是它的**父目录**），
    # 导致 `internal_policy_api.register()` 等路径成为"从未被走通的死代码"。
    _add(ROOT / "modules")
    for pkg in wanted:
        _add(ROOT / "modules" / pkg)
    if include_tools:
        _add(ROOT / "tools")
    for p in extra:
        pp = Path(p)
        _add(pp if pp.is_absolute() else ROOT / pp)


def injected() -> tuple[str, ...]:
    """已注入路径快照（供诊断/门禁断言）。"""
    return tuple(sorted(_INJECTED))


if __name__ == "__main__":  # 离线自检
    bootstrap("all", include_tools=True)
    print("[bootstrap] 已注入:", len(injected()), "条")
