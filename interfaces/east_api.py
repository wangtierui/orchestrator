# -*- coding: utf-8 -*-
"""interfaces.east_api — EAST 规范**全附件**只读门面（v2，2026-10-26）。

事实源（四件产物 + 采集事实源）：

| 文件 | 内容 | 页面何时读 |
|---|---|---|
| `data/east/east_structure.json` | 通知/附件清单/统计/表索引/代码表索引/规则索引/**E-R 图**/正文摘要 | 首屏 |
| `data/east/east_items.json` | 每张表的**数据项**全量 | 点开某张表 |
| `data/east/east_rules.json` | 检核规则 A~E 全量 | 切到「检核规则」 |
| `data/east/east_codes.json` | 业务代码表全量（63 张 / 9543 行） | 切到「代码表」 |

为什么拆四件：全塞一个 JSON 实测 **9.8MB**，首屏要等它下完 —— 而首屏只需要"有哪些表、
多少项、E-R 什么样"。拆分后首屏 423KB。

附件清单与正文**不复制进产物**：它们已在采集事实源里（`interfaces.attachment_api`），
本模块的 `attachments()` 直接转调，避免同一份字节/正文在仓里存两套。
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "east")
S_JSON = os.path.join(DATA_DIR, "east_structure.json")
I_JSON = os.path.join(DATA_DIR, "east_items.json")
R_JSON = os.path.join(DATA_DIR, "east_rules.json")
C_JSON = os.path.join(DATA_DIR, "east_codes.json")
DOCNO = "金非银检函〔2025〕82号"

#: 主题域展示顺序（产物里是 Counter 顺序，页面需要稳定顺序便于对照）
DOMAIN_ORDER = (
    "公共信息", "客户信息", "保险产品信息", "承保信息", "保全信息", "理赔信息",
    "再保信息", "投资业务信息", "财务信息", "关联交易信息", "渠道管理信息",
    "消费者保护信息", "年金业务信息", "受托业务信息", "商业养老金业务信息",
)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def available() -> bool:
    return os.path.exists(S_JSON)


def _degrade(reason: str, **extra) -> dict:
    return {"available": False, "reason": reason, **extra}


# ---------------------------------------------------------------- 结构（首屏）
def structure() -> dict:
    """首屏：通知、附件清单、统计、表索引、代码表索引、规则索引、E-R、正文摘要。"""
    if not available():
        return _degrade("尚未生成 EAST 产物（运行 `python -m tools.build_east_structure`）")
    try:
        d = _load(S_JSON)
    except (OSError, ValueError) as e:
        return _degrade(f"{type(e).__name__}: {str(e)[:200]}")
    tables = d.get("tables") or []
    order = {n: i for i, n in enumerate(DOMAIN_ORDER)}
    tables.sort(key=lambda x: (order.get(x.get("domain", ""), 99), x.get("table_no", "")))
    out = {
        "available": True,
        "schema_version": d.get("schema_version", ""),
        "generated_at": d.get("generated_at", ""),
        "notice": d.get("notice") or {},
        "source": d.get("source") or {},
        "stats": d.get("stats") or {},
        "format_legend": d.get("format_legend") or {},
        "elements": d.get("elements") or [],
        "tables": tables,
        "code_index": d.get("code_index") or [],
        "rule_index": d.get("rule_index") or {},
        "er": d.get("er") or {"nodes": [], "edges": [], "stats": {}},
        "docs": d.get("docs") or [],
        "sheets": d.get("sheets") or [],
        "domains": [k for k in DOMAIN_ORDER
                    if k in ((d.get("stats") or {}).get("by_domain") or {})],
        "note": ("结构取自《保险业监管数据标准化规范（人身保险公司2024版）》"
                 "2025-04-10 修订版**全部附件**（1/1-1/1-2/1-3/1-4/1-5/1-6）＋表单类附件。"
                 "本页只做**查阅**：不校验报文、不生成 EAST 数据。"),
    }
    return out


# ---------------------------------------------------------------- 数据项（按表）
def table_items(table_no: str) -> dict:
    if not table_no:
        return _degrade("缺 table_no", items=[])
    if not os.path.exists(I_JSON):
        return _degrade("数据项产物缺失（east_items.json）", items=[])
    try:
        d = _load(I_JSON)
    except (OSError, ValueError) as e:
        return _degrade(f"{type(e).__name__}: {str(e)[:200]}", items=[])
    items = (d.get("tables") or {}).get(table_no)
    if items is None:
        return _degrade(f"未找到表 {table_no}", items=[])
    meta = {}
    try:
        for t in _load(S_JSON).get("tables") or []:
            if t.get("table_no") == table_no:
                meta = t
                break
    except (OSError, ValueError):
        meta = {}      # 表元数据只是附赠信息；读不到就不带（不吞掉数据项本身）
    return {"available": True, "table_no": table_no, "meta": meta, "items": items}


# ---------------------------------------------------------------- 规则（按类）
def rules(kind: str = "") -> dict:
    """检核规则：`kind` 为空 → 返回五类索引；否则返回该类全量条目。"""
    if not os.path.exists(R_JSON):
        return _degrade("规则产物缺失（east_rules.json）", items=[])
    try:
        d = _load(R_JSON)
    except (OSError, ValueError) as e:
        return _degrade(f"{type(e).__name__}: {str(e)[:200]}", items=[])
    kinds = d.get("kinds") or {}
    if not kind:
        return {"available": True,
                "kinds": [{"kind": k, "label": v.get("label", ""),
                           "count": v.get("count", 0)} for k, v in kinds.items()]}
    v = kinds.get(kind)
    if not v:
        return _degrade(f"未找到规则类别 {kind}", items=[])
    return {"available": True, "kind": kind, "label": v.get("label", ""),
            "count": v.get("count", 0), "items": v.get("items") or []}


# ---------------------------------------------------------------- 代码表
def codes() -> dict:
    if not os.path.exists(C_JSON):
        return _degrade("代码表产物缺失（east_codes.json）", tables=[])
    try:
        d = _load(C_JSON)
    except (OSError, ValueError) as e:
        return _degrade(f"{type(e).__name__}: {str(e)[:200]}", tables=[])
    return {"available": True, "count": d.get("count", 0),
            "tables": [{"sheet": t.get("sheet"), "id": t.get("id"),
                        "name": t.get("name"), "count": t.get("count"),
                        "columns": t.get("columns"), "normalized": t.get("normalized")}
                       for t in d.get("tables") or []]}


def code_table(sheet: str) -> dict:
    if not sheet:
        return _degrade("缺 sheet", rows=[])
    if not os.path.exists(C_JSON):
        return _degrade("代码表产物缺失", rows=[])
    try:
        d = _load(C_JSON)
    except (OSError, ValueError) as e:
        return _degrade(f"{type(e).__name__}: {str(e)[:200]}", rows=[])
    for t in d.get("tables") or []:
        if t.get("sheet") == sheet:
            return {"available": True, **t}
    return _degrade(f"未找到代码表 {sheet}", rows=[])


# ---------------------------------------------------------------- 附件清单
def attachments(docno: str = DOCNO) -> dict:
    """附件清单（转调采集事实源，**不复制**）。"""
    from interfaces import attachment_api

    d = attachment_api.attachments_for_docno(docno)
    if not d.get("available"):
        return _degrade(d.get("reason") or "附件清单不可用", items=[])
    return {"available": True, **d}


def er() -> dict:
    """E-R 图（结构件内的 `er` 分区）。"""
    if not available():
        return _degrade("尚未生成 EAST 产物", nodes=[], edges=[])
    try:
        e = _load(S_JSON).get("er") or {}
    except (OSError, ValueError) as exc:
        return _degrade(f"{type(exc).__name__}: {str(exc)[:200]}", nodes=[], edges=[])
    return {"available": True, "nodes": e.get("nodes") or [],
            "edges": e.get("edges") or [], "stats": e.get("stats") or {},
            "note": e.get("note", "")}


if __name__ == "__main__":  # 离线自检
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    s = structure()
    print(json.dumps({"available": s.get("available"), "stats": s.get("stats")},
                     ensure_ascii=False, indent=1)[:800])
    print("附件:", attachments().get("count"),
          "| 规则类:", [k["kind"] for k in rules().get("kinds", [])],
          "| 代码表:", codes().get("count"),
          "| E-R:", er().get("stats"))
