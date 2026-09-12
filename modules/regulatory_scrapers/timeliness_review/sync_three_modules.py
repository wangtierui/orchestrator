# -*- coding: utf-8 -*-
"""
sync_three_modules.py —— 效力查验变更 → 三模块层级联动（可复用编排器，2026-08-30）

定位（A-08，2026-09-12）：**辅助编排/子步**——生产唯一编排为 tools/run_production_refresh.py；
本脚本承载"核验变更 → 三模块传播"的专项链（被 refresh 编排或人工按需调用），
不与其竞争主链职责（避免双编排重叠：clean_index 重建/回写/gates 均以 refresh 为准）。

用途：校验（verify_source_pkulaw.py）产出变更台账后，按既定纪律依次驱动
  regulatory_scrapers → regulatory_classifier → internal_policy_drafter 三模块，
确保「数据层回写 → 权威库层级同步 → 交付库引用核验」全链路无遗漏，并输出各环节执行报告。

⚠️ 关键顺序纪律（2026-08-29 实测教训，勿颠倒）：
  1. 归属表时效同步（sync_to_classifier）**先于** sync_all_layers；
  2. **镜像刷新（N-5）必须先于 sync_all_layers**——否则其 ③ diff_timeliness 以过期镜像
     比对会误判方向；
  3. **final.json / base.json 传播必须在 sync_all_layers 之后**——
     diff_timeliness 会对归属表执行 SSOT 校正（历史残留时效 → 核验状态），
     若先传播则会基于中间态生成，导致数据底座陈旧（2026-08-29 曾因此返工）。

流程：
  ① regulatory_scrapers：回写源 cleaned 三字段（jsonl+csv 双轨、备份+原子写）→ clean_index 重建 → S-3/S-4 门禁
  ② regulatory_classifier：归属表时效同步 → N-5 镜像刷新 → sync_all_layers --apply
     → final.json eff_status 传播 → base.json 重建自检 → 门禁/测试/产物校验
  ③ internal_policy_drafter：引用门禁 --strict → 变更 RFN 在 docs 中的引用与时效核查
  ④ 输出各环节执行报告（Markdown）

用法：
  python sync_three_modules.py --source nfra --ledger <台账CSV>            # 完整执行
  python sync_three_modules.py --source nfra --ledger <台账CSV> --dry-run  # 仅报告不写盘
  python sync_three_modules.py --source nfra --auto-ledger                # 自动取当日台账
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # regulatory_scrapers/
CLASSIFIER = os.path.abspath(os.path.join(ROOT, "..", "regulatory_classifier"))
INTERNAL = os.path.abspath(os.path.join(ROOT, "..", "internal_policy_drafter"))
REVIEW = os.path.join(ROOT, "timeliness_review")
PY = sys.executable


from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）


def _run(args, cwd, desc, dry_run=False):
    """执行子命令，返回 (ok, output_tail)。dry_run 时仅打印不执行。"""
    print(f"\n  ▸ {desc}")
    if dry_run:
        print(f"    [dry-run] {' '.join(args)}")
        return True, ""
    r = subprocess.run([PY] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)   # 审查 P2-5
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    if tail:
        print("\n".join("      " + l for l in tail.splitlines()))
    return r.returncode == 0, tail


def stage_scrapers(source, changed, dry_run, report):
    """① 数据层回写 + 索引重建 + 门禁（C-12：回写一律经统一单点 writeback_source）。"""
    sys.path.insert(0, ROOT)
    sys.path.insert(0, REVIEW)
    import apply_timeliness_to_cleaned as apply_mod  # noqa: PLC0415  统一回写单点（C-12）
    updates = {}
    for c in changed:
        nd = _norm_docno(c.get("document_number"))
        if nd and len(nd) >= 5:
            updates.setdefault(nd, {
                "timeliness_status": c.get("new_status") or "",
                "replacement_document": c.get("replacement_document") or "",
                "verification_source": "北大法宝",
            })

    def fields_for(r):
        return updates.get(_norm_docno(r.get("document_number")))

    st = apply_mod.writeback_source(source, fields_for, dry_run=dry_run, backup_tag="sync")
    if "reason" in st:
        print(f"  ① scrapers：{source} {st['reason']}")
        report.append(("① regulatory_scrapers", st["reason"]))
        return False
    synced = st.get("written", 0)
    if dry_run:
        print(f"  ① scrapers：{source} 回写预演 {synced} 条（dry-run 未写盘）")
    else:
        print(f"  ① scrapers：{source} cleaned 三字段回写 {synced} 条（jsonl+csv 双轨·统一单点）")

    _run(["-c", "import sys;sys.path.insert(0,r'%s');from clean_index import get_clean_index;"
                "i=get_clean_index(rebuild=True);l=i.latest('%s');"
                "print('rebuilt', l.get('date'), l.get('record_count'))" % (ROOT, source)],
         ROOT, "clean_index 重建")
    # A-09 修复（2026-09-12）：原引 std_lib/tools/check_pipeline_contract.py 与
    # check_enum_values.py（仓内不存在 → 恒 False，门禁形同虚设）。改跑统一门禁入口
    # （gates 已含等价检查：gate_contract=编排契约 / gate_enum_values=枚举一致性）。
    _orch = os.path.abspath(os.path.join(ROOT, "..", ".."))
    ok1, _ = _run([os.path.join(_orch, "cli.py"), "gates"], ROOT, "交付门禁（gates 全量）", dry_run)
    ok2 = ok1
    report.append(("① regulatory_scrapers", f"回写 {synced} 条；gates✅{ok1}"))
    return ok1 and ok2


def stage_classifier(source, changed, dry_run, report):
    """② 归属表 → 镜像 → 层级同步 → 数据底座传播 → 门禁。"""
    sys.path.insert(0, REVIEW)
    import verification_state as vstate
    m = um = 0
    if changed and not dry_run:
        m, um, _ = vstate.sync_to_classifier(changed, dry_run=False)
    elif changed:
        m, um, _ = vstate.sync_to_classifier(changed, dry_run=True)
    print(f"  ② classifier：归属表时效同步 匹配 {m} / 未匹配 {um}")

    # N-5 镜像刷新（必须早于 sync_all_layers）
    # A-09/F-C09 修复（2026-09-12）：原手工 shutil.copy2 目标路径指向不存在文件
    # （镜像实际在 recall_audit/output/verification_state.mirror.json，常量 MIRROR_PATH）
    # → 恒 FileNotFoundError（镜像实际滞后 13 天）。统一走 verification_state_mirror.refresh()
    # （按源 mtime 自动刷新，与 Gate2 读同一常量路径；镜像为生成物无需 .bak）。
    if not dry_run:
        try:
            _ra = os.path.join(CLASSIFIER, "recall_audit")
            if _ra not in sys.path:
                sys.path.insert(0, _ra)
            from verification_state_mirror import refresh as _mirror_refresh  # noqa: PLC0415
            _mp = _mirror_refresh()
            print(f"     N-5 镜像刷新完成（{_mp}）")
        except Exception as _e:  # noqa: BLE001
            print(f"     N-5 镜像刷新失败: {_e!r}")

    # R1 修复（2026-09-08）：原引 sync_all_layers.py / run_gates.py（仓内不存在，哑引用必败）
    # → 改指现仓等价入口：层级同步 = rfn 索引/指纹由归属表重建；门禁 = orchestrator cli.py gates。
    orch = os.path.abspath(os.path.join(CLASSIFIER, "..", ".."))
    _layers_cmd = ("import sys;"
                   f"sys.path.insert(0, r'{orch}');sys.path.insert(0, r'{CLASSIFIER}');"
                   "from rfn.registry import rebuild_index;"
                   "r=rebuild_index();print('rfn 索引/指纹已由归属表重建')")
    ok3, t3 = _run(["-c", _layers_cmd], CLASSIFIER,
                   "层级同步（rfn 索引/指纹重建，R1）", dry_run)
    if not dry_run:
        _propagate_final()
        _run([os.path.join("scripts", "build_base_from_attr.py"), "--all"], CLASSIFIER,
             "base.json 重建")
    ok4, _ = _run([os.path.join("scripts", "build_base_from_attr.py"), "--check"], CLASSIFIER,
                  "base.json 自检", dry_run)
    ok5, _ = _run(["cli.py", "gates"], orch, "交付门禁（cli.py gates，R1）", dry_run)
    report.append(("② regulatory_classifier", f"归属表匹配 {m}；层级同步✅{ok3}；base✅{ok4}；门禁✅{ok5}"))
    return ok3 and ok4 and ok5


def _propagate_final():
    """归属表时效 → final.json eff_status（末尾再跑，避免中间态）。"""
    attr_csv = os.path.join(CLASSIFIER, "data", "人身保险公司-文件归属表.csv")
    attr = {r["监管文件编号"]: (r.get("时效状态") or "").strip()
            for r in csv.DictReader(open(attr_csv, encoding="utf-8-sig"))}
    data = os.path.join(CLASSIFIER, "data")
    total = 0
    for f in os.listdir(data):
        if not re.match(r"^_t?(?:[1-9]|10)(?:_\d+)?_final\.json$", f):
            continue
        p = os.path.join(data, f)
        d = json.load(open(p, encoding="utf-8"))
        n = 0
        for r in d:
            new = attr.get(r.get("监管文件编号"), "") or "valid"
            if (r.get("eff_status") or "").strip() != new:
                r["eff_status"] = new
                n += 1
        if n:
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        total += n
    print(f"     final.json eff_status 传播 {total} 条")
    return total


def stage_internal(changed, dry_run, report):
    """③ 交付库引用门禁 + 变更 RFN 引用核查。"""
    ok6, t6 = _run([os.path.join("scripts", "verify_regulatory_citations.py"), "--strict"],
                   INTERNAL, "引用门禁 --strict", dry_run)
    note = "0 未命中" if ok6 else "存在未命中，需人工复核"
    report.append(("③ internal_policy_drafter", f"引用门禁✅{ok6}（{note}）"))
    return ok6


def main() -> int:
    ap = argparse.ArgumentParser(description="效力变更 → 三模块层级联动（编排器）")
    ap.add_argument("--source", default="nfra", help="数据源")
    ap.add_argument("--ledger", default="", help="变更台账 CSV（--auto-ledger 时忽略）")
    ap.add_argument("--auto-ledger", action="store_true", help="自动取当日台账")
    ap.add_argument("--dry-run", action="store_true", help="仅报告不写盘")
    args = ap.parse_args()

    today = datetime.datetime.now().strftime("%Y%m%d")
    if args.auto_ledger or not args.ledger:
        cands = sorted(glob.glob(os.path.join(REVIEW, f"时效核验_{args.source}变更台账_{today}.csv")))
        if not cands:
            print(f"[sync] 未找到当日台账（{args.source} / {today}），无变更可同步")
            return 0
        ledger = cands[-1]
    else:
        ledger = args.ledger
    if not os.path.exists(ledger):
        print(f"[sync] 台账不存在：{ledger}")
        return 1
    changed = list(csv.DictReader(open(ledger, encoding="utf-8-sig")))
    print(f"[sync] 台账 {os.path.basename(ledger)}：变更 {len(changed)} 条 | 源 {args.source}"
          + (" | DRY-RUN" if args.dry_run else ""))

    report = []
    a = stage_scrapers(args.source, changed, args.dry_run, report)
    b = stage_classifier(args.source, changed, args.dry_run, report)
    c = stage_internal(changed, args.dry_run, report)

    print("\n================ 三模块联动汇总 ================")
    for name, detail in report:
        print(f"  {name}: {detail}")
    print("===============================================")
    print("✅ 三模块联动完成" if (a and b and c) else "⚠️ 存在未完成环节，请查看上方输出")
    return 0 if (a and b and c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
