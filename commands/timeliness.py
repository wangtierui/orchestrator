# -*- coding: utf-8 -*-
"""commands.timeliness — orchestrator 命令：timeliness（自 cli.py 迁移，2026-09-13 审查 P3）。"""
from __future__ import annotations

import sys

import paths


def run(argv):
    """timeliness verify|sync|summary
    - verify [--source ...]：效力缺失核验（R13 三态 exit：0/2/3）
    - sync [--dry-run]：核验变更台账 → 归属表时效同步（F-C03 显式入口）
    - summary：读最新 verify_summary_*.json（F-K07 告警接点消费）"""
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    if not argv:
        print("用法: orchestrator timeliness verify|sync|summary ...")
        return 1
    _rev = os.path.join(paths.ROOT, "modules", "regulatory_scrapers", "timeliness_review")
    if argv[0] == "sync":
        # F-C03：state/台账 → 归属表"时效状态"列同步（sync_to_classifier 唯一实现）唯一 CLI 入口。
        import csv as _csv  # noqa: PLC0415
        import glob as _glob  # noqa: PLC0415

        from bootstrap import bootstrap  # noqa: PLC0415
        bootstrap("regulatory_scrapers",
                  extra=("modules/regulatory_scrapers/timeliness_review",))
        import verification_state as _vstate  # noqa: PLC0415
        ledgers = sorted(_glob.glob(os.path.join(_rev, "时效核验_*变更台账_*.csv")))
        if not ledgers:
            print("[timeliness] 无变更台账（时效核验尚未产生变更）；先运行 verify")
            return 1
        changed = []
        for p in ledgers[-1:]:
            with open(p, encoding="utf-8-sig", newline="") as fh:
                changed.extend(list(_csv.DictReader(fh)))
            print(f"[timeliness] 台账: {os.path.basename(p)}（{len(changed)} 行变更）")
        dry = "--dry-run" in argv
        m, um, cpath = _vstate.sync_to_classifier(changed, dry_run=dry)
        print(f"[timeliness] 归属表时效同步：匹配 {m} / 未匹配 {um}（{cpath}）"
              + ("［dry-run 未写盘］" if dry else ""))
        return 0
    if argv[0] == "summary":
        import glob as _glob  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        files = sorted(_glob.glob(os.path.join(_rev, "verify_summary_*.json")))
        if not files:
            print("[timeliness] 无 verify_summary_*.json（先运行 verify）")
            return 1
        data = _json.load(open(files[-1], encoding="utf-8"))
        print(f"[timeliness] {os.path.basename(files[-1])}")
        print(_json.dumps(data, ensure_ascii=False, indent=1)[:2000])
        return 0
    if argv[0] != "verify":
        print(f"未知 timeliness 子命令: {argv[0]}（可用: verify | sync | summary）")
        return 1
    script = os.path.join(paths.ROOT, "modules", "regulatory_scrapers",
                          "timeliness_review", "verify_missing.py")
    if not os.path.exists(script):
        print(f"[timeliness] 脚本缺失: {script}")
        return 3
    r = subprocess.run([sys.executable, "-X", "utf8", script] + argv[1:], timeout=7200)
    return r.returncode
