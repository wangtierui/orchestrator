# -*- coding: utf-8 -*-
"""
gates/gate_sources_config — 源目录配置一致性门禁（R15 补全，2026-09-08）

校验 sources.yaml 为唯一事实源被程序一致消费：
  1) enabled 外部源集合 == config.enums.SOURCE_SET（受控枚举与 yaml 双向一致；新增源须两处登记）；
  2) 每 enabled 源「collector」字段可解析且 modules/.../collectors/<模块>.py 存在（拍平后命名对齐）；
     **W4（§7.2 A 类）按 collector_kind 分级**（config.loader.collector_kind）：
       · `collector`（默认）——还须在编排器 `tools/run_production_refresh.py` 的
         COLLECT_CMD/produced_by 有**执行证据**（argv 路径或点号路径引用）——
         堵「纸面 collector」（supp_ingest 曾登记 13 天零执行）；
       · `ingest`——人工/触发器归集工具，无链上 collect 步骤；只断言模块存在
         + 在 `modules/_manifest.json` 有 independent 登记（J9 判据同源断言）；
  3) 每 enabled 源「clean_project」字段存在且 ∈ SOURCE_SET（供 clean 管道 --project 路由）；
  4) disabled 条目应给 disabled_reasons（透明告知，warning 级）。
"""

from __future__ import annotations

import json
import os

import paths
from config.enums import SOURCE_SET
from config.loader import active_source_ids, collector_kind, collector_module, load_sources

_COLLECTORS_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "collectors")
# 编排器（链上执行证据的唯一来源）：COLLECT_CMD argv + GOV_ARTIFACTS produced_by。
_ORCHESTRATOR = os.path.join(paths.TOOLS_DIR, "run_production_refresh.py")
# modules 独立登记清单（ingest 类 collector 的归属证据，与 J9 判据同源）。
_MODULES_MANIFEST = os.path.join(paths.MODULES_DIR, "_manifest.json")


def _chain_invoked(mod: str) -> bool:
    """collector 模块是否在编排器有执行证据。

    W4（L1 重组，2026-10-15）：`mod` 现为**点号路径**（如 `gov.collector`），链上 argv
    形态为 `os.path.join(COLLECTORS, "gov", "collector.py")`。因五源都有同名
    `collector.py`，只查脚本名会互相冒名 ⇒ 断言「源目录名 + 脚本名」**同时**出现
    （produced_by 模板 `collectors.<src>.collector` 形态亦计入）。
    """
    try:
        text = open(_ORCHESTRATOR, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    parts = mod.split(".")
    base = parts[-1]
    if (base + ".py") not in text and ("." + mod) not in text:
        return False
    return all(('"' + p + '"') in text or ("<" + p + ">") in text or ("." + p + ".") in text
               for p in parts[:-1])


def _manifest_independent(mod: str) -> bool:
    """ingest 模块是否在 modules/_manifest.json 登记为 independent（点号路径逐段比对）。"""
    try:
        with open(_MODULES_MANIFEST, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError):
        return False
    parts = mod.split(".")
    want_dir = "/".join(parts[:-1])
    want_file = parts[-1] + ".py"
    for e in man.get("entries") or []:
        f = str(e.get("file") or "")
        if not f.endswith("/" + want_file):
            continue
        if want_dir and f"/{want_dir}/" not in "/" + f:
            continue
        if e.get("category") == "independent":
            return True
    return False


def _collector_file(mod: str) -> str:
    """点号路径 → collectors/ 下的实际脚本路径（`gov.collector` → `gov/collector.py`）。"""
    return os.path.join(_COLLECTORS_DIR, *mod.split(".")) + ".py"


def run():
    problems, warns = [], []
    srcs = load_sources()
    active = set(active_source_ids(refresh=False))

    # 1) yaml enabled 集合 == enums.SOURCE_SET
    if active != set(SOURCE_SET):
        problems.append(
            f"sources.yaml enabled 源 {sorted(active)} ≠ config.enums.SOURCE_SET {sorted(SOURCE_SET)}"
            "（新增源需同步登记两处并重跑 assert_enum_bindings）"
        )

    # 2/3) collector 与 clean_project 解析（按 collector_kind 分级，W4 §7.2）
    for sid in sorted(active):
        cfg = srcs[sid]
        mod = collector_module(sid)
        if not mod:
            problems.append(f"{sid}: 缺 collector 字段")
            continue
        if not os.path.exists(_collector_file(mod)):
            problems.append(
                f"{sid}: collector 模块 {mod} 不存在"
                f"（实际查找 {_collector_file(mod)}——collectors/ 命名未对齐 yaml）"
            )
            continue
        kind = collector_kind(sid)
        if kind == "collector" and not _chain_invoked(mod):
            problems.append(
                f"{sid}: collector 模块 {mod} 在编排器无执行证据"
                "（COLLECT_CMD/produced_by 均未引用——纸面 collector；"
                "若为人工归集工具应在 sources.yaml 标 collector_kind: ingest）"
            )
        elif kind == "ingest" and not _manifest_independent(mod):
            problems.append(
                f"{sid}: ingest 模块 {mod} 未在 modules/_manifest.json 登记 independent"
                "（人工归集工具须显式登记，J9 判据同源）"
            )
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

    return (not problems), {
        "problems": problems[:30],
        "warnings": warns[:10],
        "active": sorted(active),
        "collector_kinds": {sid: collector_kind(sid) for sid in sorted(active)},
        "collectors_dir": _COLLECTORS_DIR,
    }
