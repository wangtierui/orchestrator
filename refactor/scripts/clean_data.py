#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清空采集数据（PG 落盘 + 本地 master/state 文件）。

用于调试重抓：删除某个源（或全部源）在 PG 中的原始表、批次与运行记录，
以及本地增量 master / status 文件，让该源在下次运行时必须从头重抓。

注意外键顺序：``raw_{ns}`` 副表 ``doc_id`` 引用 ``raw_docs.id``，
因此必须先删副表再删主表，否则会被外键约束拦下。

用法
----
    python -m scripts.clean_data --source mof --dry-run   # 仅预览
    python -m scripts.clean_data --source mof             # 真清 mof
    python -m scripts.clean_data --source all             # 清空全部采集源

提示：清空前建议先停掉正在跑的采集进程（``pkill -f "collectors --source all"``），
否则数据会立刻又被写入。
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

from config import settings
from sqlalchemy import create_engine, text

# 采集源（与 stats.py 保持一致）；RAW 源在 PG 走 RawStore（主表 raw_docs + 副表 raw_{ns}）
COLLECT_SOURCES = ["gov", "pbc", "mof", "nfra", "internal"]

# 项目根：scripts/ 的上一级（refactor/）
ROOT = Path(__file__).resolve().parent.parent


def _engine():
    return create_engine(settings.database.url, future=True)


def _local_files(ns: str) -> list[Path]:
    """该源可能遗留的本地文件（增量 master / status），仅在文件存在时返回。"""
    pats = [
        ROOT / "data" / "raw" / f"{ns}_laws.json",
        ROOT / "data" / "raw" / f"{ns}*.json",
        ROOT / "data" / "state" / f"{ns}_status.json",
        ROOT / "data" / "state" / f"{ns}*.json",
        ROOT / "data" / "state" / "status.json",
    ]
    out: list[Path] = []
    for p in pats:
        if "*" in str(p):
            out.extend(Path(x) for x in glob.glob(str(p)))
        elif p.exists():
            out.append(p)
    return list(dict.fromkeys(out))  # 去重


def clean_source(ns: str, dry_run: bool) -> None:
    print(f"\n=== source: {ns} ===")
    eng = _engine()
    # 1) PG：先副表后主表（外键顺序），再运行/批次记录
    #    raw_{ns} 表名无法参数化，但 ns 来自白名单，安全。
    steps = [
        (f"raw_{ns}", f"DELETE FROM raw_{ns}"),
        ("raw_docs", "DELETE FROM raw_docs WHERE namespace = :ns"),
        ("master_runs", "DELETE FROM master_runs WHERE namespace = :ns"),
        ("fetch_runs", "DELETE FROM fetch_runs WHERE source = :ns"),
    ]
    if dry_run:
        for tbl, _ in steps:
            print(f"  [dry-run] 将清空表 {tbl} (namespace/source={ns})")
    else:
        with eng.begin() as c:
            for tbl, sql in steps:
                try:
                    n = c.execute(text(sql), {"ns": ns}).rowcount
                    print(f"  deleted {n:>4} rows from {tbl}")
                except Exception as e:  # 表名/列名不符时跳过，不中断其余源
                    print(f"  skip {tbl}: {e}")
    # 2) 本地 master / state 文件
    files = _local_files(ns)
    if dry_run:
        for f in files:
            print(f"  [dry-run] 将删除本地文件 {f.relative_to(ROOT)}")
    else:
        for f in files:
            try:
                f.unlink()
                print(f"  removed {f.relative_to(ROOT)}")
            except FileNotFoundError:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description="清空采集数据（PG + 本地文件）")
    ap.add_argument("--source", required=True,
                    help="源名称: " + "/".join(COLLECT_SOURCES) + " 或 all")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅预览将要删除的对象，不真正删除")
    args = ap.parse_args()

    if args.source == "all":
        sources = COLLECT_SOURCES
    elif args.source in COLLECT_SOURCES:
        sources = [args.source]
    else:
        ap.error(f"--source 必须是 {COLLECT_SOURCES + ['all']}")

    print(f"mode: {'DRY-RUN (no changes)' if args.dry_run else 'APPLY'}")
    for ns in sources:
        clean_source(ns, args.dry_run)
    print("\n完成。")


if __name__ == "__main__":
    main()
