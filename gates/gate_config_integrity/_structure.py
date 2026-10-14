# -*- coding: utf-8 -*-
"""gates.gate_config_integrity._structure — B 族判据（W4-c 五拆）。

config/constants.py 结构断言（退出码/受控域镜像）。公共基础设施经 `._common`。
"""

from __future__ import annotations

import os

from ._common import ROOT


def _check_constants() -> tuple[list[str], dict]:
    problems: list[str] = []
    detail: dict = {}
    # 引导：本模块顶层 `import paths` 已要求仓根在 sys.path（由 cli.py/GatesRunner 保证），
    # 不再自行注入（P0-6 纪律：注入只经 bootstrap.py）。

    from config import constants as C

    try:
        C.assert_registry_consistent()
    except AssertionError as e:
        problems.append(f"B1: config.constants 自检失败：{e}")

    # B2 派生点一致性
    import paths as _p

    try:
        for pkg in C.MODULE_PKGS:
            assert os.path.isdir(_p.module_dir(pkg)), pkg
    except AssertionError as e:
        problems.append(f"B2: paths.module_dir 无法解析模块 {e}")

    from gates import gate_flat_layout, gate_no_cross_module_import

    if set(gate_no_cross_module_import.MODULES) != set(C.MODULE_PKGS):
        problems.append(
            "B2: gate_no_cross_module_import.MODULES 与 MODULE_PKGS 不一致："
            f"{sorted(gate_no_cross_module_import.MODULES)} vs {sorted(C.MODULE_PKGS)}"
        )

    flat_mods = {
        "regulatory_scrapers",
        "regulatory_classifier",
        "internal_policy_base",
        "internal_policy_drafter",
    }  # 见 gate_flat_layout 遍历表
    if not flat_mods <= set(C.MODULE_PKGS):
        problems.append(
            f"B2: gate_flat_layout 遍历的模块未全部登记：{sorted(flat_mods - set(C.MODULE_PKGS))}"
        )
    if set(gate_flat_layout.ALLOWED_DATA_SUBDIRS) - set(C.MODULE_PKGS):
        problems.append("B2: gate_flat_layout.ALLOWED_DATA_SUBDIRS 含未登记模块键")

    for m in C.MODULE_SPECS:  # PKG_OWNER 应覆盖全部模块包
        if m.pkg not in gate_no_cross_module_import.PKG_OWNER:
            problems.append(f"B2: PKG_OWNER 缺模块 {m.pkg}")
    detail["modules"] = list(C.MODULE_PKGS)

    # C1 退出码
    from config.exitcodes import ExitCode

    assert int(ExitCode.OK) == 0 and int(ExitCode.FAIL) == 1 and int(ExitCode.DATA) == 2
    assert int(ExitCode.ENV) == 3 and int(ExitCode.NOT_SOURCE_TREE) == 4
    detail["exitcodes"] = {e.name: int(e) for e in ExitCode}

    # D 组：待落地项（报告不阻断）
    pending = []
    for rel in ("config/triggers.yaml", "config/schedule.yaml"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            pending.append(rel)
    detail["pending"] = pending
    return problems, detail
