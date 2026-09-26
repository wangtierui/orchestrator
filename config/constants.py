# -*- coding: utf-8 -*-
"""config/constants.py — 结构清单唯一事实源（R25，v2 方案 §3.1.1）

背景（v2 《全链路重构方案·修订版》§2.1 A4）：
    改造前「模块清单」在仓内有 **4 份互不一致**的硬编码副本——
      ① `paths.module_dir()`（4 个，无 base_publish，且**零调用方**）
      ② `gates/gate_no_cross_module_import.py` 的 `MODULES`/`PKG_OWNER`（5 个）
      ③ `gates/gate_flat_layout.py` 的遍历列表与 `ALLOWED_DATA_SUBDIRS` 键（4 个）
      ④ `tools/build_migration_manifest.py` 的 `MODULE_SUBDIRS`（4 个，键名歧义）
    本文件把该清单上收为**唯一声明**，其余位置一律派生；`gate_module_registry`
    断言派生一致性，防止再次漂移。

纪律：
  - 新增/改名模块：**只改本文件** + 同步本模块 docstring 的变更记录；
  - 禁止在其它模块本地定义模块名列表（含 `gates/`、`tools/`、`tests/`）；
  - 阶段 id 供 `cli.py run --from/--only` 与 `triggers.yaml` 的 `stage` 字段引用。
"""

from __future__ import annotations

from typing import Final, NamedTuple


class ModuleSpec(NamedTuple):
    """单个业务模块的声明。

    key : 短键（迁移清单/文档/CLI 展示用；与包名解耦，避免 `base`/`ipb` 歧义）
    pkg : 包名（即 `modules/<pkg>/`，供 `paths.module_dir()` 与 sys.path 注入）
    kind: core（业务主链）| publish（发布层）
    """

    key: str
    pkg: str
    kind: str


# --------------------------------------------------------------------------- #
# 模块清单（唯一声明）
# --------------------------------------------------------------------------- #
MODULE_SPECS: Final[tuple[ModuleSpec, ...]] = (
    ModuleSpec("scrapers", "regulatory_scrapers", "core"),
    ModuleSpec("classifier", "regulatory_classifier", "core"),
    ModuleSpec("ipb", "internal_policy_base", "core"),
    ModuleSpec("drafter", "internal_policy_drafter", "core"),
    # base_publish 此前长期缺席于 paths/gate_flat_layout，但 gate_no_cross_module_import
    # 早已把它算作第 5 个模块 —— 本次统一（v2 §2.1 A4）
    ModuleSpec("publish", "base_publish", "publish"),
)

MODULE_PKGS: Final[tuple[str, ...]] = tuple(m.pkg for m in MODULE_SPECS)
MODULE_KEYS: Final[frozenset[str]] = frozenset(m.key for m in MODULE_SPECS)
MODULE_PKG_SET: Final[frozenset[str]] = frozenset(MODULE_PKGS)

_BY_KEY: Final[dict[str, ModuleSpec]] = {m.key: m for m in MODULE_SPECS}
_BY_PKG: Final[dict[str, ModuleSpec]] = {m.pkg: m for m in MODULE_SPECS}


def module_by_key(key: str) -> ModuleSpec:
    """按短键取模块声明；未知键抛 KeyError（不做静默兜底）。"""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知模块键: {key!r}（允许 {sorted(MODULE_KEYS)}）") from None


def module_by_pkg(pkg: str) -> ModuleSpec:
    """按包名取模块声明；未知包名抛 KeyError。"""
    try:
        return _BY_PKG[pkg]
    except KeyError:
        raise KeyError(f"未知模块包名: {pkg!r}（允许 {sorted(MODULE_PKG_SET)}）") from None


def is_module_pkg(name: str) -> bool:
    return name in MODULE_PKG_SET


# --------------------------------------------------------------------------- #
# 主链阶段清单（唯一声明）
# --------------------------------------------------------------------------- #
# 依据 `tools/run_production_refresh.py` 模块 docstring 与代码实际调用顺序。
# 注意：**编号顺序 ≠ 执行顺序**（6.8 analysis gen 先于 6.5 watch baseline 执行，
# 见该文件 docstring）。此处按编号排序，执行顺序由编排器持有。
PIPELINE_STAGES: Final[tuple[str, ...]] = (
    "0",
    "1",
    "2",
    "2.5",
    "2.55",
    "2.6",
    "3",
    "3.5",
    "4",
    "4.2",
    "4.5",
    "5.5",
    "6",
    "6.8",
    "6.5",
)

# 条件触发链阶段（v2 §3.5 设计，尚未接线；供 triggers.yaml 的 stage 字段引用）
CONDITIONAL_STAGES: Final[tuple[str, ...]] = ("6.9", "7.4", "7.5", "21.5")

STAGES: Final[frozenset[str]] = frozenset(PIPELINE_STAGES) | frozenset(CONDITIONAL_STAGES)
STAGE_ORDER: Final[dict[str, int]] = {s: i for i, s in enumerate(PIPELINE_STAGES)}


# --------------------------------------------------------------------------- #
# 自检（供 gate_module_registry 调用）
# --------------------------------------------------------------------------- #
def assert_registry_consistent() -> None:
    """清单自身完整性自检：键/包名唯一、kind 受控、阶段编号唯一。"""
    assert len(MODULE_SPECS) == 5, MODULE_SPECS
    assert len(MODULE_KEYS) == 5, "模块短键重复"
    assert len(MODULE_PKG_SET) == 5, "模块包名重复"
    assert {m.kind for m in MODULE_SPECS} <= {"core", "publish"}, "kind 非受控值"
    assert len(PIPELINE_STAGES) == 15, PIPELINE_STAGES
    assert len(set(PIPELINE_STAGES)) == 15, "主链阶段编号重复"
    assert not (set(PIPELINE_STAGES) & set(CONDITIONAL_STAGES)), "阶段编号跨链重复"


if __name__ == "__main__":  # 离线自检
    assert_registry_consistent()
    print("[config.constants] 自检通过：", MODULE_PKGS)
