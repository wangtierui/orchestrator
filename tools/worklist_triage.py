# -*- coding: utf-8 -*-
"""tools.worklist_triage — 待办**分批裁决**辅助（治理决策 2026-10-16：641 条 open 分批裁决）

为什么需要它（实测依据，见 `docs/tech_debt.md` 与本模块 docstring 的批次表）：
待办积压的真实结构不是「641 条同类」，而是**七种裁决动作类型**——其中两种可由机器
判定并安全处置，其余需要业务判断。若按数量平均切批，会把可自动的与需人工的混在一批，
要么错放（机器替人做业务判断），要么漏放（人工去处置机器能判的）。

安全设计（三条硬约束，缺一不可）
--------------------------------
1. **默认只读**：`scan` / `bundle` 永不写库；`apply` 必须显式 `--execute` 才落盘。
2. **机器只判「机器能判的」**：每条自动处置都必须满足 `PREDICATES[kind]` 的谓词，
   谓词依据写在代码注释里（可复核）；不满足即入「人工批次」，工具**只出建议不处置**。
3. **人工闸门**：执行必须带 `--by`（处置人），逐条经 `governance_store.worklist_resolve`
   ⇒ 自动落审计（判据 C1），处置依据（`--reason`）入库可追溯。

用法
----
    python -m tools.worklist_triage scan                      # 分批总览（只读）
    python -m tools.worklist_triage bundle --kind trigger_manual_breakpoint
    python -m tools.worklist_triage apply --batch B1 --execute --by alice --reason "…"
"""
from __future__ import annotations

import json
import os

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

# --------------------------------------------------------------------------- #
# 批次定义：按「裁决动作类型」而非数量切分
#
# 实测依据（2026-10-16，641 条 open）：
#   rfn_theme_uncertain 324（近7天新增 0 ⇒ 已停产）—— payload 只有
#       {decision, reason:"无票/并列/形态不明", title}，**无候选主题与票数**；
#       且标题在归属表 1162 个名称中命中 0 条 ⇒ 这些文件尚未进入 RFN 登记体系，
#       主题判定需业务知识 ⇒ **不可机器判定**。
#   relevance_boundary 211（近 7 天新增 211 ⇒ 产生速率 > 处置速率）：
#       208 条明细（带 hits/snippet ⇒ 证据齐全，需人工判定 INCLUDE/EXCLUDE）
#       + 3 条汇总（subject=summary:N，只带总数，明细在 relevance/*.jsonl
#         ⇒ **无可裁决对象**，属每轮重复登记的不可操作条目）。
#   trigger_manual_breakpoint 23：历史批次遗留的**技术决策待办**，其中多条 payload
#       自证闭环（"已修复/已处置/已修"）⇒ 机器可据此关单，但**是否真的闭环需人确认**
#       （本工具默认仍归人工批次，只在 `--include-self-closed` 时才纳入自动批）。
#   internal_unaligned 66 / rfn_clean_drift_c2 12 / timeliness_conflict 3 /
#   clean_quarantine_triage 2：均已停产，数量小，走人工批次。
# --------------------------------------------------------------------------- #
BATCHES: tuple[dict, ...] = (
    {
        "batch_id": "B1",
        "kind": "trigger_manual_breakpoint",
        "title": "历史技术决策待办（多条 payload 自证闭环）",
        "action_type": "self_closed_or_pending",
        "auto_rule": "payload 含闭环标记词（已修复/已处置/已修/已闭环）",
        "auto_action": "resolved",   # 受控域 = resolved|dismissed
        "needs_human": True,
        "note": "闭环自证来自 payload 文本，属**弱证据**；默认只出建议，"
                "加 --include-self-closed 才纳入自动执行",
    },
    {
        "batch_id": "B2",
        "kind": "relevance_boundary",
        "title": "BOUNDARY 汇总条目（无裁决对象）",
        "action_type": "machine_determinable",
        "auto_rule": "subject 以 `summary:` 开头（只带 total/registered，明细在 jsonl）",
        "auto_action": "dismissed",   # 受控域 = resolved|dismissed（写口校验，写 dismiss 会 ValueError）
        "needs_human": False,
        "note": "明细事实源 = modules/regulatory_classifier/relevance/*.jsonl（保留不动）",
    },
    {
        "batch_id": "B3",
        "kind": "relevance_boundary",
        "title": "BOUNDARY 明细条目（带 hits/snippet，需判定 INCLUDE/EXCLUDE）",
        "action_type": "business_judgement",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "证据齐全（hits/snippet/confidence）；产出裁决包供批量确认",
    },
    {
        "batch_id": "B4",
        "kind": "rfn_theme_uncertain",
        "title": "主题不确定（无票/并列/形态不明）",
        "action_type": "business_judgement",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "标题在归属表中命中 0 ⇒ 无法机器判定主题；需业务裁决或先判断是否纳入体系",
    },
    {
        "batch_id": "B5",
        "kind": "internal_unaligned",
        "title": "内部制度无对应监管主题",
        "action_type": "business_judgement",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "处置二选一：补 THEME_TITLE_KW 关键词，或确认确无对应主题后 dismiss",
    },
    {
        "batch_id": "B6",
        "kind": "rfn_clean_drift_c2",
        "title": "clean 漂移待人工（supersede）",
        "action_type": "business_judgement",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "",
    },
    {
        "batch_id": "B7",
        "kind": "timeliness_conflict",
        "title": "时效状态冲突（needs_review）",
        "action_type": "business_judgement",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "",
    },
    {
        "batch_id": "B8",
        "kind": "clean_quarantine_triage",
        "title": "清洗隔离记录（已有 triage 工具）",
        "action_type": "delegated",
        "auto_rule": None,
        "auto_action": None,
        "needs_human": True,
        "note": "处置入口 = python -m tools.quarantine_triage（本工具不代劳）",
    },
)

