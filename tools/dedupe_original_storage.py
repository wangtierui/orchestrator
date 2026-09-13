# -*- coding: utf-8 -*-
"""dedupe_original_storage.py — 内部制度原件库「冗余处置 + 跨层硬链接 + 非正文标记」（P1/P2，2026-09-13）

背景（`reports/内部制度原件双份存储与索引漂移分析_20260913.md` · §6.2 P1/P2）
--------------------------------------------------------------------------
同一批部门制度在仓内有两份物理副本：
  A `modules/regulatory_scrapers/data/corpus/dept_policies`（归集/审计层，306 MB）
  B `modules/internal_policy_base/data/originals`（摄取/运行层，349 MB）
二者内容高度重合（A 的 967 个业务文档内容 B 中全有），B 内部还有 75 组同内容多份拷贝。

本工具一次性处置三类冗余（每类可单独开关，**默认 dry-run**）：

  ① **内部去冗余**（`--dedupe`）：B 内部同内容多份 → 保留「被索引 `relative_path` 引用者」
     （一份内容若被多个 IPN 引用则都保留），其余**移入** `backups/originals_dedupe_<ts>/`。
     无任何引用的组：保留路径最浅者作代表并**标记为孤儿待查**（不删，报告可见）。
     ⚠️ move 到 backups **不释放磁盘**（仅脱离运行面、保留可回滚）。释放空间靠 ②。

  ② **跨层硬链接**（`--hardlink`）：B 中「内容在 A 中唯一存在」的文件，替换为**指向 A 的硬链接**
     （同卷 NTFS 生效；`os.link` 失败自动跳过并计数）。B 与 A 仍**两个路径都可用**，
     但共享同一 inode → 释放约 306 MB。
     ⚠️ 语义变更：此后**修改任一路径会影响另一处**（原件只读场景可接受；删除任一路径不丢数据）。
     ⚠️ 前置：A 若被删除/移动，B 的硬链接**仍然有效**（inode 引用计数），不会丢数据。

  ③ **非正文表格台账显式标记**（`--mark-non-policy`）：索引中「路径不可解析且内容不在 B」
     的 xls/xlsx（制度检视台账，非制度正文）→ 写 `policy_kind="non_policy_sheet"` +
     `excluded_reason` + `excluded_at`。**不删除记录、不改 relative_path**（保证 957 制度数与
     下游 merged/analysis 不变），使「排除」从隐式变为**显式可审计**。

安全性
------
- 默认 dry-run；`--apply` 前自动备份 **索引 + 将被移动的 processed 无关**（本工具不改 processed）
  与生成 `manifest.json`（逐条动作记录 + 原路径），支持回滚；
- 硬链接采用「先 `os.link` 到临时名 → `os.replace` 原子替换」；失败保留原文件；
- A 侧（corpus）**只读**，从不写入/删除。

用法
----
  python tools/dedupe_original_storage.py                        # dry-run 报告
  python tools/dedupe_original_storage.py --apply                # 三类全做
  python tools/dedupe_original_storage.py --apply --no-hardlink  # 仅去冗余 + 标记
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
CORPUS = os.path.join(ROOT, "modules", "regulatory_scrapers", "data",
                      "corpus", "dept_policies")
BACKUP_ROOT = os.path.join(IPB, "backups")

NON_POLICY_EXTS = {".xls", ".xlsx"}
EXCLUDED_REASON = "非制度正文（检视台账/清单），内容已不在原件库；见 reports/内部制度原件双份存储与索引漂移分析_20260913.md §4.6"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _walk(d: str) -> list[str]:
    out = []
    for dp, _dn, fn in os.walk(d):
        out.extend(os.path.join(dp, f) for f in fn)
    return out


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _atomic_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _rel_safe(p: str, base: str) -> str:
    """相对路径（清单可读用）；跨盘符（Windows ValueError）时回退绝对路径，绝不因清单而失败。"""
    try:
        return os.path.relpath(p, base)
    except ValueError:
        return os.path.abspath(p)


def build_plan() -> dict:
    """只读对账：产出 去冗余/硬链接/标记 三类动作清单。"""
    index = json.load(open(INDEX_PATH, encoding="utf-8"))
    recs = index.get("records", [])
    referenced = {_norm(os.path.join(ORIGINALS, (r.get("relative_path") or "").replace("/", os.sep)))
                  for r in recs if r.get("relative_path")}

    disk = _walk(ORIGINALS)
    by_sha: dict[str, list[str]] = collections.defaultdict(list)
    for p in disk:
        try:
            by_sha[_sha256(p)].append(p)
        except OSError:
            continue

    move, orphans, keep = [], [], []
    for _sha, paths in by_sha.items():
        if len(paths) == 1:
            keep.extend(paths)
            continue
        refs = [p for p in paths if _norm(p) in referenced]
        if refs:
            keep.extend(refs)
            move.extend(p for p in paths if p not in refs)
        else:
            reps = sorted(paths, key=lambda x: (x.count(os.sep), x))
            keep.append(reps[0])
            orphans.append(reps[0])
            move.extend(reps[1:])

    corpus_by_sha: dict[str, str] = {}
    if os.path.isdir(CORPUS):
        for p in _walk(CORPUS):
            try:
                corpus_by_sha.setdefault(_sha256(p), p)
            except OSError:
                continue
    links = [(p, corpus_by_sha[_sha256(p)]) for p in keep
             if _sha256(p) in corpus_by_sha
             and not os.path.samefile(p, corpus_by_sha[_sha256(p)])]

    marks = [r for r in recs
             if os.path.splitext(r.get("file_name") or "")[1].lower() in NON_POLICY_EXTS
             and not os.path.exists(os.path.join(ORIGINALS,
                                                (r.get("relative_path") or "").replace("/", os.sep)))
             and not r.get("excluded_at")]

    def _sz(paths) -> int:
        t = 0
        for p in paths:
            try:
                t += os.path.getsize(p)
            except OSError:
                pass
        return t

    # 保留下来的、但无任何索引记录引用的文件（多为"内容未被索引覆盖"的散件/图片/台账本体）
    unreferenced = [p for p in keep if _norm(p) not in referenced]

    return {"records": len(recs), "disk_files": len(disk), "by_sha": by_sha,
            "move": move, "move_bytes": _sz(move), "orphans": orphans,
            "unreferenced": unreferenced,
            "links": links, "link_bytes": _sz([p for p, _c in links]),
            "marks": marks, "corpus_files": len(corpus_by_sha)}


def apply_plan(plan: dict, *, dedupe=True, hardlink=True, mark=True,
               backup=True) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"originals_dedupe_{ts}") if backup else ""
    actions = {"moved": 0, "linked": 0, "link_skipped": 0, "marked": 0}
    if backup:
        os.makedirs(backup_dir, exist_ok=True)
        # 备份"标记前的索引"（③ 会改索引；moved/linked 清单见 manifest.json）
        shutil.copy2(INDEX_PATH, os.path.join(backup_dir, "internal_policy_index.json"))

    # ① 内部去冗余：move 到 backups（保留相对路径，可回滚）
    if dedupe and plan["move"]:
        for p in plan["move"]:
            try:
                if backup:
                    rel = os.path.relpath(p, DATA)
                    dst = os.path.join(backup_dir, "moved", rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(p, dst)
                else:
                    os.remove(p)
                actions["moved"] += 1
            except OSError:
                continue

    # ② 跨层硬链接：先 link 到临时名再原子替换
    if hardlink and plan["links"]:
        for p, c in plan["links"]:
            if not os.path.exists(p):
                continue
            tmp = p + ".linktmp"
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
                os.link(c, tmp)
                os.replace(tmp, p)
                actions["linked"] += 1
            except OSError:
                actions["link_skipped"] += 1
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    # ③ 非正文台账显式标记（不改路径、不删记录）
    if mark and plan["marks"]:
        index = json.load(open(INDEX_PATH, encoding="utf-8"))
        target = {r.get("ipn") for r in plan["marks"]}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in index.get("records", []):
            if r.get("ipn") in target:
                r["policy_kind"] = "non_policy_sheet"
                r["excluded_reason"] = EXCLUDED_REASON
                r["excluded_at"] = now
                actions["marked"] += 1
        _atomic_json(INDEX_PATH, index)

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backup_dir": backup_dir,
        "actions": actions,
        "moved": [_rel_safe(p, DATA) for p in plan["move"]],
        "linked": [[_rel_safe(p, ORIGINALS), _rel_safe(c, ROOT)]
                   for p, c in plan["links"]],
        "orphans_kept": [_rel_safe(p, ORIGINALS) for p in plan["orphans"]],
        "unreferenced_kept": [_rel_safe(p, ORIGINALS) for p in plan.get("unreferenced", [])],
        "marked_ipns": [r.get("ipn") for r in plan["marks"]],
    }
    if backup:
        _atomic_json(os.path.join(backup_dir, "manifest.json"), manifest)
    return {"actions": actions, "backup_dir": backup_dir}


def main() -> int:
    ap = argparse.ArgumentParser(description="原件库冗余处置（内部去冗余 / 跨层硬链接 / 非正文标记）")
    ap.add_argument("--apply", action="store_true", help="执行（默认 dry-run）")
    ap.add_argument("--no-dedupe", action="store_true", help="跳过①内部去冗余")
    ap.add_argument("--no-hardlink", action="store_true", help="跳过②跨层硬链接")
    ap.add_argument("--no-mark", action="store_true", help="跳过③非正文标记")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（不建议）")
    args = ap.parse_args()

    for p in (INDEX_PATH, ORIGINALS, CORPUS):
        if not os.path.exists(p):
            print(f"[dedupe] 缺少输入：{p}")
            return 1

    plan = build_plan()
    mb = lambda n: round(n / 1048576, 1)  # noqa: E731
    print(f"[dedupe] 索引记录 {plan['records']} | originals 文件 {plan['disk_files']}"
          f" | corpus 唯一内容 {plan['corpus_files']}")
    print(f"  ① 内部冗余副本可移出 : {len(plan['move'])} 个（{mb(plan['move_bytes'])} MB）"
          f"；无引用代表(孤儿待查) {len(plan['orphans'])}")
    print(f"  ② 可跨层硬链接       : {len(plan['links'])} 个（约省 {mb(plan['link_bytes'])} MB）")
    print(f"  ③ 非正文台账可标记   : {len(plan['marks'])} 条")
    print(f"  ·  保留但无索引引用   : {len(plan['unreferenced'])} 个（散件/图片/台账本体；"
          "不处置，仅登记）")

    if not args.apply:
        print("[dedupe] dry-run 结束（未改动任何文件）。加 --apply 执行。")
        return 0

    res = apply_plan(plan, dedupe=not args.no_dedupe, hardlink=not args.no_hardlink,
                     mark=not args.no_mark, backup=not args.no_backup)
    a = res["actions"]
    print(f"[dedupe] 已移出 {a['moved']} | 已硬链接 {a['linked']}（跳过 {a['link_skipped']}）"
          f" | 已标记 {a['marked']}")
    if res["backup_dir"]:
        print(f"[dedupe] 备份与清单 → {res['backup_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
