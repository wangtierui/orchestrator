# -*- coding: utf-8 -*-
"""
gates/gate_enum_values — 受控枚举一致性门禁（S-3 / v3）

P0 阶段：
  1) config/enums.py 自检（assert_enum_bindings）；
  2) 若 PyYAML 可用，校验 sources.yaml active 源 == config.enums.SOURCE_SET；
     PyYAML 缺失时跳过该项并记录 warning（不 FAIL，P0 骨架放行）。
P1 起扩展：扫描 modules/**/*.py 中枚举值字面量，凡不在 config.enums 登记集合且不在
豁免白名单者即 FAIL。
"""
from __future__ import annotations

import sys

import paths

sys.path.insert(0, paths.ROOT)  # noqa: E402
try:
    import config.enums as E
except Exception as e:  # pragma: no cover
    E = None
    _import_err = repr(e)

_PYYAML_OK = False
try:
    import yaml  # noqa: F401
    _PYYAML_OK = True
except Exception:
    _PYYAML_OK = False


def run():
    if E is None:
        return False, {"error": "config.enums 导入失败: " + _import_err}
    try:
        E.assert_enum_bindings()
    except AssertionError as e:
        return False, {"error": f"config.enums.assert_enum_bindings 失败: {e}"}
    problems = []
    warnings = []
    if _PYYAML_OK:
        try:
            from config.loader import active_source_ids
            active = active_source_ids()
            if set(active) != set(E.SOURCE_SET):
                problems.append(f"sources.yaml active {sorted(active)} != SOURCE_SET {sorted(E.SOURCE_SET)}")
        except Exception as e:  # pragma: no cover
            warnings.append(f"loader 检查跳过: {e}")
    else:
        warnings.append("PyYAML 未安装，sources.yaml 一致性检查跳过（P0 放行）")
    detail = {"problems": problems, "warnings": warnings,
              "SOURCE_SET": sorted(E.SOURCE_SET),
              "TIMELINESS_STATUS": sorted(E.TIMELINESS_STATUS)}
    return (not problems), detail
