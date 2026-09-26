# -*- coding: utf-8 -*-
"""
gates/gate_provenance — 数据血缘（provenance）覆盖门禁（R10，2026-09-08）

校验 classifier 各派生产物记录均带写者血缘字段（二期 traceability 打底）：
  - _t*_base.json：每条 generated_by/generated_at/source_snapshot（build_base_from_attr 注入）
  - _t*_final.json：每条 finalized_by/finalized_at（cluster_by_keywords 派生批次，继承 base 三键）
  - 明细表 T*_*.csv：表头含 generated_by/generated_at（build_detail_tables）
  - rfn_clean_bridge.csv：表头含 generated_by/generated_at（reconcile/rfn.bridge upsert 注入）
归属表为人工权威表，其血缘由 base source_snapshot（归属表 mtime）间接承载，不另行要求行级字段。
"""

from __future__ import annotations

import csv
import glob
import json
import os

import paths

_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")

BASE_PROV = ("generated_by", "generated_at", "source_snapshot")
FINAL_PROV = ("finalized_by", "finalized_at")
_CSV_PROV = ("generated_by", "generated_at")


def _check_records(problems, warns):
    base_files = sorted(glob.glob(os.path.join(_DATA, "_t*_base.json")))
    final_files = sorted(glob.glob(os.path.join(_DATA, "_t*_final.json")))
    if not base_files:
        warns.append("data 无 _t*_base.json（底座未生成；provenance 覆盖待数据就绪）")
    for fp in base_files:
        name = os.path.basename(fp)
        recs = json.load(open(fp, encoding="utf-8"))
        bad = [r for r in recs if not all(r.get(k) for k in BASE_PROV)]
        if bad:
            problems.append(f"{name}: {len(bad)}/{len(recs)} 条缺 provenance({BASE_PROV})")
    for fp in final_files:
        name = os.path.basename(fp)
        recs = json.load(open(fp, encoding="utf-8"))
        bad = [r for r in recs if not all(r.get(k) for k in FINAL_PROV)]
        if bad:
            problems.append(
                f"{name}: {len(bad)}/{len(recs)} 条缺 finalized provenance({FINAL_PROV})"
            )


def _check_csv(problems, pattern, what, warn_if_empty=True):
    files = sorted(glob.glob(os.path.join(_DATA, pattern)))
    if not files:
        if warn_if_empty:
            problems.append(f"{what}: 无匹配文件 {pattern}")
        return
    for fp in files:
        name = os.path.basename(fp)
        with open(fp, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            head = reader.fieldnames or []
        missing = [k for k in _CSV_PROV if k not in head]
        if missing:
            problems.append(f"{name}: 表头缺 provenance 列 {missing}")


def run():
    problems, warns = [], []
    if not os.path.isdir(_DATA):
        # F-S09：输入缺失不得空跑放行（原 return True 使"全部门禁通过"含未实检门禁）。
        return False, {
            "error": f"classifier data 未就绪（{_DATA}）；provenance 门禁未实检，不得视为通过",
            "problems": problems,
        }
    _check_records(problems, warns)
    _check_csv(problems, "T*_*.csv", "明细表")
    _check_csv(problems, "rfn_clean_bridge.csv", "桥表")
    # 桥表行级抽查：非空行应带值（generated_by/at）
    bp = os.path.join(_DATA, "rfn_clean_bridge.csv")
    if os.path.exists(bp):
        with open(bp, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        nob = [r for r in rows if not (r.get("generated_by") and r.get("generated_at"))]
        if nob:
            problems.append(f"rfn_clean_bridge.csv: {len(nob)}/{len(rows)} 行缺 provenance 值")
    return (not problems), {
        "base_files": len(glob.glob(os.path.join(_DATA, "_t*_base.json"))),
        "problems": problems[:30],
        "warnings": warns[:10],
    }
