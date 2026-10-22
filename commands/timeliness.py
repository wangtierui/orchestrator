# -*- coding: utf-8 -*-
"""commands.timeliness — orchestrator 命令：timeliness（自 cli.py 迁移，2026-09-13 审查 P3）。"""

from __future__ import annotations

import sys

import paths
from config.exitcodes import ExitCode


def _print_state_summary() -> None:
    """打印 SSOT（`verification_state.json`）的**分布与年龄**。

    为什么加（2026-10-22，处置遗留风险"948 条 pending placeholder"）：
    这些记录是历次 `verify` 判定"法宝查无同名命中"的**合法结论**（不是脏数据），
    但它们在 `status` 上仍记 `pending`，于是：
      · 从 `status` 看不出"是没查过、还是查过没命中"；
      · 运维无法判断"948 条要不要人工裁决"。
    本函数按 **status × 权威性原因 × 年龄** 三维摊开，让"这批到底是什么"可读。
    判据（`authority()`）与回填命令**同源**，不另写一套。
    """
    import collections
    from datetime import datetime

    from bootstrap import bootstrap

    bootstrap("regulatory_scrapers",
              extra=("modules/regulatory_scrapers/timeliness_review",))
    import verification_state as _vstate

    from std_lib.common_lib import timeliness_ssot as _ssot

    state = _vstate.load_state() or {}
    if not state:
        print("[timeliness] verification_state.json 为空或缺失")
        return
    by_status: collections.Counter = collections.Counter()
    by_reason: collections.Counter = collections.Counter()
    by_age: collections.Counter = collections.Counter()
    placeholder: list[tuple[str, str]] = []
    for k, rec in state.items():
        if not isinstance(rec, dict):
            continue
        st = str(rec.get("status") or "")
        by_status[st] += 1
        _ok, why = _ssot.authority(rec)
        by_reason[why] += 1
        last = str(rec.get("last_checked_at") or "")
        try:
            days = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            days = -1
        bucket = "从未核验" if days < 0 else (
            "≤30 天" if days <= 30 else ("31–90 天" if days <= 90 else ">90 天"))
        by_age[bucket] += 1
        if why == "non_authoritative_source" and len(placeholder) < 5:
            placeholder.append((k, str(rec.get("verification_source") or "")[:40]))

    print(f"\n[timeliness] SSOT 记录 {len(state)}")
    print("  按状态：" + " / ".join(f"{k or '(空)'}={v}"
                                for k, v in sorted(by_status.items(), key=lambda x: -x[1])))
    print("  按可用性：" + " / ".join(f"{k}={v}"
                                 for k, v in sorted(by_reason.items(), key=lambda x: -x[1])))
    print("  按核验年龄：" + " / ".join(f"{k}={v}" for k, v in by_age.items()))
    n_na = by_reason.get("non_authoritative_source", 0)
    if n_na:
        print(f"  说明：其中 {n_na} 条为「法宝查无同名命中/规则判断」——**核验已完成、只是无权威结论**，"
              "不会覆盖归属表；它们在 90 天后会自动回到高优先级队列重查。")
        for k, vsrc in placeholder:
            print(f"    样例 {k} ← {vsrc}")


def _sync_from_state(dry_run: bool) -> int:
    """`timeliness sync --from-state`：**直接从 SSOT 回填归属表**（TD-21 入口）。

    为什么需要这条入口
    ------------------
    原有的 `sync` 只读「最新一份变更台账」，而台账**只在 `verify` 成功跑完时**产出。
    一旦 `verify` 因配额/网络失败（2026-10-22 实测：跑 4662s 后配额耗尽 rc=2），
    权威结论已写进 `verification_state.json`，却**永远没有机会**进入归属表 ——
    于是层1 出现 44 条 `state=valid/repealed/amended vs 归属表=pending` 的漂移，
    而且**下一次成功 verify 之前都无法自愈**。

    本入口把"要不要回填"与"verify 是否成功"**解耦**：只要 SSOT 里有权威结论
    （非 pending/uncertain、未超 90 日、来源不是"无同名命中/规则判断"），
    就能安全回填。判据与门禁**同源**（`std_lib.common_lib.timeliness_ssot`），
    不存在"门禁不算漂移、命令却去改"或反之的分叉。

    ⚠️ **只改归属表这一层**：底座（`eff_status`）由 `cli.py classify --steps base`
    从归属表重建、cleaned 由 `apply` 写。改完本层后必须跑基座重建，
    否则漂移只是从层1 转移到层2（门禁会立刻抓到，不会静默）。
    """
    import collections
    import csv as _csv

    from bootstrap import bootstrap

    bootstrap("regulatory_scrapers",
              extra=("modules/regulatory_scrapers/timeliness_review",))
    import verification_state as _vstate

    from std_lib.common_lib import timeliness_ssot as _ssot

    # cleaned 的 body_text 字段可能超过 csv 默认 128KB 上限（归属表本身不大，
    # 但同一进程里若先读过 cleaned 会沿用旧上限；显式抬高更稳）。
    _csv.field_size_limit(sys.maxsize)

    state = _vstate.load_state() or {}
    if not state:
        print("[timeliness] verification_state.json 为空或缺失（先运行 verify）")
        return ExitCode.FAIL
    with open(_vstate.CLASSIFIER_CSV, encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))
    if not rows:
        print(f"[timeliness] 归属表为空：{_vstate.CLASSIFIER_CSV}")
        return ExitCode.FAIL

    rep = _ssot.drift_pairs(rows, state)
    pairs = rep["pairs"]
    print(f"[timeliness] SSOT 记录 {len(state)}｜归属表 {len(rows)} 行"
          f"｜可匹配 {rep['checked']}｜无 SSOT 记录 {rep['no_state']}")
    print(f"[timeliness] 跳过（不作为回填依据）：{rep['skipped']}"
          f"｜归属表为空（不擅自定级）：{rep['empty_attr']}")
    if pairs:
        cnt = collections.Counter((p["state"], p["attr"]) for p in pairs)
        print(f"[timeliness] 应回填 {len(pairs)} 条（state→归属表 分布）：")
        for (st, at), n in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"    {st:20s} ← {at:20s} {n:4d}")

    changed, unmatched, path = _vstate.apply_status_by_rfn(pairs, dry_run=dry_run)
    print(f"[timeliness] 归属表时效回填：改动 {changed} / 未命中 {unmatched}（{path}）"
          + ("［dry-run 未写盘］" if dry_run else ""))
    if not dry_run and changed:
        print("[timeliness] ⚠️ 下一步必须重建基座（否则漂移转到层2）："
              "`python cli.py classify --steps base`")
    return ExitCode.OK


