# -*- coding: utf-8 -*-
"""split_internal_nonpolicy.py — 把「非制度正文件」从制度原件库中隔离归置（2026-09-13）

背景
----
`originals/` 是**制度正文原件库**（供摄取/OCR/条款/起草对照）。但历史整树拷贝把**非制度正文**
一并带了进来：部门台账/清单（xls/xlsx）、Office 锁文件（`~$*`）、缩略图库（Thumbs.db）、
图片、压缩包、html/rtf/txt 等。它们既不是制度正文，也不应进入制度索引
（摄入后正是产生"内容已不在原件库"失效记录的那类，见
`reports/内部制度原件双份存储与索引漂移分析_20260913.md` §4.6）。

归置策略
--------
  · **台账/清单类**（xls/xlsx）→ `data/ledgers/<部门>/<原名>`
    —— 保留部门维度：台账的价值正是"哪个部门的工作底稿"。
  · **其他非正文件**（图片/压缩/数据库/html/rtf/txt 等）→ `data/misc/<部门>/<原名>`
  · `originals/制度清单.xlsx` **保持原位**——用户规则 3 指定它作为文号兜底来源。
  · 移动后清理 `originals/` 下的空目录，使其回归"单一扁平原件层"。

安全
----
dry-run 默认；`--apply` 写 `backups/nonpolicy_<ts>/manifest.json`（逐条 from→to）可回滚；
幂等（重跑无变化）；**只移动**，从不删除。

用法
----
  python tools/split_internal_nonpolicy.py            # dry-run
  python tools/split_internal_nonpolicy.py --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IPB = os.path.join(ROOT, "modules", "internal_policy_base")
DATA = os.path.join(IPB, "data")
ORIGINALS = os.path.join(DATA, "originals")
LEDGERS = os.path.join(DATA, "ledgers")
MISC = os.path.join(DATA, "misc")
BACKUP_ROOT = os.path.join(IPB, "backups")

DOC_EXTS = {".pdf", ".doc", ".docx"}
LEDGER_EXTS = {".xls", ".xlsx", ".xlsm"}
KEEP_IN_PLACE = {"制度清单.xlsx"}  # 规则 3 指定的文号兜底来源，留在 originals 根层


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def build_plan() -> dict:
    """只读规划：{move: [(src, dst, kind, dept)], keep: [...], doc_left: [...]}。"""
    move, keep, doc_left = [], [], []
    if not os.path.isdir(ORIGINALS):
        return {"move": move, "keep": keep, "doc_left": doc_left}

    for name in sorted(os.listdir(ORIGINALS)):
        full = os.path.join(ORIGINALS, name)
        if os.path.isfile(full):
            ext = os.path.splitext(name)[1].lower()
            if name in KEEP_IN_PLACE or ext in DOC_EXTS or name.startswith("~$"):
                if name.startswith("~$"):
                    move.append((full, os.path.join(MISC, name), "temp", ""))
                else:
                    keep.append(name)
                continue
            kind = "ledger" if ext in LEDGER_EXTS else "misc"
            base = LEDGERS if kind == "ledger" else MISC
            move.append((full, os.path.join(base, name), kind, ""))
            continue
        if not os.path.isdir(full):
            continue
        for dp, _dn, fn in os.walk(full):
            rel = os.path.relpath(dp, ORIGINALS)
            for f in sorted(fn):
                src = os.path.join(dp, f)
                ext = os.path.splitext(f)[1].lower()
                if ext in DOC_EXTS and not f.startswith("~$"):
                    doc_left.append(os.path.relpath(src, ORIGINALS))
                    continue
                kind = (
                    "temp" if f.startswith("~$") else ("ledger" if ext in LEDGER_EXTS else "misc")
                )
                base = LEDGERS if kind == "ledger" else MISC
                move.append((src, os.path.join(base, rel, f), kind, rel.split(os.sep)[0]))
    return {"move": move, "keep": keep, "doc_left": doc_left}


def apply_plan(plan: dict, *, backup: bool = True) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"nonpolicy_{ts}")
    acts = collections.Counter()
    entries = []
    for src, dst, kind, dept in plan["move"]:
        if _norm(src) == _norm(dst):
            acts["skip_same"] += 1
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            acts[kind] += 1
            entries.append(
                {
                    "src": os.path.relpath(src, DATA).replace(os.sep, "/"),
                    "dst": os.path.relpath(dst, DATA).replace(os.sep, "/"),
                    "kind": kind,
                    "dept": dept,
                }
            )
        except OSError as e:
            acts["failed"] += 1
            entries.append({"src": src, "error": repr(e)[:120]})
    # 清理 originals 下空目录（回归单一扁平原件层）
    removed_dirs = 0
    for dp, _dn, _fn in os.walk(ORIGINALS, topdown=False):
        if dp == ORIGINALS:
            continue
        try:
            if not os.listdir(dp):
                os.rmdir(dp)
                removed_dirs += 1
        except OSError:
            pass
    if backup:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "actions": dict(acts),
                    "removed_empty_dirs": removed_dirs,
                    "entries": entries,
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    return {"actions": dict(acts), "removed_empty_dirs": removed_dirs, "backup_dir": backup_dir}


def plan_prune() -> dict:
    """索引清理规划：剔除「已隔离的非制度正文」记录。

    判据（保守）：记录**路径不可解析** 且（扩展名属表格类 或 文件名以 `~$` 开头）。
    不可解析但属制度正文的（pdf/doc/docx）**一律保留并单独报告**——那属路径问题，
    应先用 `tools/reconcile_original_paths.py` 重定位，绝不在本工具里剔除。
    """
    ip = os.path.join(DATA, "internal_policy_index.json")
    if not os.path.exists(ip):
        return {"keep": [], "drop": [], "stuck": []}
    recs = json.load(open(ip, encoding="utf-8")).get("records", [])
    keep, drop, stuck = [], [], []
    for r in recs:
        rel = (r.get("relative_path") or "").replace("/", os.sep)
        if rel and os.path.exists(os.path.join(ORIGINALS, rel)):
            keep.append(r)
            continue
        name = r.get("file_name") or ""
        ext = os.path.splitext(name)[1].lower()
        (drop if (ext in LEDGER_EXTS or name.startswith("~$")) else stuck).append(r)
    return {"keep": keep, "drop": drop, "stuck": stuck, "total": len(recs)}


def apply_prune(plan: dict, *, backup: bool = True) -> dict:
    ip = os.path.join(DATA, "internal_policy_index.json")
    sp = os.path.join(DATA, "_ingest_state.json")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"prune_{ts}")
    if backup:
        os.makedirs(backup_dir, exist_ok=True)
        for src in (ip, sp):
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup_dir, os.path.basename(src)))
    idx = json.load(open(ip, encoding="utf-8"))
    idx["records"] = plan["keep"]
    idx["count"] = len(plan["keep"])
    idx["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stat = idx.get("stat") or {}
    stat["pruned_nonpolicy"] = len(plan["drop"])
    stat["pruned_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    idx["stat"] = stat
    tmp = ip + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, ip)

    pruned_ipns = {r.get("ipn") for r in plan["drop"]}
    state_removed = 0
    if os.path.exists(sp):
        st = json.load(open(sp, encoding="utf-8"))
        for k in list(st):
            if str(k).startswith("_"):
                continue
            if isinstance(st[k], dict) and st[k].get("ipn") in pruned_ipns:
                del st[k]
                state_removed += 1
        tmp = sp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, sp)
    if backup:
        with open(os.path.join(backup_dir, "pruned.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "kept": len(plan["keep"]),
                    "dropped": len(plan["drop"]),
                    "state_removed": state_removed,
                    "dropped_records": [
                        {
                            "ipn": r.get("ipn"),
                            "file_name": r.get("file_name"),
                            "relative_path": r.get("relative_path"),
                        }
                        for r in plan["drop"]
                    ],
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    return {
        "kept": len(plan["keep"]),
        "dropped": len(plan["drop"]),
        "state_removed": state_removed,
        "backup_dir": backup_dir,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="非制度正文件从原件库隔离归置（含失效索引清理）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--prune-index",
        action="store_true",
        help="同时剔除已隔离文件的失效索引记录（表格类 / ~$ 锁文件）",
    )
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    plan = build_plan()
    kinds = collections.Counter(k for _s, _d, k, _dept in plan["move"])
    print(
        f"[split] 待隔离 {len(plan['move'])} 个：ledger {kinds.get('ledger', 0)} / "
        f"misc {kinds.get('misc', 0)} / temp {kinds.get('temp', 0)}"
    )
    print(f"[split] 保留原位 {len(plan['keep'])}（含制度清单.xlsx，规则 3 兜底来源）")
    if plan["doc_left"]:
        print(
            f"[split] 注意：子目录中仍有制度正文 {len(plan['doc_left'])} 个（应先跑 "
            f"normalize_internal_naming.py）：{plan['doc_left'][:3]}"
        )
    for s, _dst, k, _dept in plan["move"][:8]:
        print(f"   [{k:6s}] {os.path.relpath(s, DATA)}")
    if args.prune_index:
        pp = plan_prune()
        print(
            f"[split] 索引清理：总 {pp.get('total', 0)} → 剔除 {len(pp['drop'])} / "
            f"保留 {len(pp['keep'])}；**制度正文类不可解析（需先重定位，不剔除）** "
            f"{len(pp['stuck'])}"
        )
        for r in pp["stuck"][:8]:
            print(f"   [stuck] {r.get('ipn')} | {r.get('file_name')}")
    if not args.apply:
        print("[split] dry-run 结束（未改动）。加 --apply 执行。")
        return 0
    res = apply_plan(plan, backup=not args.no_backup)
    print(f"[split] 完成：{res['actions']}；清理空目录 {res['removed_empty_dirs']} 个")
    print(f"[split] 清单 → {res['backup_dir']}\\manifest.json")
    if args.prune_index:
        pr = apply_prune(plan_prune(), backup=not args.no_backup)
        print(
            f"[split] 索引清理完成：保留 {pr['kept']} / 剔除 {pr['dropped']}；"
            f"state 移除 {pr['state_removed']} 条"
        )
        print(f"[split] 清理清单 → {pr['backup_dir']}\\pruned.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
