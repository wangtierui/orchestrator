# -*- coding: utf-8 -*-
"""
classifier_pkulaw_verify.py — 归属表效力查验驱动（复用既有工具链核心，2026-08-27）

用途：对 regulatory_classifier 权威归属表中「未调用北大法宝查验效力」的全部文件，
经北大法宝官方 CLI（零消耗）逐条核验时效，确保三库效力一致
（classifier 归属表 时效状态 ↔ 五源 cleaned 三字段 ↔ verification_state 查验记录）。

复用既有工具链（不重复造轮子）：
  - scraper_std.pkulaw_cli：find_cli / load_token / build_query_plan / execute_queries(断点) / judge_candidate
  - timeliness_review.verification_state：is_fresh(规范① 90日复用) / mark_checked(规范④ 时间戳防死循环) / sync_to_classifier(规范⑤)
判定规则：同名自版本优先（现行有效 ∩ 标题=查询名或"查询名("前缀），排除修改决定/司法解释/实施细则决定。
风控红线：workers≤2（默认2）、间隔≥0.2s、连续15次认证失败自动停止、批量≤500条/批、checkpoint 即断点。

用法：
  python classifier_pkulaw_verify.py --dry-run            # 只计算待查验集合，不查询
  python classifier_pkulaw_verify.py --probe 3            # 冒烟：只查前 3 项（验证 CLI/Token/鉴权）
  python classifier_pkulaw_verify.py                      # 全量查验（断点续跑）
  python classifier_pkulaw_verify.py --workers 2 --retry-failed
  python classifier_pkulaw_verify.py --csv <归属表路径>     # 指定归属表（默认 RFN_REGISTRY_CSV 或权威表）
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # regulatory_scrapers/
sys.path.insert(0, ROOT)                                                # clean_index 包（五源 cleaned 索引事实源）
sys.path.insert(0, os.path.join(ROOT, "std_lib"))
sys.path.insert(0, os.path.join(ROOT, "timeliness_review"))
import verification_state as vstate  # noqa: E402
from clean_index import get_clean_index  # noqa: E402
from scraper_std import pkulaw_cli as pk  # noqa: E402

OUT_DIR = os.path.join(ROOT, "timeliness_review")
CHECKPOINT = os.path.join(OUT_DIR, "classifier_pkulaw_checkpoint.jsonl")
BACKUP_SUBDIR = "backups/classifier_pkulaw_20260827"
CLASSIFIER_CSV = os.environ.get("RFN_REGISTRY_CSV") or os.path.join(
    ROOT, "..", "regulatory_classifier", "data", "人身保险公司-文件归属表.csv")

# 五源 cleaned（动态取 clean_index latest；禁止硬编码快照日期，含 supplementary 经 glob 最新）
_idx = get_clean_index()
FILES = {
    "nfra": _idx.latest_jsonl_path("nfra"),
    "pbc":  _idx.latest_jsonl_path("pbc"),
    "mof":  _idx.latest_jsonl_path("mof"),
    "gov":  _idx.latest_jsonl_path("gov"),
    "supplementary": None,  # glob 最新
}


def _latest_supp_jsonl():
    import glob as _glob
    cand = sorted(_glob.glob(os.path.join(ROOT, "supplementary_regulations_scraper",
                                          "data", "cleaned", "supp_cleaned_*.jsonl")))
    return cand[-1] if cand else None


from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）


def _resolve_replacement(rep, rows):
    """将法宝返回的现行替代标题解析为库内实际标题（归一化括号后比对）。"""
    if not rep:
        return rep
    nr = rep.replace("(", "（").replace(")", "）")
    for r in rows:
        if (r.get("文件名称") or "").replace("(", "（").replace(")", "）") == nr:
            return r["文件名称"]
    return rep


def load_classifier_rows(csv_path):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    for r in rows:
        r["_key"] = vstate.state_key(docno=r.get("发文字号", ""), title=r.get("文件名称", ""))
    return rows


def load_five_source_rows():
    """加载五源 cleaned 行（供三库一致回写）。返回 (src, rows) 列表。"""
    out = {}
    for src, rel in FILES.items():
        if not rel:
            continue
        if src == "supplementary":
            rel = _latest_supp_jsonl()
            if not rel:
                continue
            rel = os.path.relpath(rel, ROOT)
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        rows = []
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
        out[src] = rows
    return out


def build_candidates(classifier_rows, state):
    """规范①：存在北大法宝查验记录且 ≤90 日 → 复用；否则进入待查验集合。"""
    cands, locs = [], []
    skipped = 0
    for i, r in enumerate(classifier_rows):
        if vstate.is_fresh(docno=r.get("发文字号", ""), title=r.get("文件名称", ""),
                           status=r.get("时效状态", "").strip(), state=state):
            skipped += 1
            continue
        cands.append({"title": r.get("文件名称", ""), "document_number": r.get("发文字号", "") or "",
                      "publish_date": r.get("发布日期", "") or "",
                      "timeliness_status": r.get("时效状态", "").strip()})
        locs.append(i)
    return cands, locs, skipped


def main():
    ap = argparse.ArgumentParser(description="归属表未北大法宝查验文件 效力核验（既有工具链）")
    ap.add_argument("--csv", default=CLASSIFIER_CSV, help="归属表路径")
    ap.add_argument("--dry-run", action="store_true", help="只计算待查验集合，不查询")
    ap.add_argument("--probe", type=int, default=0, help="只查前 N 项（冒烟）")
    ap.add_argument("--max", type=int, default=0, help="最多查 N 项（分批控制）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（风控 ≤2）")
    ap.add_argument("--retry-failed", action="store_true", help="重试 checkpoint 中失败的查询")
    ap.add_argument("--token-file", default="", help="北大法宝 token 文件（默认环境变量/OUT/.pkulaw_token）")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print(f"[verify] 归属表不存在: {args.csv}")
        return 1

    state = vstate.load_state()
    rows = load_classifier_rows(args.csv)
    cands, locs, skipped = build_candidates(rows, state)
    print(f"[verify] 归属表 {len(rows)} 条 | 规范① 北大法宝≤90日复用 {skipped} 条 | 待查验 {len(cands)} 条")
    if args.dry_run:
        # 待查验样本（前 10）
        for c in cands[:10]:
            print(f"  待查: {c['document_number'] or '(无文号)'} | {c['title'][:28]} | 现值={c['timeliness_status']!r}")
        print("[verify] dry-run：未发起任何北大法宝查询")
        return 0
    if not cands:
        print("[verify] 无待查验文件，全部已北大法宝核验（≤90日）")
        return 0

    cli = pk.find_cli()
    token = pk.load_token(args.token_file)
    if not token:
        token = pk.load_token(os.path.join(OUT_DIR, ".pkulaw_token"))
    if not token:
        print("[verify] 未找到 Token：请设置 PKULAW_TOKEN 环境变量或提供 --token-file")
        return 1

    plan, cand2item = pk.build_query_plan(cands)
    if args.probe:
        plan = plan[:args.probe]
    if args.max:
        plan = plan[:args.max]
    executed = {p.get("key", p.get("title")) for p in plan}
    print(f"[verify] 查询计划 {len(plan)} 项（CLI 就绪，workers={args.workers}，checkpoint={os.path.basename(CHECKPOINT)}）")
    done = pk.execute_queries(plan, cli, token, CHECKPOINT, workers=args.workers,
                              retry_failed=args.retry_failed, pause=0.2, log=print)

    today = datetime.datetime.now().strftime("%Y%m%d")
    ledger_out = os.path.join(OUT_DIR, "时效核验_classifier变更台账_%s.csv" % today)
    changed = []
    stats = {"valid": 0, "amended": 0, "repealed": 0, "expired": 0, "nomatch": 0, "fail": 0}
    for i, c in enumerate(cands):
        it = cand2item[i]
        if it is None or it.get("key", it.get("title")) not in executed:
            continue  # 本批计划未覆盖（--max/--probe 截断或重复标题合并），留待下一批（checkpoint 续跑）
        obj = done.get(it["key"]) or done.get(it["title"])
        if obj is None or obj.get("message") != "成功" or not obj.get("data"):
            stats["nomatch" if obj and obj.get("message") == "成功" else "fail"] += 1
            if obj and obj.get("message") != "成功":
                c["verification_note"] = "北大法宝查询失败: " + str(obj.get("message"))[:120]
            else:
                c["verification_note"] = "北大法宝 get_law_list 无命中(0条)"
                # C-11 语义统一（2026-09-12）：无命中（0 条）与 verify_missing 同款——
                # 写核验痕（防下轮重复查询/状态透明）；"查询失败"分支仍不写（留待重试续跑）。
                state, _, _ = vstate.mark_checked(
                    docno=c.get("document_number", ""), title=c.get("title", ""),
                    status=c.get("timeliness_status") or "pending", replacement="",
                    vsource="北大法宝（无同名命中，维持原判定）", state=state)
            continue
        old = rows[locs[i]].get("时效状态", "").strip()
        st, rep, vsrc, note = pk.judge_candidate(obj["data"], c)
        if vsrc == "规则判断":
            # 北大法宝返回记录但无同名命中/未确认 → 如实标记「已核验·无命中·维持原判定」，
            # 不覆盖为 规则判断 来源（避免 is_fresh 失效与记录污染）
            stats["nomatch"] += 1
            cur_st = c.get("timeliness_status") or old or "pending"
            state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                              status=cur_st, replacement=rep or c.get("replacement_document", ""),
                                              vsource="北大法宝（无同名命中，维持原判定）", state=state)
            c["verification_note"] = note
            continue
        rep = _resolve_replacement(rep, rows)
        # 规范④：写入查验记录（时间戳 + 效力状态，防死循环）
        state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                          status=st, replacement=rep, vsource=vsrc, state=state)
        stats[st if st in stats else "nomatch"] += 1
        if st != old:
            changed.append({"title": c.get("title", ""), "document_number": c.get("document_number", ""),
                            "publish_date": c.get("publish_date", ""), "old_status": old, "new_status": st,
                            "replacement_document": rep, "note": note})
            print(f"  [变更] {c['document_number'] or '(无文号)'} | {c['title'][:24]} | {old or '(空)'} → {st} | {vsrc[:16]}")

    vstate.save_state(state)
    os.makedirs(os.path.join(ROOT, BACKUP_SUBDIR), exist_ok=True)

    # 规范⑤：变更 → 同步 classifier 归属表"时效状态"列
    if changed:
        m, um, cpath = vstate.sync_to_classifier(changed, dry_run=False)
        print(f"[verify] 规范⑤ 归属表时效状态同步：匹配 {m} / 未匹配 {um}（{cpath}）")
    else:
        print("[verify] 本次无效力变更（0 条），归属表时效状态不变")

    # 三库一致：同步五源 cleaned 三字段（C-12：经统一回写单点 writeback_source 逐源回写，
    # 原本地实现无原子/无 CSV 轨/备份口径不同——三份分叉已收敛）
    sys.path.insert(0, OUT_DIR)
    import apply_timeliness_to_cleaned as apply_mod  # noqa: PLC0415  统一回写单点（C-12）
    updates = {}
    for ch in changed:
        nd = _norm_docno(ch["document_number"])
        if len(nd) < 5:
            continue
        updates[nd] = {
            "timeliness_status": ch["new_status"],
            "replacement_document": ch.get("replacement_document") or "",
            "verification_source": "北大法宝",
        }

    def fields_for(r):
        return updates.get(_norm_docno(r.get("document_number", "")))

    synced = 0
    for src_id in ("gov", "mof", "nfra", "pbc", "supp"):
        st = apply_mod.writeback_source(src_id, fields_for, dry_run=False, backup_tag="classifier_pkulaw")
        if "reason" in st:
            continue
        synced += st.get("written", 0)
    print(f"[verify] 五源 cleaned 三字段同步 {synced} 处（统一单点，jsonl+csv 双轨）")

    # 台账：追加模式（跨批/跨天续跑不覆盖历史变更）
    ledger_new = not os.path.exists(ledger_out)
    with open(ledger_out, "a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["title", "document_number", "publish_date",
                                           "old_status", "new_status", "replacement_document", "note"])
        if ledger_new:
            w.writeheader()
        w.writerows(changed)
    print(f"[verify] 判定统计: {stats} | 本批变更 {len(changed)} 条 | 台账(追加) {ledger_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