def run(argv):
    """timeliness verify|sync|summary
    - verify [--source ...]：效力缺失核验（R13 三态 exit：0/2/3）
    - sync [--dry-run] [--from-state]：变更台账 → 归属表同步；`--from-state`
      改为**直接从 SSOT 回填**（verify 失败/配额耗尽时仍可消漂移；TD-21）
    - summary：读最新 verify_summary_*.json（F-K07 告警接点消费）"""
    import os
    import subprocess

    if not argv:
        print("用法: orchestrator timeliness verify|sync|summary ...")
        print(run.__doc__)
        return ExitCode.FAIL
    if argv[0] in ("-h", "--help", "help"):
        # 2026-10-22：此前 `cli.py timeliness --help` 会落到"未知子命令"，帮助**不可达**
        # （`cli.py` 对 `timeliness` 注册的是单个位置参数 action，help 也走 handler）。
        print(run.__doc__)
        return ExitCode.OK
    _rev = os.path.join(paths.ROOT, "modules", "regulatory_scrapers", "timeliness_review")
    if argv[0] == "sync":
        dry = "--dry-run" in argv
        if "--from-state" in argv:
            return _sync_from_state(dry)
        # F-C03：state/台账 → 归属表"时效状态"列同步（sync_to_classifier 唯一实现）唯一 CLI 入口。
        import csv as _csv
        import glob as _glob

        from bootstrap import bootstrap

        bootstrap("regulatory_scrapers", extra=("modules/regulatory_scrapers/timeliness_review",))
        import verification_state as _vstate

        ledgers = sorted(_glob.glob(os.path.join(_rev, "时效核验_*变更台账_*.csv")))
        if not ledgers:
            print("[timeliness] 无变更台账（时效核验尚未产生变更）；先运行 verify")
            return ExitCode.FAIL
        changed = []
        for p in ledgers[-1:]:
            with open(p, encoding="utf-8-sig", newline="") as fh:
                changed.extend(list(_csv.DictReader(fh)))
            print(f"[timeliness] 台账: {os.path.basename(p)}（{len(changed)} 行变更）")
        m, um, cpath = _vstate.sync_to_classifier(changed, dry_run=dry)
        print(
            f"[timeliness] 归属表时效同步：匹配 {m} / 未匹配 {um}（{cpath}）"
            + ("［dry-run 未写盘］" if dry else "")
        )
        return ExitCode.OK
    if argv[0] == "summary":
        import glob as _glob
        import json as _json

        _print_state_summary()

        files = sorted(_glob.glob(os.path.join(_rev, "verify_summary_*.json")))
        if not files:
            print("[timeliness] 无 verify_summary_*.json（先运行 verify）")
            return ExitCode.FAIL
        data = _json.load(open(files[-1], encoding="utf-8"))
        print(f"[timeliness] {os.path.basename(files[-1])}")
        print(_json.dumps(data, ensure_ascii=False, indent=1)[:2000])
        return ExitCode.OK
    if argv[0] != "verify":
        print(f"未知 timeliness 子命令: {argv[0]}（可用: verify | sync | summary）")
        return ExitCode.FAIL
    script = os.path.join(
        paths.ROOT, "modules", "regulatory_scrapers", "timeliness_review", "verify_missing.py"
    )
    if not os.path.exists(script):
        print(f"[timeliness] 脚本缺失: {script}")
        return ExitCode.ENV
    r = subprocess.run([sys.executable, "-X", "utf8", script] + argv[1:], timeout=7200)
    return r.returncode
