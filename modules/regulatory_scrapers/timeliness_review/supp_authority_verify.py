# -*- coding: utf-8 -*-
"""
supp_authority_verify.py —— supp 存量权威核验批次补齐（2026-09-08）

背景：supp cleaned 快照中多数记录 timeliness_status 已有值，但 verification_source
为「数据源标注 / 官网原文核验 / 权威媒体 / 本地文件提取 / gov.cn 检索」等**非北大法宝
权威核验**来源（实测 supp_cleaned_20260908 58 行中 16 行非权威）。此类存量 valid 属
"待权威复核"而非"效力缺失"——verify_missing 只处理 timeliness_status 为空的候选，
不覆盖本场景。

本驱动：对 supp cleaned 中「verification_source 不含『北大法宝』」的行做**北大法宝权威
复核批次补齐**：
  - 命中现行/替代/废止/失效 → mark_checked 更新 verification_state（vsource 升级为
    北大法宝权威 + 状态如实更新），并追加 时效核验_supp变更台账_<date>.csv。
  - 无同名命中（judge 规则判断）→ 维持原判定与来源**不动**（不写 state/台账），
    仅打印提示人工核对，防非权威来源被误替换。
  - 降级纪律（R13 对齐）：外部不可用（CLI/Token 缺失/异常）不写任何判定。
风控红线（继承）：workers≤2、间隔≥0.2s、checkpoint 即断点、批量 ≤500。
后续链路（本脚本不代跑）：consolidate_timeliness 收 state+台账 → 全量清单 →
apply_timeliness_to_cleaned --source supp 回写 cleaned 三字段。

用法：
  python supp_authority_verify.py --dry-run             # 只列待权威复核候选
  python supp_authority_verify.py --probe 1             # 冒烟：只核 1 条（验证 CLI/Token）
  python supp_authority_verify.py                       # 全量权威复核批次（断点续跑）
  python supp_authority_verify.py --workers 2 --retry-failed
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
SOURCE = "supp"
# 权威 = verification_source 含「北大法宝」；本驱动只复核非权威存量
AUTHORITY_MARK = "北大法宝"

# R13 三态
S_SUCCESS, S_PARTIAL, S_UNAVAILABLE = "success", "partial", "unavailable"


def should_verify(vsource: str) -> bool:
    """非权威来源（不含『北大法宝』）需权威复核；空来源亦纳入。"""
    return AUTHORITY_MARK not in (vsource or "")


def collect_candidates():
    """supp cleaned 最新快照中 verification_source 非北大法宝权威的行。"""
    idx = get_clean_index()
    p = idx.latest_csv_path(SOURCE)
    if not p or not os.path.exists(p):
        raise FileNotFoundError(f"[{SOURCE}] 无 cleaned 最新快照")
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
            "source": SOURCE,
        })
    return rows, cands


def _summary_record(status, **kw):
    rec = {"source": SOURCE, "status": status,
           "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    rec.update(kw)
    return rec


def run(args) -> dict:
    """supp 权威复核批次（R13 三态摘要返回）。"""
    try:
        rows, cands = collect_candidates()
    except FileNotFoundError as e:
        print(f"[supp_authority] 跳过: {e}")
        return _summary_record(S_UNAVAILABLE, reason=str(e), pending=0)
    print(f"[supp_authority] supp cleaned {len(rows)} 条 | 非权威待复核 {len(cands)} 条")
    if not cands:
        return _summary_record(S_SUCCESS, pending=0, queried=0, ok=0, changed=0,
                               nomatch=0, fail=0, degraded=0)
    if args.dry_run:
        for c in cands:
            print(f"  待权威复核: {c['document_number'] or '(无文号)':<24} | "
                  f"{c['title'][:34]} | {c['timeliness_status'] or '(空)'} | {c['verification_source'][:14]}")
        print("[supp_authority] dry-run：未发起任何北大法宝查询")
        return _summary_record(S_SUCCESS, pending=len(cands), queried=0, ok=0, changed=0,
                               nomatch=0, fail=0, degraded=0, dry_run=True)

    cli = pk.find_cli()
    token = pk.load_token(args.token_file) or pk.load_token(os.path.join(OUT_DIR, ".pkulaw_token"))
    if not token:
        print("[supp_authority] 未找到 Token：请提供 --token-file 或 timeliness_review/.pkulaw_token")
        return _summary_record(S_UNAVAILABLE, reason="no_token", pending=len(cands), queried=0,
                               ok=0, changed=0, nomatch=0, fail=0, degraded=len(cands))

    checkpoint = os.path.join(OUT_DIR, "pkulaw_supp_authority_checkpoint.jsonl")
    plan, cand2item = pk.build_query_plan(cands)
    if args.probe:
        plan = plan[:args.probe]
    executed = {p.get("key", p.get("title")) for p in plan}
    print(f"[supp_authority] 查询计划 {len(plan)} 项（CLI 就绪，workers={args.workers}）")
    done = pk.execute_queries(plan, cli, token, checkpoint, workers=args.workers,
                              retry_failed=args.retry_failed, pause=0.2, log=print)

    today = datetime.datetime.now().strftime("%Y%m%d")
    ledger_out = os.path.join(OUT_DIR, f"时效核验_{SOURCE}变更台账_{today}.csv")
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
            c["verification_note"] = ("北大法宝查询失败: " + str(obj.get("message"))[:120]
                                      if obj and obj.get("message") != "成功"
                                      else "北大法宝 get_law_list 无命中(0条)")
            # 查询失败/无命中：不改判定不改来源（防权威来源被非结果误覆盖）
            print(f"  [维持] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} | 查询失败/无命中")
            continue
        old = c.get("timeliness_status") or ""
        st, rep, vsrc, note = pk.judge_candidate(obj["data"], c)
        if vsrc == "规则判断":
            stats["nomatch"] += 1
            # 无同名命中：维持原数据源标注（不写 state/台账，待人工核对）
            print(f"  [维持] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} "
                  f"| 无同名命中，维持原标注（{old or '空'}）")
            continue
        state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                          status=st, replacement=rep, vsource=vsrc, state=state)
        stats[st if st in stats else "nomatch"] += 1
        changed.append({"title": c.get("title", ""), "document_number": c.get("document_number", ""),
                        "publish_date": c.get("publish_date", ""), "old_status": old, "new_status": st,
                        "replacement_document": rep, "verification_source": vsrc, "note": note, "source": SOURCE})
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
    print(f"[supp_authority] 判定统计: {stats} | 权威确认/变更 {len(changed)} 条 | 台账(追加) {ledger_out}")
    print("[supp_authority] 状态已写入 verification_state.json；cleaned 回写请跑："
          "consolidate_timeliness → apply_timeliness_to_cleaned --source supp")
    failed = stats["fail"] + stats["nomatch"]
    status = S_SUCCESS if failed == 0 else S_PARTIAL
    return _summary_record(status, pending=len(cands), queried=len(executed),
                           ok=len(changed), changed=len(changed), nomatch=stats["nomatch"],
                           fail=stats["fail"], degraded=failed, detail=stats)


def main() -> int:
    ap = argparse.ArgumentParser(description="supp 存量权威核验批次补齐（非北大法宝来源逐条复核）")
    ap.add_argument("--dry-run", action="store_true", help="仅列出候选，不查询")
    ap.add_argument("--probe", type=int, default=0, help="只核前 N 条（冒烟）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（风控 ≤2）")
    ap.add_argument("--retry-failed", action="store_true", help="重试 checkpoint 失败查询")
    ap.add_argument("--token-file", default="", help="北大法宝 token 文件")
    args = ap.parse_args()
    try:
        s = run(args)
    except Exception as e:  # noqa: BLE001 外部异常 → 降级 unavailable（不误标）
        print(f"[supp_authority] 异常降级: {e}")
        s = _summary_record(S_UNAVAILABLE, reason="exception:" + str(e)[:120], pending=0)
    print(f"[supp_authority] supp 权威复核批次 overall: {s['status']} | {s}")
    return {"success": 0, "partial": 2, "unavailable": 3}[s["status"]]


if __name__ == "__main__":
    sys.exit(main())
