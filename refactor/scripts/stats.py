#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局链路数据统计（refactor 独立项目）。

设计目标
--------
一把伞式统计：从「采集」逐步扩展到「清洗 / RAG 向量化 / docgraph / 全链路」。
通过 ``--phase`` 选择统计阶段：

* ``collect``  （当前已实现）采集环节 —— 各源 PG 落盘量、fetch_runs 指标、
  fetch_dead_letter 死信、master_runs 批次，以及正在运行的采集进程。
* ``pipeline`` / ``rag`` / ``docgraph`` / ``all``（未来扩展）统计下游链路。

不注入 ``sys.path``：以 refactor/ 为项目根运行（见 pyproject.toml 的 pythonpath=["."]），
数据库连接走 ``config.settings.database.url``。

用法
----
    python -m refactorctl stats --phase collect --json
    python -m scripts.stats --phase collect --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone

from config import settings
from sqlalchemy import create_engine, text

# 采集源（与 collectors/__main__.py 的 SOURCES 顺序保持一致）
COLLECT_SOURCES = ["gov", "pbc", "mof", "nfra", "internal"]
LEGACY_TABLES = ["raw_gov", "raw_pbc", "raw_mof", "raw_nfra", "raw_internal"]


# --------------------------------------------------------------------------- #
# 低层工具
# --------------------------------------------------------------------------- #
def _engine():
    return create_engine(settings.database.url, future=True)


def _q(engine, sql):
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        return list(result.keys()), result.fetchall()


def _running_procs() -> list[str]:
    """正在运行的采集进程（采集入口关键字）。"""
    try:
        out = subprocess.run(
            "ps -ef | grep -E 'collectors --source|cli.py run|nfra/collector.py' | grep -v grep",
            shell=True, capture_output=True, text=True,
        )
        return [
            ln for ln in out.stdout.strip().splitlines()
            if "grep" not in ln and "safe-delete" not in ln and "codebuddy" not in ln.lower()
        ]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# 阶段：collect（采集环节）
# --------------------------------------------------------------------------- #
def collect_stats(engine) -> dict:
    stats: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}

    # 1) raw_docs 主表 by namespace
    _, rows = _q(
        engine,
        "SELECT COALESCE(namespace,'(NULL)'), count(*) FROM raw_docs GROUP BY 1 ORDER BY 2 DESC",
    )
    stats["raw_docs"] = {r[0]: r[1] for r in rows}

    # 2) 旧架构 per-source 副表（重构后可能为空，仅作对照）
    legacy: dict[str, int | None] = {}
    for tbl in LEGACY_TABLES:
        try:
            _, rows = _q(engine, f'SELECT count(*) FROM "{tbl}"')
            legacy[tbl] = rows[0][0]
        except Exception:  # noqa: BLE001
            legacy[tbl] = None
    stats["legacy_tables"] = legacy

    # 3) fetch_runs 累计指标 by source
    _, rows = _q(
        engine,
        """
        SELECT source,
               count(*),
               coalesce(sum(attempted), 0),
               coalesce(sum(ok), 0),
               coalesce(sum(status_403), 0),
               coalesce(sum(status_429), 0),
               coalesce(sum(status_5xx), 0),
               coalesce(sum(status_404_perm), 0),
               coalesce(sum(status_net), 0),
               coalesce(sum(parse_err), 0),
               coalesce(sum(recovered), 0),
               coalesce(sum(remaining), 0)
        FROM fetch_runs
        GROUP BY source
        ORDER BY source
        """,
    )
    stats["fetch_runs"] = [
        {
            "source": r[0], "runs": r[1], "attempted": r[2], "ok": r[3],
            "status_403": r[4], "status_429": r[5], "status_5xx": r[6],
            "status_404_perm": r[7], "status_net": r[8], "parse_err": r[9],
            "recovered": r[10], "remaining": r[11],
        }
        for r in rows
    ]

    # 4) master_runs by namespace（列名动态取，兼容不同命名）
    _, cols = _q(
        engine,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='master_runs'",
    )
    mcols = [c[0] for c in cols]
    src = next((x for x in mcols if x in ("source", "namespace")), None)
    master: dict[str, int] = {}
    if src:
        _, rows = _q(engine, f"SELECT {src}, count(*) FROM master_runs GROUP BY 1 ORDER BY 2 DESC")
        master = {r[0]: r[1] for r in rows}
    stats["master_runs"] = master

    # 5) fetch_dead_letter by source
    _, rows = _q(
        engine,
        "SELECT source, count(*) FROM fetch_dead_letter GROUP BY 1 ORDER BY 2 DESC",
    )
    stats["dead_letters"] = {r[0]: r[1] for r in rows}

    # 6) 正在运行的采集进程
    stats["running_procs"] = _running_procs()
    return stats


def _print_collect(stats: dict) -> None:
    def line(label: str, value) -> None:
        print(f"  {label:<16}: {value}")

    print(f"===== 采集统计 (生成时间: {stats['generated_at']}) =====")

    print("\n[raw_docs 主表 by namespace]")
    if stats["raw_docs"]:
        for ns, n in stats["raw_docs"].items():
            line(ns, n)
    else:
        print("  (空)")

    print("\n[legacy 副表]")
    for tbl, n in stats["legacy_tables"].items():
        line(tbl, n if n is not None else "(无表/无权限)")

    print("\n[fetch_runs 累计 by source]")
    if stats["fetch_runs"]:
        hdr = f"  {'source':<8}{'runs':>5}{'att':>7}{'ok':>7}{'403':>6}{'429':>6}{'5xx':>6}{'404p':>7}{'net':>6}{'parse':>7}{'rec':>6}{'rem':>7}"
        print(hdr)
        for r in stats["fetch_runs"]:
            print(f"  {r['source']:<8}{r['runs']:>5}{r['attempted']:>7}{r['ok']:>7}"
                  f"{r['status_403']:>6}{r['status_429']:>6}{r['status_5xx']:>6}"
                  f"{r['status_404_perm']:>7}{r['status_net']:>6}{r['parse_err']:>7}"
                  f"{r['recovered']:>6}{r['remaining']:>7}")
    else:
        print("  (空：尚无 fetch_runs 记录)")

    print("\n[master_runs by namespace]")
    if stats["master_runs"]:
        for ns, n in stats["master_runs"].items():
            line(ns, n)
    else:
        print("  (空)")

    print("\n[fetch_dead_letter by source]")
    if stats["dead_letters"]:
        for src, n in stats["dead_letters"].items():
            line(src, n)
    else:
        print("  (空：无死信)")

    print("\n[正在运行的采集进程]")
    if stats["running_procs"]:
        for ln in stats["running_procs"]:
            # 只截取关键部分，避免行过长
            print("  " + ln.strip()[:160])
    else:
        print("  (无采集进程在运行)")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="全局链路统计：采集/清洗/RAG/docgraph（当前实现 collect 阶段）")
    ap.add_argument(
        "--phase", choices=["collect"], default="collect",
        help="统计阶段；collect=采集环节，后续扩展 pipeline/rag/docgraph/all",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（便于管道/聚合）")
    args = ap.parse_args()

    engine = _engine()
    # 后续扩展：elif args.phase == "pipeline": stats = pipeline_stats(engine)
    if args.phase == "collect":
        stats = collect_stats(engine)
    else:  # pragma: no cover - 当前仅 collect
        raise SystemExit(f"未知/未实现阶段: {args.phase}")

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    else:
        _print_collect(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