#: 自证闭环的标记词（弱证据；仅在 --include-self-closed 时启用）
SELF_CLOSED_HINTS = ("已修复", "已处置", "已闭环", "已落地", "（已修）", "已修：")


def _is_summary_item(subject: str) -> bool:
    """汇总条目判定：subject 形如 `summary:588`（B2 的机器判据）。"""
    return str(subject or "").startswith("summary:")


def _has_self_closed(row: dict) -> bool:
    """自证闭环判定：subject + payload 文本含闭环标记词（B1 的机器判据，弱证据）。

    ⚠️ 标记词**必须同时扫 subject 与 payload**：实测多条自证闭环写在 **subject**
    （如 `N-208 已修复并完成历史数据补救`、`处置留痕被同名覆盖（已修：…）`），
    只扫 payload 会漏判（假阴性）。
    """
    blob = str(row.get("subject") or "")
    try:
        blob += json.dumps(row.get("payload") or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        blob += str(row.get("payload"))
    return any(h in blob for h in SELF_CLOSED_HINTS)


def _classify(kind: str, row: dict) -> str:
    """单条归类 ⇒ `auto` / `human`。**按 kind 判谓词**（同一 kind 的多个批次共享谓词集）。

    缺陷修正（2026-10-16）：原实现按 `batch["batch_id"]` 分支，导致 B3 也走
    "恒 human"分支 ⇒ 汇总项被重复计入 B3（分批总数 214 > 实际 211）。
    """
    if kind == "relevance_boundary":
        return "auto" if _is_summary_item(str(row.get("subject") or "")) else "human"
    if kind == "trigger_manual_breakpoint":
        return "auto" if _has_self_closed(row) else "human"
    return "human"


def _load_open() -> list[dict]:
    from std_lib.common_lib import governance_store as gs

    return gs.worklist_list(status="open", limit=0)


#: 批次 → 该批次只收哪一类（`auto` 批 / `human` 批；None = 该 kind 全部归此批）
#:
#: ⚠️ B1 收**全部** `trigger_manual_breakpoint`（None），其中仅「自证闭环」子集可机器执行。
#: 早期版本令 B1 只收 auto 类 ⇒ 该 kind 另有 18 条非自证项无处归批（覆盖 623/641）。
#: 修正后的口径：**批次是归口单位（覆盖必须闭合），机器可执行范围是批内子集**——
#: 两者混为一谈会漏归口，而漏归口的条目将永远不出现在任何裁决包里。
_BATCH_TAKE: dict[str, str | None] = {
    "B1": None,           # trigger_manual_breakpoint 全部（auto 子集才可执行）
    "B2": "auto",          # relevance_boundary 的汇总子集
    "B3": "human",         # relevance_boundary 的明细子集
}


def _rows_of(rows: list[dict], kind: str, batch_id: str) -> list[dict]:
    """取某批次负责的行：按 kind 判谓词，再按批次声明收哪一类。"""
    want = _BATCH_TAKE.get(batch_id)
    out = []
    for r in rows:
        if str(r.get("kind")) != kind:
            continue
        cls = _classify(kind, r)
        if want is not None and cls != want:
            continue
        out.append({**r, "_class": cls})
    return out


def cmd_scan() -> int:
    """分批总览（只读）。"""
    rows = _load_open()
    total = len(rows)
    print(f"[scan] open 合计 = {total}；分 {len(BATCHES)} 批（按裁决动作类型，非数量）")
    print(f"{'批次':4s} {'kind':30s} {'总数':>5s} {'机器可判':>8s} {'需人工':>6s}  说明")
    auto_total = 0
    for b in BATCHES:
        rs = _rows_of(rows, b["kind"], b["batch_id"])
        auto = [r for r in rs if r["_class"] == "auto"]
        human = [r for r in rs if r["_class"] == "human"]
        auto_total += len(auto)
        print(f"{b['batch_id']:4s} {b['kind']:30s} {len(rs):5d} {len(auto):8d} {len(human):6d}  "
              f"{b['title'][:34]}")
    covered = sum(len(_rows_of(rows, b['kind'], b['batch_id'])) for b in BATCHES)
    print(f"\n[scan] 覆盖 = {covered}/{total}"
          f"{'（有未归批 kind：' + str(sorted({r['kind'] for r in rows} - {b['kind'] for b in BATCHES})) + '）' if covered < total else ''}")
    print(f"[scan] 机器可判合计 = {auto_total}（默认仍需 --execute + --by 才落盘）")
    return ExitCode.OK


def cmd_bundle(kind: str, out_dir: str = "") -> int:
    """导出裁决包（只读）：逐条含证据摘要 + 建议动作 + 批次依据。"""
    rows = _load_open()
    batches = [b for b in BATCHES if b["kind"] == kind]
    if not batches:
        print(f"[bundle] 未知 kind={kind}；可选：{sorted({b['kind'] for b in BATCHES})}")
        return ExitCode.USAGE
    out_dir = out_dir or os.path.join(paths.REPORTS_DIR, "worklist_batches")
    os.makedirs(out_dir, exist_ok=True)
    stamp = _now()
    payload: dict = {"generated_at": stamp, "kind": kind, "batches": []}
    md = [f"# 待办裁决包 · {kind}", "", f"> 生成：{stamp}｜工具：tools/worklist_triage（只读）", ""]
    for b in batches:
        rs = _rows_of(rows, b["kind"], b["batch_id"])
        auto = [r for r in rs if r["_class"] == "auto"]
        md.append(f"## {b['batch_id']} · {b['title']}")
        md.append(f"- 条数：{len(rs)}（机器可判 {len(auto)} / 需人工 {len(rs) - len(auto)}）")
        if b["auto_rule"]:
            md.append(f"- 机器判据：`{b['auto_rule']}`")
        if b["note"]:
            md.append(f"- 说明：{b['note']}")
        md.append("")
        md.append("| item_id | subject | 建议动作 | 依据摘要 |")
        md.append("|---|---|---|---|")
        for r in rs:
            pl = r.get("payload") or {}
            brief = json.dumps(pl, ensure_ascii=False)[:120]
            act = "resolve" if r["_class"] == "auto" else "待裁决"
            md.append(f"| {r['item_id']} | {str(r.get('subject'))[:40]} | {act} | {brief} |")
        payload["batches"].append({"batch_id": b["batch_id"], "title": b["title"],
                                    "count": len(rs), "auto": len(auto),
                                    "items": [{"item_id": r["item_id"],
                                               "subject": r.get("subject"),
                                               "class": r["_class"],
                                               "payload": r.get("payload")} for r in rs]})
        md.append("")
    jf = os.path.join(out_dir, f"{kind}_bundle_{stamp.replace(':', '').replace('+', '_')}.json")
    mf = jf[:-5] + ".md"
    with open(jf, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    with open(mf, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(md))
    print(f"[bundle] kind={kind} → {_display(mf)}（+ .json）")
    return ExitCode.OK


def cmd_apply(batch_id: str, execute: bool, by: str = "", reason: str = "",
              include_self_closed: bool = False) -> int:
    """受控执行：只处置**机器可判**且谓词成立的条目；默认 dry-run。"""
    b = next((x for x in BATCHES if x["batch_id"] == batch_id), None)
    if b is None:
        print(f"[apply] 未知批次 {batch_id}；可选 {[x['batch_id'] for x in BATCHES]}")
        return ExitCode.USAGE
    if b["batch_id"] == "B1" and not include_self_closed:
        print("[apply] B1（自证闭环）属**弱证据**，须显式加 --include-self-closed 才执行；"
              "否则请用 bundle 产出建议清单后人工确认。")
        return ExitCode.OK
    if not b["auto_rule"] or not b["auto_action"]:
        print(f"[apply] 批次 {batch_id} 无机器判据（需业务判断），本工具不代劳；"
              f"请用 bundle 导出裁决包。")
        return ExitCode.OK
    rows = _rows_of(_load_open(), b["kind"], b["batch_id"])
    auto = [r for r in rows if r["_class"] == "auto"]
    print(f"[apply] 批次 {batch_id}（{b['kind']}）：机器可判 {len(auto)}/{len(rows)} 条")
    for r in auto:
        print(f"    {'[dry] ' if not execute else '[exec]'} {r['item_id']} "
              f"{str(r.get('subject'))[:40]}")
    if not execute:
        print("[apply] dry-run：未落库（加 --execute 才实际处置）")
        return ExitCode.OK
    if not by:
        print("[apply] 缺 --by（处置人必填——审计与台账需要处置人）")
        return ExitCode.USAGE
    if not reason:
        print("[apply] 缺 --reason（处置依据必填——无依据的处置不可审计）")
        return ExitCode.USAGE
    from std_lib.common_lib import governance_store as gs

    ok = 0
    for r in auto:
        try:
            if gs.worklist_resolve(r["item_id"], reason, by=by,
                                   status=b["auto_action"]):
                ok += 1
        except Exception as e:  # noqa: BLE001 单条失败不中断批次
            print(f"[apply] 失败 {r['item_id']}：{type(e).__name__}: {e}")
    print(f"[apply] 已处置 {ok}/{len(auto)} 条（逐条落 audit_log，判据 C1）")
    return ExitCode.OK


def _display(path: str) -> str:
    """相对仓根展示；**跨盘符时退回绝对路径**（`os.path.relpath` 在 C:→D: 会 ValueError，
    而裁决包允许写到仓外目录，如测试的 tmp_path）。"""
    try:
        return os.path.relpath(path, paths.ROOT)
    except ValueError:
        return path


def _now() -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="待办分批裁决辅助（默认只读；执行需 --execute + --by）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="分批总览（只读）")
    p_b = sub.add_parser("bundle", help="导出裁决包（只读）")
    p_b.add_argument("--kind", required=True)
    p_b.add_argument("--out-dir", default="")
    p_a = sub.add_parser("apply", help="受控执行（机器可判批次）")
    p_a.add_argument("--batch", required=True)
    p_a.add_argument("--execute", action="store_true", help="真正落库（默认 dry-run）")
    p_a.add_argument("--by", default="", help="处置人（必填，执行时）")
    p_a.add_argument("--reason", default="", help="处置依据（必填，执行时）")
    p_a.add_argument("--include-self-closed", action="store_true",
                     help="B1：纳入 payload 自证闭环的条目（弱证据，默认不纳入）")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        return cmd_scan()
    if a.cmd == "bundle":
        return cmd_bundle(a.kind, a.out_dir)
    return cmd_apply(a.batch, a.execute, a.by, a.reason, a.include_self_closed)


if __name__ == "__main__":
    raise SystemExit(main())
