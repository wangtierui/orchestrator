# -*- coding: utf-8 -*-
"""commands.worklist — orchestrator 命令：worklist（v2 §3.14.3，P1-6）

定位：把链外节点的"决策自动化"缺口（D1–D6）与断点（投放区/配额）从**散落台账**
统一为**可查询待办队列**（`governance.db.worklist`）。本命令是队列的**处置入口**，
不产生待办（产生方为各检测点，见 `config/enums.WORKLIST_KIND` 注释）。

用法：
  orchestrator worklist list [--all] [--kind K] [--limit N] [--json]
  orchestrator worklist resolve <item_id> --resolution "…" [--dismiss]
  orchestrator worklist export [--out PATH]     # 默认 reports/worklist_<date>.md（入库可审计）
  orchestrator worklist stats [--json]

退出码：0=成功；1=用法错误 / 未命中 / 治理库未启用。
"""
from __future__ import annotations

import json as _json


def _api():
    """治理库接口（唯一写/读实现 = std_lib/common_lib/governance_store.py）。"""
    from bootstrap import bootstrap  # noqa: PLC0415
    bootstrap()
    from std_lib.common_lib import governance_store as gs  # noqa: PLC0415
    return gs


def _flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _resolve(argv) -> int:
    if not argv:
        print("用法: orchestrator worklist resolve <item_id> --resolution \"…\" [--dismiss]")
        return 1
    item_id = argv[0]
    res = _flag(argv, "--resolution", "") or ""
    if not res.strip():
        print("[worklist] 需提供 --resolution（处置结论；写回队列供审计）")
        return 1
    gs = _api()
    status = "dismissed" if "--dismiss" in argv else "resolved"
    ok = gs.worklist_resolve(item_id, res, status=status)
    if not ok:
        print(f"[worklist] 未命中或已处置：{item_id}")
        return 1
    print(f"[worklist] {item_id} → {status}：{res}")
    return 0


def _export(argv) -> int:
    import datetime as _dt  # noqa: PLC0415
    import os  # noqa: PLC0415

    import paths  # noqa: PLC0415
    gs = _api()
    rows = gs.worklist_list(status="")
    out = _flag(argv, "--out", "") or os.path.join(
        paths.REPORTS_DIR, f"worklist_{_dt.date.today().strftime('%Y%m%d')}.md")
    lines = ["# 待办队列导出（worklist）", "",
             f"> 生成时间：{gs.now_iso()}｜条目：**{len(rows)}**"
             f"（open {sum(1 for r in rows if r['status'] == 'open')}）",
             "",
             "| item_id | kind | status | subject | suggestion | confidence | round | created_at |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| `{r['item_id']}` | {r['kind']} | {r['status']} | "
            f"{(r['subject'] or '')[:48]} | {(r['suggestion'] or '')[:32]} | "
            f"{'' if r['confidence'] is None else r['confidence']} | "
            f"`{r['round'] or ''}` | {r['created_at']} |")
    lines += ["", "## 处置方法", "",
              "```", "python cli.py worklist resolve <item_id> --resolution \"处置结论\"",
              "```", ""]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[worklist] 已导出 {len(rows)} 条 → {out}")
    return 0


def run(argv):
    gs = _api()
    action = argv[0] if argv else "list"

    if not gs.enabled():
        print("[worklist] 治理库未启用（先 `python cli.py governance init`）")
        return 1

    if action == "list":
        status = "" if "--all" in argv else "open"
        rows = gs.worklist_list(status=status, kind=_flag(argv, "--kind", "") or "",
                                limit=int(_flag(argv, "--limit", 0) or 0))
        if "--json" in argv:
            print(_json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print(f"[worklist] 无待办（status={status or 'all'}）")
            return 0
        print(f"{'item_id':<40}{'kind':<26}{'status':<10}subject")
        for r in rows:
            print(f"{r['item_id']:<40}{r['kind']:<26}{r['status']:<10}{(r['subject'] or '')[:40]}")
        print(f"[worklist] 共 {len(rows)} 条（处置：worklist resolve <item_id> --resolution \"…\"）")
        return 0

    if action == "resolve":
        return _resolve(argv[1:])

    if action == "export":
        return _export(argv[1:])

    if action == "stats":
        st = gs.worklist_stats()
        if "--json" in argv:
            print(_json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(f"[worklist] open={st['open']} 最长滞留={st.get('oldest_open_days', 0)}天 "
                  f"| 已处置={st.get('all', {})}")
            for k, v in (st.get("by_kind") or {}).items():
                print(f"    {k:<28}{v}")
        return 0

    print(f"未知 worklist 子命令: {action}（可用: list, resolve, export, stats）")
    return 1
