# -*- coding: utf-8 -*-
"""
gates/gate_field_aliases — 中文列名受控注册门禁（字段治理 2026-09-08，批次1）

数据"CSV 系以中文列名为正式编码"是已定决策（不破坏性改名），治理方式=**逐层登记**：
  - 每个权威数据表（归属/主题/明细/桥/rfn 索引/指纹）表头的中文列必须 ∈ interfaces/contract.CN_FIELD_REGISTRY
    （登记其英文规范名 en/scope/note）——新增中文列未登记即 FAIL，杜绝"新列无规范别名漂移"。
  - 注册表回验：scope≠recall 的登记名应在至少一个被扫描文件表头中出现（否则提示维护）。

补充（FP 契约对齐）：rfn/文件指纹.csv 表头须 = registry.FP_FIELDS(6 列，含「唯一键」)。
"""
from __future__ import annotations

import csv
import glob
import os
import re

import paths
from interfaces.contract import CN_FIELD_REGISTRY

_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
_RFN = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "rfn")

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _head(p: str) -> list[str]:
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh).fieldnames or [])


def run():
    problems, warns = [], []
    scanned: list[list[str]] = []
    targets = []

    def _scan(path: str, label: str):
        if not os.path.exists(path):
            return
        head = _head(path)
        scanned.append(head)
        for col in head:
            if _CJK.search(col) and col not in CN_FIELD_REGISTRY:
                problems.append(f"{label}: 中文列 {col!r} 未在 contract.CN_FIELD_REGISTRY 登记")

    _scan(os.path.join(_DATA, "人身保险公司-文件归属表.csv"), "归属表")
    _scan(os.path.join(_DATA, "人身保险公司-主题归属表.csv"), "主题归属表")
    for f in sorted(glob.glob(os.path.join(_DATA, "T*_*.csv"))):
        _scan(f, f"明细 {os.path.basename(f)[:30]}")
    _scan(os.path.join(_DATA, "rfn_clean_bridge.csv"), "桥表")
    for f in ("监管文件编号索引.csv", "文件指纹.csv"):
        p = os.path.join(_RFN, f)
        if os.path.exists(p):
            _scan(p, f"rfn/{f}")
    # FP 契约对齐：文件指纹表头 == FP_FIELDS(6)
    fp = os.path.join(_RFN, "文件指纹.csv")
    if os.path.exists(fp):
        try:
            from rfn.registry import FP_FIELDS  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            FP_FIELDS = []
        if FP_FIELDS and _head(fp) != FP_FIELDS:
            problems.append(f"rfn/文件指纹.csv 表头 ≠ registry.FP_FIELDS(6列): {_head(fp)}")
    # 回验：非 recall 的登记列至少出现一次
    seen = {c for h in scanned for c in h}
    unused = [k for k, v in CN_FIELD_REGISTRY.items()
              if v.get("scope") != "recall" and k not in seen]
    if unused:
        warns.append(f"注册表未命中列（scope≠recall 却未见文件表头）: {unused}")
    return (not problems), {"problems": problems[:30], "warnings": warns[:10],
                            "registered": len(CN_FIELD_REGISTRY), "checked_files": len(scanned)}
