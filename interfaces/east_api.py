# -*- coding: utf-8 -*-
"""interfaces.east_api — EAST 数据结构（表 / 数据项）只读门面。

事实源：`data/east/east_structure.json`（由 `tools/build_east_structure.py` 从监管总局
《数据结构一览表（修订版）》解析而来）。

为什么要这一层
--------------
页面不该知道"产物长什么样"之外的任何事：**路径、字段名、筛选语义**都应封装在这里，
否则一旦产物升版（`schema_version` 变化），`web/` 与前端会各自崩一次。
同时它提供**降级形状**（`available=False` + `reason`），产物缺失时页面显示
"未生成，请运行 …"，而不是白屏 —— 与 `external_api` / `relations_api` 同一契约。
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.path.join(ROOT, "data", "east", "east_structure.json")

#: 主题域展示顺序（产物里是 Counter 顺序，页面需要稳定顺序便于对照）
DOMAIN_ORDER = (
    "公共信息", "客户信息", "保险产品信息", "承保信息", "保全信息", "理赔信息",
    "再保信息", "投资业务信息", "财务信息", "关联交易信息", "渠道管理信息",
    "消费者保护信息", "年金业务信息", "受托业务信息", "商业养老金业务信息",
)


def available() -> bool:
    return os.path.exists(DATA_JSON)


def load() -> dict:
    """全量产物（不含降级包装；缺文件抛 FileNotFoundError，由调用方决定怎么降级）。"""
    with open(DATA_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def structure() -> dict:
    """页面用形状：统计 + 表清单（**不含数据项**，避免一次下发 3062 条）。"""
    if not available():
        return {"available": False,
                "reason": "尚未生成 EAST 数据结构产物（运行 "
                          "`python -m tools.build_east_structure --xlsx <一览表>`）",
                "tables": [], "stats": {}}
    try:
        d = load()
    except (OSError, ValueError) as e:
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:200]}",
                "tables": [], "stats": {}}
    tables = [{
        "table_no": t.get("table_no", ""),
        "table_code": t.get("table_code", ""),
        "table_name": t.get("table_name", ""),
        "domain": t.get("domain", ""),
        "table_nature": t.get("table_nature", ""),
        "table_desc": t.get("table_desc", ""),
        "item_count": len(t.get("items") or []),
        "pk_count": sum(1 for i in (t.get("items") or []) if i.get("is_pk") == "Y"),
    } for t in (d.get("tables") or [])]
    order = {n: i for i, n in enumerate(DOMAIN_ORDER)}
    tables.sort(key=lambda x: (order.get(x["domain"], 99), x["table_no"]))
    return {
        "available": True,
        "schema_version": d.get("schema_version", ""),
        "generated_at": d.get("generated_at", ""),
        "source_file": d.get("source_file", ""),
        "source_sha256": (d.get("source_sha256") or "")[:16],
        "stats": d.get("stats") or {},
        "domains": [k for k in DOMAIN_ORDER
                    if k in ((d.get("stats") or {}).get("by_domain") or {})],
        "tables": tables,
        "note": ("数据结构取自监管总局《保险业监管数据标准化规范（人身保险公司2024版）"
                 "数据结构一览表（修订版）》。本页只做**字段级查阅**：不校验报文、"
                 "不生成 EAST 数据。"),
    }


def table_items(table_no: str) -> dict:
    """单表数据项（页面点开某张表时才拉）。"""
    if not table_no:
        return {"available": False, "reason": "缺 table_no", "items": []}
    if not available():
        return {"available": False, "reason": "产物未生成", "items": []}
    try:
        d = load()
    except (OSError, ValueError) as e:
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:200]}",
                "items": []}
    for t in d.get("tables") or []:
        if t.get("table_no") == table_no:
            return {"available": True,
                    "table_no": table_no,
                    "table_code": t.get("table_code", ""),
                    "table_name": t.get("table_name", ""),
                    "domain": t.get("domain", ""),
                    "table_desc": t.get("table_desc", ""),
                    "items": t.get("items") or []}
    return {"available": False, "reason": f"未找到表 {table_no}", "items": []}


if __name__ == "__main__":  # 离线自检
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    s = structure()
    print(json.dumps({k: s[k] for k in ("available", "stats") if k in s},
                     ensure_ascii=False, indent=2)[:600])
    if s.get("tables"):
        first = s["tables"][0]
        print("首表:", first["table_code"], first["table_name"], first["item_count"], "项")
