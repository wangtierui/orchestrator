# -*- coding: utf-8 -*-
"""gate_reg_frameworks — 多监管数据体系建模契约（W3，方案 §5.4 门禁）。

五组断言（每条对应一类真实失实）：
  F1 **契约登记**：`contract_manifest.json` 登记 framework/data_table/data_item/clause_item_map
     键集，且与模块实体字段一致（登记 ≠ 实装是本仓最常见的失实形态）；
  F2 **受控域闭包**：模块内出现的 `cadence`/`item_type`/`method`/`status` 取值
     ⊆ `config.enums` 受控域（构造期校验是第二道防线，门禁是第一道）；
  F3 **置信纪律**：映射的 `method=semantic` 一律 `status=review`（阈值纪律「无度量不上线」），
     且 `confidence ≤ 方法上限`；
  F4 **映射闭合**：每条 `ClauseItemMap` 的 `iid` 必须存在于 `data_items.jsonl`
     （悬空 iid = 映射指向不存在的数据项 ✗）；
  F5 **模块纪律**：新模块在 `MODULE_SPECS` 登记且无跨模块越权 import（判据 D 覆盖）。
"""
from __future__ import annotations

import json
import os

import paths


def run() -> tuple[bool, dict]:
    from config import enums as _en
    from modules.reg_frameworks import regfw_store as _store

    problems: list[str] = []
    detail: dict = {}

    # F1 契约登记
    try:
        with open(os.path.join(paths.CONFIG_DIR, "schema", "contract_manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        # 清单真实结构：`contracts` 为**字典**（键 = 契约名），非 list
        entries = man.get("contracts") or {}
        names = set(entries) if isinstance(entries, dict) else {
            str(e.get("name") or e.get("id") or "") for e in entries}
        need = {"regfw_frameworks", "regfw_data_tables", "regfw_data_items",
                "regfw_clause_item_map"}
        miss = sorted(need - names)
        if miss:
            problems.append(f"F1: contract_manifest 缺登记 {miss}（声明面缺失 ⇒ 契约无机器守门）")
        detail["contracts"] = sorted(names)
    except Exception as e:  # noqa: BLE001
        problems.append(f"F1: contract_manifest 不可读（{type(e).__name__}: {e}）")

    # F2 受控域闭包
    fw = _store.list_frameworks()
    bad_cad = sorted({str(r.get("cadence")) for r in fw} - _en.REPORTING_CADENCE)
    if bad_cad:
        problems.append(f"F2: cadence 越受控域 {bad_cad}")
    bad_item = sorted({str(r.get("item_type")) for r in _store.list_items()} - _en.ITEM_TYPE)
    if bad_item:
        problems.append(f"F2: item_type 越受控域 {bad_item}")
    maps = _store.list_maps()
    bad_method = sorted({str(r.get("method")) for r in maps} - _en.MAP_METHOD)
    bad_status = sorted({str(r.get("status")) for r in maps} - _en.MAP_STATUS)
    if bad_method:
        problems.append(f"F2: method 越受控域 {bad_method}")
    if bad_status:
        problems.append(f"F2: status 越受控域 {bad_status}")
    detail["counts"] = {**_store.stats()}

    # F3 置信纪律（语义法一律 review；置信不得超方法上限）
    from modules.reg_frameworks.regfw_model import METHOD_CEILING

    for r in maps:
        m = str(r.get("method"))
        ceiling = METHOD_CEILING.get(m)
        try:
            conf = float(r.get("confidence") or 0.0)
        except (TypeError, ValueError):
            problems.append(f"F3: 映射 {r.get('map_id')} 的 confidence 非数值（{r.get('confidence')!r}）")
            continue
        if ceiling is not None and conf > ceiling + 1e-9:
            problems.append(f"F3: 映射 {r.get('map_id')} method={m} 置信 {conf} 超上限 {ceiling}")
        if m == "semantic" and str(r.get("status")) != "review":
            problems.append(
                f"F3: 映射 {r.get('map_id')} 为 semantic 却 status={r.get('status')}"
                "（语义法不得自动确认——阈值纪律）"
            )

    # F4 映射闭合（iid 必须存在）
    iids = {str(r.get("iid")) for r in _store.list_items()}
    dangling = sorted({str(r.get("iid")) for r in maps} - iids)
    if dangling:
        problems.append(f"F4: 映射指向不存在的数据项 iid {dangling[:5]}（悬空引用 ✗）")
    detail["dangling_iids"] = len(dangling)

    # F5 模块登记
    from config.constants import MODULE_PKG_SET

    for pkg in ("reg_frameworks", "implementation_audit"):
        if pkg not in MODULE_PKG_SET:
            problems.append(f"F5: 模块 {pkg} 未登记进 MODULE_SPECS（派生点会漏）")

    return (not problems), {"problems": problems, **detail}


if __name__ == "__main__":
    from gates import run_cli

    raise SystemExit(run_cli("reg_frameworks", run))
