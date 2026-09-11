# -*- coding: utf-8 -*-
"""
authority_backfill_verify.py —— 五源「未经北大法宝时效审查」存量权威复核批次（2026-09-08）

核验脚本家族（C-11 统一，2026-09-12）：
  1) verify_missing         —— 效力缺失（timeliness_status 为空）候选；
  2) 本脚本（authority_backfill）—— 存量非权威来源复核（verification_source 不含「北大法宝」，
     含 supp 在内的任意源；原 supp_authority_verify 为其 supp 专化重复已删除，断点同名延续）；
  3) classifier_pkulaw_verify —— classifier 归属表时效状态核验。
  共享工具链：scraper_std.pkulaw_cli + verification_state；**cleaned 回写统一经
  apply_timeliness_to_cleaned.writeback_source（C-12 单点）**，本家族不各自实现写盘。

与 verify_missing（仅效力空候选）互补：对任意源 cleaned 最新快照中
**verification_source 不含「北大法宝」** 的记录（无论效力空/数据源标注/规则判断/
媒体/本地提取等），经北大法宝 CLI 权威复核批次补齐：
  - 命中现行/替代/废止/失效 → mark_checked 更新 verification_state（vsource 升级
    为北大法宝 + 状态如实更新，含 判定变化），并追加 时效核验_{source}变更台账_<date>.csv；
  - 无同名命中（judge 规则判断）→ **写核验痕**（state 记"北大法宝（无同名命中，维持原判定）"、
    状态保持原值）——C-11 统一口径，与 verify_missing/classifier_pkulaw 一致，防下轮重复查询；
  - 降级纪律（R13 对齐）：外部不可用（CLI/Token 缺失/异常）不写任何判定（留待重试）。
风控红线（继承）：workers≤2、间隔≥0.2s、checkpoint 即断点、批量 ≤500、积分用尽(90001)自动停。
后续链路（本脚本不代跑）：consolidate_timeliness --use-state → apply_timeliness_to_cleaned
回写 cleaned 三字段。

用法：
  python authority_backfill_verify.py --source gov --dry-run      # 只列待权威复核候选
  python authority_backfill_verify.py --source nfra --probe 2     # 冒烟：只核 2 条
  python authority_backfill_verify.py --source all                # 全量（断点续跑/配额自动停）
  python authority_backfill_verify.py --source mof,pbc
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys

# 正文等字段可能远超默认 131072 上限，放宽以允许大字段读取
csv.field_size_limit(sys.maxsize)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # modules/regulatory_scrapers/
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "timeliness_review"))
# R4 适配：std_lib 上收 orchestrator 根
_ORCH_ROOT = os.path.dirname(os.path.dirname(ROOT))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
sys.path.insert(0, os.path.join(_ORCH_ROOT, "std_lib"))

import verification_state as vstate  # noqa: E402
from clean_index import get_clean_index  # noqa: E402
from scraper_std import pkulaw_cli as pk  # noqa: E402

OUT_DIR = os.path.join(ROOT, "timeliness_review")
ALL_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")
AUTHORITY_MARK = "北大法宝"

S_SUCCESS, S_PARTIAL, S_UNAVAILABLE = "success", "partial", "unavailable"


def parse_sources(raw: str) -> list[str]:
    if not raw or raw == "all":
        return list(ALL_SOURCES)
    out = []
    for s in raw.replace("，", ",").split(","):
        s = s.strip()
        if s in ALL_SOURCES and s not in out:
            out.append(s)
    return out or list(ALL_SOURCES)


def should_verify(vsource: str) -> bool:
    return AUTHORITY_MARK not in (vsource or "")


def collect_candidates(source: str):
    """某源 cleaned 最新快照中 verification_source 非北大法宝权威的行。"""
    idx = get_clean_index()
    p = idx.latest_csv_path(source)
    if not p or not os.path.exists(p):
        raise FileNotFoundError(f"[{source}] 无 cleaned 最新快照")
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    cands = []
    for r in rows:
        vs = (r.get("verification_source") or "").strip()
        if not should_verify(vs):
            continue
        if not (r.get("document_number") or r.get("title") or "").strip():
            continue
        cands.append({
            "title": (r.get("title") or "").strip(),
            "document_number": (r.get("document_number") or "").strip(),
            "publish_date": (r.get("publish_date") or "").strip(),
            "timeliness_status": (r.get("timeliness_status") or "").strip(),
            "verification_source": vs,
            "source": source,
        })
    return rows, cands


def _summary_record(source, status, **kw):
    rec = {"source": source, "status": status,
           "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    rec.update(kw)
    return rec


def run_source(source: str, args) -> dict:
    """单源权威复核批次（R13 三态摘要返回）。"""
    try:
        rows, cands = collect_candidates(source)
    except FileNotFoundError as e:
        print(f"[authority:{source}] 跳过: {e}")
        return _summary_record(source, S_UNAVAILABLE, reason=str(e), pending=0)
    print(f"[authority:{source}] cleaned {len(rows)} 条 | 非权威待复核 {len(cands)} 条")
    if not cands:
        return _summary_record(source, S_SUCCESS, pending=0, queried=0, ok=0, changed=0,
                               nomatch=0, fail=0, degraded=0)
    if args.dry_run:
        for c in cands[: max(args.show or 8, 1)]:
            print(f"  待权威复核: {c['document_number'] or '(无文号)':<24} | "
                  f"{c['title'][:34]} | {c['timeliness_status'] or '(空)'} | {c['verification_source'][:14]}")
        if len(cands) > (args.show or 8):
            print(f"   … 共 {len(cands)} 条（仅示前 {args.show or 8}）")
        print(f"[authority:{source}] dry-run：未发起任何北大法宝查询")
        return _summary_record(source, S_SUCCESS, pending=len(cands), queried=0, ok=0,
                               changed=0, nomatch=0, fail=0, degraded=0, dry_run=True)

    cli = pk.find_cli()
    token = pk.load_token(args.token_file) or pk.load_token(os.path.join(OUT_DIR, ".pkulaw_token"))
    if not token:
        print("[authority] 未找到 Token：请提供 --token-file 或 timeliness_review/.pkulaw_token")
        return _summary_record(source, S_UNAVAILABLE, reason="no_token", pending=len(cands),
                               queried=0, ok=0, changed=0, nomatch=0, fail=0, degraded=len(cands))

    checkpoint = os.path.join(OUT_DIR, f"pkulaw_{source}_authority_checkpoint.jsonl")
    plan, cand2item = pk.build_query_plan(cands)
    if args.probe:
        plan = plan[:args.probe]
    executed = {p.get("key", p.get("title")) for p in plan}
    print(f"[authority:{source}] 查询计划 {len(plan)} 项（CLI 就绪，workers={args.workers}）")
    done = pk.execute_queries(plan, cli, token, checkpoint, workers=args.workers,
                              retry_failed=args.retry_failed, pause=0.2, log=print)

    today = datetime.datetime.now().strftime("%Y%m%d")
    ledger_out = os.path.join(OUT_DIR, f"时效核验_{source}变更台账_{today}.csv")
    changed = []
    stats = {"valid": 0, "amended": 0, "repealed": 0, "expired": 0, "nomatch": 0, "fail": 0}
    state = vstate.load_state()
    for i, c in enumerate(cands):
        it = cand2item[i]
        if it is None or it.get("key", it.get("title")) not in executed:
            continue
        obj = done.get(it["key"]) or done.get(it["title"])
        if obj is None or obj.get("message") != "成功" or not obj.get("data"):
            stats["fail" if not (obj and obj.get("message") == "成功") else "nomatch"] += 1
            if obj and obj.get("message") == "成功":
                # C-11 统一（2026-09-12）：无命中（0 条）写核验痕（防重复查询；状态保原值）。
                state, _, _ = vstate.mark_checked(
                    docno=c.get("document_number", ""), title=c.get("title", ""),
                    status=c.get("timeliness_status") or "pending", replacement="",
                    vsource="北大法宝（无同名命中，维持原判定）", state=state)
            print(f"  [维持] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} | 查询失败/无命中")
            continue
        old = c.get("timeliness_status") or ""
        st, rep, vsrc, note = pk.judge_candidate(obj["data"], c)
        if vsrc == "规则判断":
            stats["nomatch"] += 1
            # C-11 统一：无同名命中写核验痕（防重复查询；状态保原值）——与 verify_missing 同款。
            state, _, _ = vstate.mark_checked(
                docno=c.get("document_number", ""), title=c.get("title", ""),
                status=c.get("timeliness_status") or "pending", replacement="",
                vsource="北大法宝（无同名命中，维持原判定）", state=state)
            print(f"  [维持] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} "
                  f"| 无同名命中，维持原标注（{old or '空'}）")
            continue
        state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                          status=st, replacement=rep, vsource=vsrc, state=state)
        stats[st if st in stats else "nomatch"] += 1
        changed.append({"title": c.get("title", ""), "document_number": c.get("document_number", ""),
                        "publish_date": c.get("publish_date", ""), "old_status": old, "new_status": st,
                        "replacement_document": rep, "verification_source": vsrc, "note": note, "source": source})
        flag = "权威确认" if st == old else f"{old or '(空)'} → {st}"
        print(f"  [{flag}] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} | {vsrc[:20]}")

    vstate.save_state(state)
    if changed:
        ledger_new = not os.path.exists(ledger_out)
        with open(ledger_out, "a", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["title", "document_number", "publish_date",
                                               "old_status", "new_status", "replacement_document",
                                               "verification_source", "note", "source"])
            if ledger_new:
                w.writeheader()
            w.writerows(changed)
    print(f"[authority:{source}] 判定统计: {stats} | 权威确认/变更 {len(changed)} 条 | 台账(追加) {ledger_out}")
    failed = stats["fail"] + stats["nomatch"]
    status = S_SUCCESS if failed == 0 else S_PARTIAL
    return _summary_record(source, status, pending=len(cands), queried=len(executed),
                           ok=len(changed), changed=len(changed), nomatch=stats["nomatch"],
                           fail=stats["fail"], degraded=failed, detail=stats)


def main() -> int:
    ap = argparse.ArgumentParser(description="五源未北大法宝审查存量权威复核批次")
    ap.add_argument("--source", default="all", help="源: gov/mof/nfra/pbc/supp，逗号分隔或 all")
    ap.add_argument("--dry-run", action="store_true", help="仅列出候选，不查询")
    ap.add_argument("--show", type=int, default=8, help="dry-run 每源预览条数")
    ap.add_argument("--probe", type=int, default=0, help="每源只核前 N 条（冒烟）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（风控 ≤2）")
    ap.add_argument("--retry-failed", action="store_true", help="重试 checkpoint 失败查询")
    ap.add_argument("--token-file", default="", help="北大法宝 token 文件")
    args = ap.parse_args()

    sources = parse_sources(args.source)
    summaries = []
    rc = 0
    for s in sources:
        try:
            summaries.append(run_source(s, args))
        except Exception as e:  # noqa: BLE001 外部异常 → 降级 unavailable（不误标）
            print(f"[authority:{s}] 异常降级: {e}")
            summaries.append(_summary_record(s, S_UNAVAILABLE, reason="exception:" + str(e)[:120],
                                             pending=0))
    for sm in summaries:
        print(f"  {sm['source']:5s} {sm['status']:12s} pending={sm.get('pending', 0):>4} "
              f"ok={sm.get('ok', 0):>4} degraded={sm.get('degraded', 0):>4}")
    # 整体 rc：任一 unavailable→3；否则任一 partial→2；否则 0
    if any(x["status"] == S_UNAVAILABLE for x in summaries):
        rc = 3
    elif any(x["status"] == S_PARTIAL for x in summaries):
        rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
