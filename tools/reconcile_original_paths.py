# -*- coding: utf-8 -*-
"""reconcile_original_paths.py — 内部制度「主索引路径 ↔ 原件库」对账与重定位（2026-09-13）

背景（reports/内部制度原件双份存储与索引漂移分析_20260913.md · §4.3）
--------------------------------------------------------------------------
`internal_policy_base.indexer.ingest()` 的幂等键为**内容 sha256**，且跳过条件为
`prev.ipn == f.ipn`；当源目录被重组/改名（同一内容换路径）时该文件被判为"已摄入"而
**静默 skip**，索引里的 `relative_path`/`original_path` 停留在旧布局。叠加"主索引按 IPN 去重"
（`indexer.py:211-217`），最终形成：

    索引 957 条中仅 443 条可解析；514 条指向已不存在的旧目录；
    `internal reocr` 因此在 `extract.py:293-295` 静默跳过 46% 的记录。

本工具按**内容**（而非路径）把索引路径重新定位到原件库中的实际位置，使不变式
「索引 `relative_path` = 原件库实际落位路径」重新成立。

对账口径（三分）
----------------
1. **可解析**：路径已在磁盘 → 不动。
2. **漂移**：路径不在磁盘，但 `sha256` 在原件库可定位 → 重定位。
   - 唯一匹配 → 直接采用；
   - 多重匹配 → 选优：① 与索引原路径**同部门前缀**优先 ② 层级最浅 ③ 字典序，
     并写 `path_relocated_ambiguous=true`（可审计）。
3. **无内容匹配** → 不改路径，按类登记：
   - **非制度正文（表格类 `xls/xlsx`）** → `non_policy`——本语料制度正文载体为
     pdf/doc/docx，表格一律为附表/台账/清单（实证见 `NON_POLICY_EXTS` 注释）；
     门禁按登记基线容忍其存在、但**不得增长**；
   - 其余（pdf/doc/docx）→ `missing_original` = 真实缺口，门禁直接阻断。

副作用与安全性
--------------
- 只改写**路径字段**（`relative_path`/`original_path`），**不改内容、不删除任何文件**；
- `--apply` 前自动备份将被改写的 `internal_policy_index.json`、`_ingest_state.json`
  与受影响的 `processed/<ipn>.json` 到 `modules/internal_policy_base/backups/original_paths_<ts>/`；
- 每条被改记录写入 `path_relocated_from`（原路径）→ 支持审计与回滚；
- 幂等：重复运行无变化即 `unchanged`（第二次 `--apply` 应报 0 变更）；
- 同步迁移 `_ingest_state.json` 的 `path_key` 字段（v2 schema：内容键 + 仓内落位路径）。

用法
----
  python tools/reconcile_original_paths.py                 # dry-run（默认，仅报告）
  python tools/reconcile_original_paths.py --apply         # 执行重定位（含备份）
  python tools/reconcile_original_paths.py --apply --no-backup   # 跳过备份（已知有外部备份时）
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IPB = os.path.join(ROOT, "modules", "internal_policy_base")
DATA = os.path.join(IPB, "data")
ORIGINALS = os.path.join(DATA, "originals")
PROCESSED = os.path.join(DATA, "processed")
INDEX_PATH = os.path.join(DATA, "internal_policy_index.json")
STATE_PATH = os.path.join(DATA, "_ingest_state.json")
BACKUP_ROOT = os.path.join(IPB, "backups")

# 非制度正文（表格类）识别：**按扩展名**——本语料中制度正文载体为 pdf/doc/docx，
# 全部 xls/xlsx 均为附表/台账/清单/标准/模板/说明（2026-09-13 实证：索引 102 条 xls/xlsx
# 无一为制度正文；其中 19 条路径可解析、其余按内容可重定位或属已失效台账）。
# 关键词仅用于 detail 标注，不参与判定（避免"名称恰好不含关键词"造成误判为真缺失）。
NON_POLICY_EXTS = {".xls", ".xlsx"}
LEDGER_HINTS = ("清单", "自查情况表", "台账", "统计表", "收集表")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_non_policy_sheet(file_name: str) -> bool:
    """非制度正文（表格类）判定：扩展名 xls/xlsx。"""
    return os.path.splitext(file_name or "")[1].lower() in NON_POLICY_EXTS


def is_ledger_named(file_name: str) -> bool:
    """detail 标注用：名称是否命中台账/清单类关键词（不参与豁免判定）。"""
    return any(k in (file_name or "") for k in LEDGER_HINTS)


def _norm(rel: str) -> str:
    return os.path.normcase(rel.replace("/", os.sep))


def _fresh_sha(path: str) -> str:
    """按内容读 sha（不信任索引里的值，避免对账基于陈旧哈希）。"""
    return _sha256_file(path)


def scan_originals() -> dict[str, list[str]]:
    """{sha256: [相对 original 根的路径, ...]}（对原件库做一次内容寻址扫描）。"""
    out: dict[str, list[str]] = collections.defaultdict(list)
    for dirpath, _dirnames, filenames in os.walk(ORIGINALS):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                out[_fresh_sha(full)].append(os.path.relpath(full, ORIGINALS))
            except OSError:
                continue
    return out


def pick_candidate(rec: dict, cands: list[str]) -> tuple[str, bool]:
    """多候选选优：① 同部门前缀 ② 层级最浅 ③ 字典序。返回 (路径, 是否歧义)。"""
    old = (rec.get("relative_path") or "").replace("/", os.sep)
    old_top = old.split(os.sep)[0] if os.sep in old else ""
    same_top = [c for c in cands if old_top and c.split(os.sep)[0] == old_top]
    pool = same_top or cands
    pool = sorted(pool, key=lambda c: (c.count(os.sep), c))
    return pool[0], len(pool) > 1


def build_plan() -> dict:
    """生成对账计划（只读，不落盘）：分类 + 每条的重定位目标。"""
    index = json.load(open(INDEX_PATH, encoding="utf-8"))
    records = index.get("records", [])
    by_sha = scan_originals()

    plan: dict = {
        "total": len(records),
        "resolvable": [],
        "relocate": [],
        "non_policy": [],
        "missing": [],
        "unchanged": 0,
    }
    for rec in records:
        rel = (rec.get("relative_path") or "").replace("/", os.sep)
        if rel and os.path.exists(os.path.join(ORIGINALS, rel)):
            plan["resolvable"].append(rec)
            continue
        cands = by_sha.get(rec.get("sha256") or "", [])
        if not cands:
            bucket = (
                plan["non_policy"]
                if is_non_policy_sheet(rec.get("file_name", ""))
                else plan["missing"]
            )
            bucket.append(rec)
            continue
        target, ambiguous = pick_candidate(rec, cands)
        plan["relocate"].append(
            {
                "rec": rec,
                "target": target,
                "ambiguous": ambiguous,
                "old": rel,
                "candidates": len(cands),
            }
        )
    return plan


def _backup(paths: list[str]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, f"original_paths_{ts}")
    for src in paths:
        if not os.path.exists(src):
            continue
        rel = os.path.relpath(src, DATA)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return dest


def _atomic_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_plan(plan: dict, *, backup: bool = True) -> dict:
    """执行重定位：改索引 + 改 processed 路径字段 + 迁移 state 的 path_key。"""
    touched_proc = [
        os.path.join(PROCESSED, (r["rec"].get("ipn") or "") + ".json") for r in plan["relocate"]
    ]
    touched_proc = [p for p in touched_proc if os.path.exists(p)]
    backup_dir = ""
    if backup:
        backup_dir = _backup([INDEX_PATH, STATE_PATH] + touched_proc)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = 0
    for item in plan["relocate"]:
        rec = item["rec"]
        target_posix = item["target"].replace(os.sep, "/")
        old = rec.get("relative_path", "")
        if _norm(old) == _norm(target_posix):
            continue
        rec["relative_path"] = target_posix
        rec["original_path"] = "originals/" + target_posix
        rec["path_relocated_from"] = old
        rec["path_relocated_at"] = now
        if item["ambiguous"]:
            rec["path_relocated_ambiguous"] = True
        # 同步 processed 主记录
        pp = os.path.join(PROCESSED, (rec.get("ipn") or "") + ".json")
        if os.path.exists(pp):
            try:
                pr = json.load(open(pp, encoding="utf-8"))
            except (OSError, ValueError):
                pr = None
            if isinstance(pr, dict):
                pr["relative_path"] = target_posix
                pr["original_path"] = "originals/" + target_posix
                pr["path_relocated_from"] = old
                pr["path_relocated_at"] = now
                _atomic_json(pp, pr)
        changed += 1

    # 索引落盘（保留 stat 等既有字段）
    # 注意：plan['relocate'] 的元素是包装字典 {rec,target,...}，须取 item['rec'] 才能拿到记录本体
    index = json.load(open(INDEX_PATH, encoding="utf-8"))
    by_ipn = {it["rec"].get("ipn"): it["rec"] for it in plan["relocate"]}
    index["records"] = [by_ipn.get(r.get("ipn"), r) for r in index.get("records", [])]
    index["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _atomic_json(INDEX_PATH, index)

    # state 迁移：为每个 IPN 的当前落位路径回填/更新 path_key（v2 schema）
    state_migrated = 0
    if os.path.exists(STATE_PATH):
        state = json.load(open(STATE_PATH, encoding="utf-8"))
        path_of_ipn = {r.get("ipn"): r.get("relative_path", "") for r in index.get("records", [])}
        for key, val in state.items():
            if str(key).startswith("_") or not isinstance(val, dict):
                continue
            want = path_of_ipn.get(val.get("ipn"))
            if want and val.get("path_key") != want:
                val["path_key"] = want
                state_migrated += 1
        meta = state.get("_meta") or {}
        meta.update(
            {
                "schema_version": "2.0",
                "written_by": "tools/reconcile_original_paths.py",
                "written_at": now,
                "path_key": "仓内落位路径（relative_path）；与 sha256 共同构成幂等键",
            }
        )
        state["_meta"] = meta
        _atomic_json(STATE_PATH, state)

    return {
        "relocated": changed,
        "state_path_key_migrated": state_migrated,
        "backup_dir": backup_dir,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="内部制度主索引 ↔ 原件库 路径对账/重定位")
    ap.add_argument("--apply", action="store_true", help="执行重定位（默认 dry-run 只报告）")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（需已知有外部备份）")
    args = ap.parse_args()

    for p in (INDEX_PATH, STATE_PATH, ORIGINALS):
        if not os.path.exists(p):
            print(f"[reconcile] 缺少输入：{p}（数据未就绪）")
            return 1

    plan = build_plan()
    print(f"[reconcile] 索引记录 {plan['total']} 条")
    print(f"  可解析            : {len(plan['resolvable'])}")
    print(
        f"  待重定位（漂移）  : {len(plan['relocate'])}"
        f"（其中多重匹配歧义 {sum(1 for x in plan['relocate'] if x['ambiguous'])}）"
    )
    print(f"  台账类（非正文）  : {len(plan['non_policy'])}")
    print(f"  真缺失（无内容）  : {len(plan['missing'])}")
    for rec in plan["missing"][:10]:
        print(f"      [真缺失] {rec.get('ipn')} | {rec.get('file_name', '')[:60]}")

    if not args.apply:
        print("[reconcile] dry-run 结束（未改动任何文件）。加 --apply 执行。")
        return 0

    res = apply_plan(plan, backup=not args.no_backup)
    print(
        f"[reconcile] 已重定位 {res['relocated']} 条；"
        f"state path_key 迁移 {res['state_path_key_migrated']} 条"
    )
    if res["backup_dir"]:
        print(f"[reconcile] 备份 → {res['backup_dir']}")

    # 复核：重定位后的可解析率
    after = build_plan()
    ok = len(after["resolvable"]) + len(after["relocate"])
    print(
        f"[reconcile] 复核：可解析 {len(after['resolvable'])} + 仍漂移 {len(after['relocate'])}"
        f" = {ok}/{after['total']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
