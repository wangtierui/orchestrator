# -*- coding: utf-8 -*-
"""tools/build_east_structure.py — EAST 数据结构一览（数据表 / 数据项）构建。

为什么要把它做成**产物**而不是页面里现读 Excel
------------------------------------------------
「监管统计」页要展示 EAST 的**数据表清单 + 每个表的数据项**（用户要求参照监管总局
的《数据结构一览表（修订版）》）。源文件是 1.6MB 的 `.xlsm`，且在**桌面目录**
（例如桌面下的某个治理目录）—— 页面直接读它有三个问题：
  1. 服务账户未必能访问桌面路径（内网用同事的机器更没有这个文件）；
  2. openpyxl 解析 76 个 sheet 需数秒，每次翻页都付一次代价；
  3. 源文件是**外部参照**，不纳入版本库就无法审计"页面上的表结构是哪一版"。

所以：**一次性解析 → 落 JSON 产物 → 页面只读产物**。产物随仓提交，谁都能 diff
出版本差异（数据项增删改一眼可见）。

用法
----
    python -m tools.build_east_structure --xlsx "<数据结构一览表.xlsm>"   # 生成
    python -m tools.build_east_structure --check                        # 校验产物

读取规则（实测自 2024 版修订版一览表）
--------------------------------------
· 工作表 `数据结构` 是**一张扁平大表**：一行一个数据项，24 列（表头在第 1 行）；
· `数据结构目录` 是表级目录（表名/中文名/表编号/数据表说明/数据表性质/采集模式）；
· `数据结构` 里夹着"主题域行""表头行"（无 `数据项标识`）⇒ 必须按该列非空过滤，
  否则会把标题当数据项（实测 3190 行里只有 2900+ 是真数据项）。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import sys

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

OUT_DIR = os.path.join(paths.DATA_DIR, "east")
OUT_JSON = os.path.join(OUT_DIR, "east_structure.json")
SCHEMA_VERSION = "1.0"

#: `数据结构` 工作表的列序（1-based）→ 字段名。**按位置取而不是按表头文字匹配**：
#: 表头里有换行与合并单元格（`是否\\n空表`），按文字匹配会在版本间脆断。
COLS_ITEM = {
    1: "is_empty_table",
    2: "seq_in_table",
    3: "seq_all",
    4: "item_id",
    5: "domain",
    6: "domain_code",
    7: "table_code",
    8: "table_name",
    9: "table_no",
    10: "item_name",
    11: "item_code",
    12: "is_pk",
    13: "nullable",
    14: "item_desc",
    15: "default_note",
    16: "changed",
    17: "change_scene",
    18: "element_code",
    19: "element_name",
    20: "element_desc",
    21: "value_range",
    22: "data_format",
    23: "link",
}
#: `数据结构目录` 的列序 → 字段名
COLS_DIR = {
    1: "domain",
    2: "table_code",
    3: "table_name",
    4: "table_no",
    5: "table_desc",
    6: "table_nature",
    7: "mode_code_focus",
    8: "mode_desc_focus",
    9: "scope_focus",
    10: "mode_code_cont",
    11: "mode_desc_cont",
    12: "scope_cont",
}


def _s(v: object) -> str:
    """单元格 → 单行字符串（去换行、去首尾空白）。"""
    if v is None:
        return ""
    return str(v).replace("\r", " ").replace("\n", " ").strip()


def _read_dir(wb) -> dict[str, dict]:
    """`数据结构目录` → {表编号: 表级元数据}。"""
    if "数据结构目录" not in wb.sheetnames:
        return {}
    ws = wb["数据结构目录"]
    out: dict[str, dict] = {}
    for idx, row in enumerate(ws.iter_rows(values_only=True), 1):
        if idx <= 3:            # 1 行大标题 + 2 行两级表头
            continue
        rec = {name: _s(row[i - 1] if i - 1 < len(row) else "") for i, name in COLS_DIR.items()}
        if not rec.get("table_no") and not rec.get("table_code"):
            continue
        key = rec.get("table_no") or rec.get("table_code")
        out[key] = rec
    return out


def parse_xlsx(path: str) -> dict:
    """解析一览表 → 结构化产物（纯函数，便于守卫直接调用）。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    dir_map = _read_dir(wb)
    if "数据结构" not in wb.sheetnames:
        raise ValueError("工作簿缺少「数据结构」工作表（版本不符）")
    ws = wb["数据结构"]

    tables: dict[str, dict] = {}
    n_rows = n_skipped = 0
    for idx, row in enumerate(ws.iter_rows(values_only=True), 1):
        if idx == 1:            # 表头
            continue
        n_rows += 1
        rec = {name: _s(row[i - 1] if i - 1 < len(row) else "") for i, name in COLS_ITEM.items()}
        if not rec.get("item_id"):
            n_skipped += 1      # 主题域行 / 表标题行 / 空行
            continue
        key = rec.get("table_no") or rec.get("table_code")
        tbl = tables.get(key)
        if tbl is None:
            meta = dir_map.get(key, {})
            tbl = {
                "table_no": key,
                "table_code": rec.get("table_code") or meta.get("table_code", ""),
                "table_name": rec.get("table_name") or meta.get("table_name", ""),
                "domain": rec.get("domain") or meta.get("domain", ""),
                "domain_code": rec.get("domain_code", ""),
                "table_desc": meta.get("table_desc", ""),
                "table_nature": meta.get("table_nature", ""),
                "mode_code_focus": meta.get("mode_code_focus", ""),
                "scope_focus": meta.get("scope_focus", ""),
                "items": [],
            }
            tables[key] = tbl
        tbl["items"].append({
            "seq_all": rec["seq_all"],
            "seq_in_table": rec["seq_in_table"],
            "item_id": rec["item_id"],
            "item_code": rec["item_code"],
            "item_name": rec["item_name"],
            "is_pk": rec["is_pk"],
            "nullable": rec["nullable"],
            "data_format": rec["data_format"],
            "item_desc": rec["item_desc"],
            "value_range": rec["value_range"],
            "element_code": rec["element_code"],
            "element_name": rec["element_name"],
            "changed": rec["changed"],
            "change_scene": rec["change_scene"],
        })
    wb.close()

    order = sorted(tables)
    items_total = sum(len(t["items"]) for t in tables.values())
    by_domain: dict[str, int] = collections.Counter(t["domain"] for t in tables.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generated_by": "tools/build_east_structure.py",
        "source_file": os.path.basename(path),
        "source_sha256": _sha256(path),
        "stats": {
            "tables": len(tables),
            "items": items_total,
            "rows_scanned": n_rows,
            "rows_skipped": n_skipped,
            "pk_items": sum(1 for t in tables.values() for i in t["items"] if i["is_pk"] == "Y"),
            "by_domain": dict(by_domain.most_common()),
        },
        "tables": [tables[k] for k in order],
    }


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(data: dict) -> list[str]:
    """产物自检（结构不变量；供 `--check` 与守卫共用）。"""
    bad: list[str] = []
    tables = data.get("tables") or []
    if not tables:
        return ["产物为空（tables 为空）"]
    if len(tables) != int((data.get("stats") or {}).get("tables") or -1):
        bad.append("stats.tables 与 tables 长度不一致")
    seen_no: set[str] = set()
    for t in tables:
        no = t.get("table_no") or ""
        if not no:
            bad.append(f"表缺少表编号：{t.get('table_code')!r}")
        elif no in seen_no:
            bad.append(f"表编号重复：{no}")
        seen_no.add(no)
        if not t.get("table_code") or not t.get("table_name"):
            bad.append(f"表 {no} 缺表名/中文名")
        items = t.get("items") or []
        if not items:
            bad.append(f"表 {no} 没有数据项")
            continue
        ids = [i.get("item_id") for i in items]
        if len(ids) != len(set(ids)):
            bad.append(f"表 {no} 存在重复数据项标识")
        for i in items:
            if not i.get("item_id") or not i.get("item_name"):
                bad.append(f"表 {no} 有数据项缺标识/名称")
                break
    total = sum(len(t.get("items") or []) for t in tables)
    if total != int((data.get("stats") or {}).get("items") or -1):
        bad.append("stats.items 与明细不一致")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_east_structure",
                                 description="EAST 数据结构一览（表/数据项）→ 产物")
    ap.add_argument("--xlsx", default="", help="《数据结构一览表》xlsm/xlsx 路径")
    ap.add_argument("--out", default=OUT_JSON, help=f"输出（默认 {OUT_JSON}）")
    ap.add_argument("--check", action="store_true", help="校验已落盘产物")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    a = ap.parse_args(argv)

    if a.check:
        if not os.path.exists(a.out):
            print(f"[east] 产物不存在：{a.out}")
            return ExitCode.FAIL
        data = json.load(open(a.out, encoding="utf-8"))
        bad = check(data)
        if bad:
            print("[east] 校验失败：")
            for b in bad[:12]:
                print("  -", b)
            return ExitCode.FAIL
        st = data.get("stats") or {}
        print(f"[east] 校验通过：{st.get('tables')} 张表 / {st.get('items')} 个数据项"
              f"（主键项 {st.get('pk_items')}）")
        return ExitCode.OK

    if not a.xlsx:
        print("[east] 需要 --xlsx <数据结构一览表>（或先用 --check 校验已有产物）")
        return ExitCode.ENV
    if not os.path.exists(a.xlsx):
        print(f"[east] 源文件不存在：{a.xlsx}")
        return ExitCode.ENV

    data = parse_xlsx(a.xlsx)
    bad = check(data)
    st = data["stats"]
    print(f"[east] 解析完成：{st['tables']} 张表 / {st['items']} 个数据项"
          f"（扫描 {st['rows_scanned']} 行，跳过非数据项 {st['rows_skipped']} 行）")
    print(f"       按主题域：{st['by_domain']}")
    if bad:
        print("[east][warn] 结构自检问题：")
        for b in bad[:8]:
            print("  -", b)
    if a.dry_run:
        print("[east] dry-run：不落盘")
        return ExitCode.OK
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"[east] 已写入 {a.out}")
    return ExitCode.OK if not bad else ExitCode.FAIL


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
