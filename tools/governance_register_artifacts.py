# -*- coding: utf-8 -*-
"""tools/governance_register_artifacts — 原件注册进治理库（阶段 1，2026-09-18）

背景（方案 §4.2 / §3.2.1）：
    原始文件（PDF/Word/图片）**保持原格式 + 原路径**，只在治理库登记
    「内容 sha256 → 路径集合 + inode + 字节数」。**原件本身不入库**——BLOB 化会摧毁
    本项目的两项既有能力：①`originals/` ↔ `corpus/` 的**跨层硬链接去重**
    （967 个共享 inode；删任一路径不丢数据）②Word COM / PaddleOCR 的按路径抽取链。

    ⚠️ 建模要点：硬链接意味着**同一份内容对应多个路径**，故 `artifact.path_keys`
    是**集合**（JSON 数组）而非单值；表内以 `artifact_key = sha256[:16]` 聚合，
    同一 sha 的不同路径并入同一行的 `path_keys`。

用法：
    python tools/governance_register_artifacts.py                 # 登记 内部原件 + 外部附件
    python tools/governance_register_artifacts.py --scope internal
    python tools/governance_register_artifacts.py --dry-run       # 只统计不写库
    python tools/governance_register_artifacts.py --limit 50

幂等：以 sha 聚合 upsert，重复执行为 no-op（只做路径集合的并集）。
治理库未创建时本工具会**自动建库**（`governance_store.init_db`）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_THIS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from std_lib.common_lib import governance_store as gs  # noqa: E402

IPB_ORIGINALS = os.path.join(ROOT, "modules", "internal_policy_base", "data", "originals")
EXT_ATTACHMENTS = os.path.join(ROOT, "modules", "regulatory_scrapers", "published",
                               "external_attachments.jsonl")


def _row(path: str) -> dict | None:
    """单文件 → artifact 行（缺失/不可读 → None）。"""
    try:
        st = os.stat(path)
        if not os.path.isfile(path):
            return None
        sha = gs.version_of_file(path, short=64)
        if not sha:
            return None
        return {
            "sha256": sha,
            "path": path,
            "bytes": st.st_size,
            "kind": (os.path.splitext(path)[1].lstrip(".").lower() or "unknown"),
            "inode": getattr(st, "st_ino", ""),
        }
    except OSError:
        return None


def _iter_internal(limit: int = 0):
    """内部制度原件（单一扁平原件层）。"""
    if not os.path.isdir(IPB_ORIGINALS):
        print(f"[artifacts] 跳过 internal：目录不存在 {IPB_ORIGINALS}"
              "（数据不入 git，异机需先恢复）")
        return
    names = sorted(os.listdir(IPB_ORIGINALS))
    if limit:
        names = names[:limit]
    for n in names:
        p = os.path.join(IPB_ORIGINALS, n)
        if os.path.isfile(p):
            yield p


def _iter_external(limit: int = 0):
    """外部附件（发布件登记的 local_path；仅登记磁盘仍存在者）。"""
    if not os.path.exists(EXT_ATTACHMENTS):
        print(f"[artifacts] 跳过 external：发布件不存在 {EXT_ATTACHMENTS}"
              "（先运行 `orchestrator base publish`）")
        return
    seen = set()
    n = 0
    with open(EXT_ATTACHMENTS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            p = (rec.get("local_path") or "").strip()
            if not p:
                continue
            p = p if os.path.isabs(p) else os.path.join(ROOT, p)
            if p in seen or not os.path.exists(p):
                continue
            seen.add(p)
            yield p
            n += 1
            if limit and n >= limit:
                return


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="原件注册进治理库（阶段 1）")
    ap.add_argument("--scope", choices=["internal", "external", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="每类最多登记 N 件（0=全部）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args(argv)

    if not args.dry_run:
        gs.init_db()
        print(f"[artifacts] 治理库：{gs.db_path()}")

    srcs = []
    if args.scope in ("internal", "all"):
        srcs.append(("internal", _iter_internal(args.limit)))
    if args.scope in ("external", "all"):
        srcs.append(("external", _iter_external(args.limit)))

    total, done = 0, 0
    for label, it in srcs:
        batch: list[dict] = []
        for p in it:
            total += 1
            row = _row(p)
            if row:
                batch.append(row)
            if len(batch) >= 200:
                done += _flush(batch, args.dry_run)
                batch = []
        if batch:
            done += _flush(batch, args.dry_run)
        print(f"[artifacts] {label}：扫描 {total} 件（累计可登记 {done}）")

    rows = gs.list_artifacts() if not args.dry_run else []
    multi = [r for r in rows if len(r.get("paths") or []) > 1]
    print(f"[artifacts] 完成：登记 {done} 件"
          + ("（dry-run 未写库）" if args.dry_run else f"；库内共 {len(rows)} 行"))
    if multi:
        print(f"[artifacts] 其中 {len(multi)} 行含**多路径**（跨层硬链接/同内容副本）——"
              "这正是 path_keys 采用集合语义的原因")
    return 0


def _flush(batch: list[dict], dry_run: bool) -> int:
    if dry_run:
        return len(batch)
    return gs.upsert_artifacts(batch)


if __name__ == "__main__":
    raise SystemExit(main())
