# -*- coding: utf-8 -*-
"""prune_orphan_processed.py — 清理「孤儿 processed 记录」（2026-09-13）

背景
----
`processed/<ipn>.json` 是被主索引引用的**逐制度产物**（主记录 + `_fulltext` + `_clauses`
+ `_rich`）。随着身份纠正/路径跟随/IPN 重算（`internal refine-identity`、
`tools/normalize_internal_naming.py`、定向补摄取），历史记录会被新 IPN 取代，旧 IPN 的
产物文件即成为**孤儿**：不在主索引里、`original_path` 指向已改名路径。

危害（非致命，但污染可观测性）：
  · `internal reocr` 遍历 processed 时会为它们记 `missing_original` 告警 →
    真实缺口被噪声淹没（实测 179 条孤儿 vs 7 条真实待 OCR）；
  · 白占磁盘（主记录 + 全文 + 条款 + 富内容四份）。

处置
----
把孤儿整套（`<ipn>.json` / `<ipn>_fulltext.json` / `<ipn>_clauses.{json,md}` / `<ipn>_rich.json`）
**移入** `backups/processed_orphans_<ts>/`（保留结构，可回滚），**不删除**；幂等；dry-run 默认。

用法
----
  python tools/prune_orphan_processed.py            # dry-run：列出孤儿与体量
  python tools/prune_orphan_processed.py --apply
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IPB = os.path.join(ROOT, "modules", "internal_policy_base")
DATA = os.path.join(IPB, "data")
PROCESSED = os.path.join(DATA, "processed")
INDEX_PATH = os.path.join(DATA, "internal_policy_index.json")
BACKUP_ROOT = os.path.join(IPB, "backups")

# 同一 IPN 的产物后缀（缺失者跳过，不报错）
_SUFFIXES = (".json", "_fulltext.json", "_clauses.json", "_clauses.md", "_rich.json")


def build_plan() -> dict:
    """只读规划：找出不在主索引中的 IPN 及其产物文件。"""
    if not os.path.exists(INDEX_PATH):
        return {"orphans": [], "suffix_counts": {}, "bytes": 0, "indexed": 0, "total_ipn": 0}
    indexed = {r.get("ipn") for r in
               json.load(open(INDEX_PATH, encoding="utf-8")).get("records", [])}
    mains = [p for p in glob.glob(os.path.join(PROCESSED, "*.json"))
             if not p.endswith(("_fulltext.json", "_clauses.json", "_rich.json"))]
    orphan_ipns = []
    for p in mains:
        ipn = os.path.basename(p)[: -len(".json")]
        if ipn not in indexed:
            orphan_ipns.append(ipn)
    files, nbytes, suffix_counts = [], 0, collections.Counter()
    for ipn in orphan_ipns:
        for suf in _SUFFIXES:
            f = os.path.join(PROCESSED, ipn + suf)
            if os.path.exists(f):
                files.append(f)
                nbytes += os.path.getsize(f)
                suffix_counts[os.path.basename(f).split("_", 1)[1] if "_" in os.path.basename(f) else ".json"] += 1
    return {"orphans": orphan_ipns, "files": files, "suffix_counts": dict(suffix_counts),
            "bytes": nbytes, "indexed": len(indexed), "total_ipn": len(mains)}


def apply_plan(plan: dict, *, backup: bool = True) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"processed_orphans_{ts}")
    moved, failed = 0, 0
    entries = []
    for f in plan["files"]:
        dst = os.path.join(backup_dir, os.path.basename(f))
        try:
            os.makedirs(backup_dir, exist_ok=True)
            shutil.move(f, dst)
            moved += 1
            entries.append(os.path.basename(f))
        except OSError:
            failed += 1
    if backup:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "orphan_ipns": plan["orphans"], "moved_files": entries,
                       "moved": moved, "failed": failed,
                       "note": "孤儿 processed 产物（不在主索引中的 IPN）。回滚：把本目录文件移回 processed/。"},
                      fh, ensure_ascii=False, indent=2)
    return {"moved": moved, "failed": failed, "backup_dir": backup_dir}


def main() -> int:
    ap = argparse.ArgumentParser(description="清理孤儿 processed 记录（移入 backups，可回滚）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    plan = build_plan()
    mb = plan["bytes"] / 1048576
    print(f"[orphan] 主索引 {plan['indexed']} 条 | processed IPN {plan['total_ipn']} 个"
          f" | 孤儿 {len(plan['orphans'])} 个 / {len(plan.get('files', []))} 文件 / {mb:.2f} MB")
    print(f"[orphan] 产物构成：{plan['suffix_counts']}")
    for ipn in plan["orphans"][:6]:
        print("   ", ipn)
    if not args.apply:
        print("[orphan] dry-run 结束（未改动）。加 --apply 执行。")
        return 0
    res = apply_plan(plan, backup=not args.no_backup)
    print(f"[orphan] 完成：移出 {res['moved']} 文件，失败 {res['failed']}")
    print(f"[orphan] 备份与清单 → {res['backup_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
