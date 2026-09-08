# -*- coding: utf-8 -*-
"""
gates/gate_sources_config — 源目录配置一致性门禁（R15 补全，2026-09-08）

校验 sources.yaml 为唯一事实源被程序一致消费：
  1) enabled 外部源集合 == config.enums.SOURCE_SET（受控枚举与 yaml 双向一致；新增源须两处登记）；
  2) 每 enabled 源「collector」字段可解析且 modules/.../collectors/<模块>.py 存在（拍平后命名对齐）；
  3) 每 enabled 源「clean_project」字段存在且 ∈ SOURCE_SET（供 clean 管道 --project 路由）；
  4) disabled 条目应给 disabled_reasons（透明告知，warning 级）。
"""
from __future__ import annotations

import os

import paths
from config.enums import SOURCE_SET
from config.loader import active_source_ids, collector_module, load_sources

_COLLECTORS_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "collectors")


def run():
    problems, warns = [], []
    srcs = load_sources()
    active = set(active_source_ids(refresh=False))

    # 1) yaml enabled 集合 == enums.SOURCE_SET
    if active != set(SOURCE_SET):
        problems.append(
            f"sources.yaml enabled 源 {sorted(active)} ≠ config.enums.SOURCE_SET {sorted(SOURCE_SET)}"
            "（新增源需同步登记两处并重跑 assert_enum_bindings）")

    # 2/3) collector 与 clean_project 解析
    for sid in sorted(active):
        cfg = srcs[sid]
        mod = collector_module(sid)
        if not mod:
            problems.append(f"{sid}: 缺 collector 字段")
            continue
        if not os.path.exists(os.path.join(_COLLECTORS_DIR, mod + ".py")):
            problems.append(f"{sid}: collector 模块 {mod}.py 不存在（collectors/ 命名未对齐 yaml）")
        cp = (cfg.get("clean_project") or "").strip()
        if not cp:
            problems.append(f"{sid}: 缺 clean_project 字段（clean 管道 --project 路由）")
        elif cp not in SOURCE_SET:
            problems.append(f"{sid}: clean_project {cp!r} ∉ SOURCE_SET")

    # 4) disabled 源透明告知
    for sid, cfg in srcs.items():
        if not cfg.get("enabled") and "." not in sid and sid != "internal":
            if not cfg.get("disabled_reasons"):
                warns.append(f"{sid}: disabled 但缺 disabled_reasons")

    return (not problems), {"problems": problems[:30], "warnings": warns[:10],
                            "active": sorted(active), "collectors_dir": _COLLECTORS_DIR}
