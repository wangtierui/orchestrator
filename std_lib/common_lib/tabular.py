# -*- coding: utf-8 -*-
"""std_lib/common_lib/tabular.py — 表格读取原语（CSV / XLSX，纯标准库）。

W3 动因：reg_frameworks.extract 与 implementation_audit.ingest 都需读表；
各写一份是重复实现，跨模块直接 import 违反单向依赖（判据 D 会拦）→ 上收到共享层。
"""
from __future__ import annotations

import csv
import os
import xml.etree.ElementTree as ET
import zipfile

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SUPPORTED = (".csv", ".xlsx")


def read_csv_rows(p: str) -> list[list[str]]:
    """CSV → 行矩阵（去全空行；utf-8-sig 容忍 BOM）。"""
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.reader(fh) if any((c or "").strip() for c in r)]


def read_xlsx_rows(p: str) -> list[list[str]]:
    """XLSX（第一个 sheet）→ 行矩阵（sharedStrings 解析）。"""
    rows: list[list[str]] = []
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
        if not sheets:
            return []
        sroot = ET.fromstring(z.read(sheets[0]))
        for row in sroot.iter(f"{_NS}row"):
            vals: list[str] = []
            for c in row.findall(f"{_NS}c"):
                v = c.find(f"{_NS}v")
                raw = v.text if v is not None else ""
                if str(c.get("t") or "") == "s":
                    try:
                        raw = shared[int(raw or "")]
                    except (ValueError, IndexError):
                        raw = ""
                vals.append(str(raw))
            if any(v.strip() for v in vals):
                rows.append(vals)
    return rows


def read_rows(p: str) -> list[list[str]]:
    """按扩展名分发（不支持 → 抛 ValueError，由调用方披露跳过原因）。"""
    ext = os.path.splitext(p)[1].lower()
    if ext == ".csv":
        return read_csv_rows(p)
    if ext == ".xlsx":
        return read_xlsx_rows(p)
    raise ValueError(f"不支持的表格扩展名：{ext}（支持 {SUPPORTED}）")


def list_tables(d: str) -> list[str]:
    """目录内受支持的表格文件（排序稳定）。"""
    return sorted(f for f in os.listdir(d) if os.path.splitext(f)[1].lower() in SUPPORTED)
