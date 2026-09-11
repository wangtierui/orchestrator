# -*- coding: utf-8 -*-
"""
verify_missing.py —— 四源效力缺失记录定向核验（聚焦驱动，2026-09-07 泛化版）

核验脚本家族（C-11 统一，2026-09-12）：
  1) 本脚本 —— 效力缺失（timeliness_status 为空）候选（--source all 含 supp）；
  2) authority_backfill_verify —— 存量非权威来源复核（原 supp_authority_verify 已并入其 --source supp）；
  3) classifier_pkulaw_verify —— classifier 归属表时效状态核验。
  共享工具链：scraper_std.pkulaw_cli + verification_state；**cleaned 回写统一经
  apply_timeliness_to_cleaned.writeback_source（C-12 单点）**；无命中统一"写核验痕"。

背景：各源 cleaned 最新快照中 `timeliness_status` 为空的"效力缺失"记录需要经
北大法宝官方 CLI（零 LLM 消耗）核验。通用 `verify_source_pkulaw.py` 按
verification_state 复用判定会扫出大量候选、触发非必要查询，违反风控红线。

本驱动聚焦：仅选取指定源 cleaned 中 `timeliness_status` 为空的记录 → 经北大法宝
CLI 逐条核验 → 更新 verification_state.json + 追加到
`时效核验_{源}变更台账_{date}.csv`（供 consolidate/backfill 管线消费）。

泛化：--source 支持 nfra/mof/pbc/gov；--source all 顺序处理四源（各源独立
checkpoint / 独立台账，互不干扰，可分别断点续跑）。

复用既有工具链（与 verify_source_pkulaw.py 同构）：
  - scraper_std.pkulaw_cli：find_cli / load_token / build_query_plan / execute_queries / judge_candidate
  - timeliness_review.verification_state：is_fresh / mark_checked（规范④ 时间戳防死循环）
风控红线：workers≤2、间隔≥0.2s、连续15次认证失败自动停止、checkpoint 即断点、分批 ≤500。
北大法宝配额：积分用尽(90001) 自动停止新增查询，断点续跑即可。

用法：
  python verify_missing.py --source nfra --dry-run   # 仅列出效力缺失候选
  python verify_missing.py --source gov  --probe 1   # 冒烟：只查 1 条
  python verify_missing.py --source all              # 四源全量核验（断点续跑）
  python verify_missing.py --source mof              # 单源全量核验
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys

# 正文等字段可能远超默认 131072 上限（gov cleaned 实测），放宽以允许大字段读取
csv.field_size_limit(sys.maxsize)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # modules/regulatory_scrapers/
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "timeliness_review"))
# R4 适配：std_lib 上收 orchestrator 根（共享库单副本，scrapers/std_lib 旧仓路径已失效）
_ORCH_ROOT = os.path.dirname(os.path.dirname(ROOT))
if _ORCH_ROOT not in sys.path:
    sys.path.insert(0, _ORCH_ROOT)
sys.path.insert(0, os.path.join(_ORCH_ROOT, "std_lib"))

import verification_state as vstate  # noqa: E402
from clean_index import get_clean_index  # noqa: E402
from scraper_std import pkulaw_cli as pk  # noqa: E402

OUT_DIR = os.path.join(ROOT, "timeliness_review")
# P2（2026-09-08）：supp 纳入效力缺失核验范围（原四源）。supp cleaned 清洗后空时效
# 记录进北大法宝核验；无同名命中自动登记 pending 占位（不污染），行业文本记录可安心跳过。
ALL_SOURCES = ["gov", "mof", "pbc", "nfra", "supp"]     # supp 置末（补充库，通常空时效待核少）

# R13 三态（2026-09-08）：success 全部核验完成 / partial 部分完成可续跑 / unavailable 外部不可用降级
S_SUCCESS, S_PARTIAL, S_UNAVAILABLE = "success", "partial", "unavailable"


def collect_missing(source: str):
    """选取 source cleaned 最新快照中 timeliness_status 为空的记录。"""
    idx = get_clean_index()
    p = idx.latest_csv_path(source)
    if not p or not os.path.exists(p):
        raise FileNotFoundError(f"[{source}] 无 cleaned 最新快照")
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    missing = []
    for r in rows:
        if not (r.get("timeliness_status") or "").strip():
            missing.append({
                "title": (r.get("title") or "").strip(),
                "document_number": (r.get("document_number") or "").strip(),
                "publish_date": (r.get("publish_date") or "").strip(),
                "timeliness_status": "",
                "source": source,
            })
    return rows, missing


def _summary_record(source, status, **kw):
    rec = {"source": source, "status": status,
           "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    rec.update(kw)
    return rec


def run_source(source: str, args) -> dict:
    """核验单源（R13）：返回三态摘要 dict——success/partial/unavailable。

    降级纪律（不误标）：外部不可用（CLI/Token 缺失）或查询失败项**不写任何判定**
    （cleaned 效力留空即待核验位，等同 needs_review 语义），不将"未核验"误标为 valid。
    """
    state = vstate.load_state()
    try:
        rows, missing = collect_missing(source)
    except FileNotFoundError as e:
        print(f"[verify_missing:{source}] 跳过: {e}")
        return _summary_record(source, S_UNAVAILABLE, reason=str(e), missing=0, degraded=0)
    print(f"[verify_missing:{source}] cleaned {len(rows)} 条 | 效力缺失（空）{len(missing)} 条")
    if not missing:
        print(f"[verify_missing:{source}] 无效力缺失记录，跳过")
        return _summary_record(source, S_SUCCESS, missing=0, queried=0, ok=0,
                               nomatch=0, fail=0, degraded=0, changed=0)
    if args.dry_run:
        for c in missing:
            print(f"  待查: {c['document_number'] or '(无文号)':<24} | {c['title'][:40]}")
        print(f"[verify_missing:{source}] dry-run：未发起任何北大法宝查询")
        return _summary_record(source, S_SUCCESS, missing=len(missing), queried=0, ok=0,
                               nomatch=0, fail=0, degraded=0, changed=0, dry_run=True)

    cli = pk.find_cli()
    token = pk.load_token(args.token_file) or pk.load_token(os.path.join(OUT_DIR, ".pkulaw_token"))
    if not token:
        print("[verify_missing] 未找到 Token：请提供 --token-file 或 timeliness_review/.pkulaw_token")
        # 外部不可用：不写判定，候选留空待下次核验（R13 降级不误标）
        return _summary_record(source, S_UNAVAILABLE, reason="no_token",
                               missing=len(missing), queried=0, ok=0, nomatch=0, fail=0,
                               degraded=len(missing), changed=0)

    checkpoint = os.path.join(OUT_DIR, f"pkulaw_{source}_missing_checkpoint.jsonl")
    plan, cand2item = pk.build_query_plan(missing)
    if args.probe:
        plan = plan[:args.probe]
    executed = {p.get("key", p.get("title")) for p in plan}
    print(f"[verify_missing:{source}] 查询计划 {len(plan)} 项（CLI 就绪，workers={args.workers}）")
    done = pk.execute_queries(plan, cli, token, checkpoint, workers=args.workers,
                              retry_failed=args.retry_failed, pause=0.2, log=print)

    today = datetime.datetime.now().strftime("%Y%m%d")
    ledger_out = os.path.join(OUT_DIR, f"时效核验_{source}变更台账_{today}.csv")
    changed = []
    stats = {"valid": 0, "amended": 0, "repealed": 0, "expired": 0, "nomatch": 0, "fail": 0}
    for i, c in enumerate(missing):
        it = cand2item[i]
        if it is None or it.get("key", it.get("title")) not in executed:
            continue
        obj = done.get(it["key"]) or done.get(it["title"])
        if obj is None or obj.get("message") != "成功" or not obj.get("data"):
            stats["nomatch" if obj and obj.get("message") == "成功" else "fail"] += 1
            c["verification_note"] = ("北大法宝查询失败: " + str(obj.get("message"))[:120]
                                      if obj and obj.get("message") != "成功"
                                      else "北大法宝 get_law_list 无命中(0条)")
            # 无命中但已核验 → 如实登记 pending，避免污染
            state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                              status="pending", replacement="",
                                              vsource="北大法宝（无同名命中，维持原判定）", state=state)
            continue
        old = c.get("timeliness_status") or ""
        st, rep, vsrc, note = pk.judge_candidate(obj["data"], c)
        if vsrc == "规则判断":
            stats["nomatch"] += 1
            state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                              status=old or "pending", replacement=rep,
                                              vsource="北大法宝（无同名命中，维持原判定）", state=state)
            continue
        state, _, _ = vstate.mark_checked(docno=c.get("document_number", ""), title=c.get("title", ""),
                                          status=st, replacement=rep, vsource=vsrc, state=state)
        stats[st if st in stats else "nomatch"] += 1
        changed.append({"title": c.get("title", ""), "document_number": c.get("document_number", ""),
                        "publish_date": c.get("publish_date", ""), "old_status": old, "new_status": st,
                        "replacement_document": rep, "verification_source": vsrc, "note": note, "source": source})
        print(f"  [变更] {c['document_number'] or '(无文号)':<22} | {c['title'][:24]} | {old or '(空)'} → {st} | {vsrc[:16]}")

    vstate.save_state(state)
    ledger_new = not os.path.exists(ledger_out)
    with open(ledger_out, "a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["title", "document_number", "publish_date",
                                           "old_status", "new_status", "replacement_document",
                                           "verification_source", "note", "source"])
        if ledger_new:
            w.writeheader()
        w.writerows(changed)
    print(f"[verify_missing:{source}] 判定统计: {stats} | 本批变更 {len(changed)} 条 | 台账(追加) {ledger_out}")
    print(f"[verify_missing:{source}] 状态已写入 verification_state.json")
    # R13 三态：fail>0 或部分候选未完成 → partial（可断点续跑）；否则 success
    queried = sum(stats.values())
    failed = stats["fail"] + stats["nomatch"]
    status = S_SUCCESS if failed == 0 else S_PARTIAL
    return _summary_record(source, status, missing=len(missing), queried=queried,
                           ok=stats["valid"] + stats["amended"] + stats["repealed"] + stats["expired"],
                           nomatch=stats["nomatch"], fail=stats["fail"],
                           degraded=failed, changed=len(changed), detail=stats)


def _overall(sources_sum: list[dict]) -> str:
    """聚合三态：unavailable 任一→unavailable；其余 partial 任一→partial；否则 success。"""
    if any(s.get("status") == S_UNAVAILABLE for s in sources_sum):
        return S_UNAVAILABLE
    if any(s.get("status") == S_PARTIAL for s in sources_sum):
        return S_PARTIAL
    return S_SUCCESS


def main() -> int:
    ap = argparse.ArgumentParser(description="四源效力缺失记录定向核验（聚焦驱动；R13 三态摘要）")
    ap.add_argument("--source", default="nfra", help="源: nfra/mof/pbc/gov 或 all")
    ap.add_argument("--dry-run", action="store_true", help="仅列出候选，不查询")
    ap.add_argument("--probe", type=int, default=0, help="只查前 N 条（冒烟，各源分别生效）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（风控 ≤2）")
    ap.add_argument("--retry-failed", action="store_true", help="重试 checkpoint 失败查询")
    ap.add_argument("--token-file", default="", help="北大法宝 token 文件")
    args = ap.parse_args()

    sources = ALL_SOURCES if args.source == "all" else [args.source]
    summaries = []
    for s in sources:
        try:
            summaries.append(run_source(s, args))
        except Exception as e:  # noqa: BLE001  外部异常 → 降级 unavailable（R13 不误标）
            print(f"[verify_missing:{s}] 异常降级: {e}")
            summaries.append(_summary_record(s, S_UNAVAILABLE, reason="exception:" + str(e)[:120],
                                             missing=0, queried=0, ok=0, nomatch=0, fail=0,
                                             degraded=0, changed=0))
    overall = _overall(summaries)
    # R13：执行摘要落盘 timeliness_review/verify_summary_{date}.json（供告警通道消费）
    today = datetime.datetime.now().strftime("%Y%m%d")
    sum_out = os.path.join(OUT_DIR, f"verify_summary_{today}.json")
    payload = {"overall": overall, "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "sources": summaries}
    existing = []
    if os.path.exists(sum_out):
        try:
            existing = json.load(open(sum_out, encoding="utf-8"))
            if isinstance(existing, dict):
                existing = [existing]
        except Exception:  # noqa: BLE001
            existing = []
    with open(sum_out, "w", encoding="utf-8") as fh:
        json.dump({"overall": overall,
                   "history": (existing[-5:] + [payload]) if isinstance(existing, list) else [payload]},
                  fh, ensure_ascii=False, indent=1)
    print(f"\n[verify_missing] R13 执行摘要 → {sum_out}")
    for s in summaries:
        print(f"  {s['source']:5s} {s['status']:12s} missing={s.get('missing', 0):>4} "
              f"ok={s.get('ok', 0):>4} degraded={s.get('degraded', 0):>4} changed={s.get('changed', 0):>3}")
    print(f"  overall: {overall}")
    return {"success": 0, S_PARTIAL: 2, S_UNAVAILABLE: 3}[overall]


if __name__ == "__main__":
    raise SystemExit(main())
