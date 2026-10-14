# -*- coding: utf-8 -*-
"""modules.implementation_audit.ingest — 执行记录摄取（台账 ledgers 解析，W3 §5.3.3）。

输入：`data/inbox/ledgers/` 下的台账文件（CSV；XLSX 复用 `reg_frameworks.extract` 的最小
读取器——**不引 openpyxl**）。输出：`ImplementationRecord` 事实源（JSONL）。

列映射（可配 `COLUMN_ALIASES`，与既有模块的字段别名机制同款）：
    部门 → dept｜期间 → period｜IPN → ipn｜条款号 → article_no｜执行状态 → exec_status
执行状态**归一**到受控域 EXEC_STATUS（已执行/部分执行/未执行 及英文同义）——
台账里的自由文本必须归一，否则规则表求值会因字面差异漏判。
"""
from __future__ import annotations

import os
import re

from config.enums import EXEC_STATUS
from modules.implementation_audit import implaudit_model as model
from modules.implementation_audit import implaudit_store as store
from std_lib.common_lib import tabular as _tabular

#: 列名别名（中文台账列头 → 规范字段）
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "dept": ("部门", "责任部门", "单位", "dept", "department"),
    "period": ("期间", "月份", "季度", "报告期", "period", "month"),
    "ipn": ("ipn", "制度编号", "制度", "制度编号/ipn"),
    "article_no": ("条款号", "条款", "条", "article_no", "article"),
    "exec_status": ("执行状态", "执行情况", "状态", "exec_status", "status"),
}

#: 执行状态归一（自由文本 → 受控域；未命中 → `not_executed` 且**计数披露**）
_STATUS_MAP: dict[str, str] = {
    "已执行": "executed", "执行": "executed", "完成": "executed", "executed": "executed", "是": "executed",
    "部分执行": "partial", "部分": "partial", "partial": "partial",
    "未执行": "not_executed", "未": "not_executed", "未执行/不适用": "not_executed",
    "not_executed": "not_executed", "否": "not_executed",
}


def _pick(header: list[str], field: str) -> int:
    norm = [re.sub(r"\s+", "", str(h or "")) for h in header]
    for alias in COLUMN_ALIASES[field]:
        if alias in norm:
            return norm.index(alias)
    return -1


def normalize_status(raw: str) -> tuple[str, bool]:
    """执行状态归一 → `(受控值, 是否命中)`；未命中返回 `not_executed`（保守：不得当已执行）。"""
    s = re.sub(r"\s+", "", str(raw or ""))
    if s in _STATUS_MAP:
        return _STATUS_MAP[s], True
    for k, v in _STATUS_MAP.items():
        if k and k in s:
            return v, True
    return "not_executed", False


def parse_file(p: str) -> dict:
    """解析单个台账文件 → `{records, unmapped_status, rows, skipped}`。"""
    try:
        rows = _tabular.read_rows(p)
    except ValueError as e:
        return {"records": [], "rows": 0, "skipped": 1, "reason": str(e)}
    if not rows:
        return {"records": [], "rows": 0, "skipped": 1, "reason": "空文件"}

    header = [str(c or "") for c in rows[0]]
    idx = {f: _pick(header, f) for f in COLUMN_ALIASES}
    missing = [f for f in ("dept", "period", "exec_status") if idx[f] < 0]
    if missing:
        return {"records": [], "rows": len(rows) - 1, "skipped": 1,
                "reason": f"缺少必需列 {missing}（表头：{header[:8]}）"}

    recs: list[dict] = []
    unmapped = 0
    for r in rows[1:]:
        # ⚠️ cell 显式收行参（B023：闭包捕获循环变量 `r` —— 迟绑定会让所有行读到**最后一行**）
        def cell(field: str, row: list = r) -> str:
            i = idx[field]
            return str(row[i] or "").strip() if 0 <= i < len(row) else ""

        st, hit = normalize_status(cell("exec_status"))
        if not hit:
            unmapped += 1
        dept, period = cell("dept"), cell("period")
        if not dept or not period:
            continue
        ipn = cell("ipn") or None
        art = cell("article_no") or None
        rec = model.ImplementationRecord(
            rid=model.ImplementationRecord.mk_id(dept, period, ipn, art),
            dept=dept, period=period, exec_status=st, ipn=ipn, article_no=art,
            source_file=os.path.basename(p),
        )
        recs.append(rec.to_dict())
    return {"records": recs, "rows": len(rows) - 1, "skipped": 0, "unmapped_status": unmapped}


def ingest_dir(src_dir: str, *, persist: bool = True) -> dict:
    """批量摄取台账目录 → upsert 事实源；逐文件结果**全部披露**（含未映射状态计数）。"""
    files = sorted(f for f in os.listdir(src_dir)
                   if os.path.splitext(f)[1].lower() in (".csv", ".xlsx"))
    all_recs: list[dict] = []
    per_file: list[dict] = []
    unmapped = 0
    for fn in files:
        r = parse_file(os.path.join(src_dir, fn))
        all_recs.extend(r["records"])
        unmapped += int(r.get("unmapped_status") or 0)
        per_file.append({"file": fn, "records": len(r["records"]),
                         "skipped": r["skipped"],
                         **({"reason": r["reason"]} if r.get("reason") else {})})
    res = {"files": len(files), "records": len(all_recs),
           "unmapped_status": unmapped, "per_file": per_file,
           "exec_status_domain": sorted(EXEC_STATUS)}
    if persist and all_recs:
        res["write"] = store.write_rows(store.F_RECORDS, all_recs, key="rid")
    return res
