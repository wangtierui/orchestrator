# -*- coding: utf-8 -*-
"""refactor init 子命令实现（项目前置步骤）。

职责：
  1. 连通性检查；
  2. 安装扩展（当前 Apache AGE；未来向量等在此追加）；
  3. 按依赖顺序幂等建表 + 索引（全部 IF NOT EXISTS，存在则跳过）；
  4. 导出 schema.sql 灾备文件（始终与权威 DDL 同步），落在 init/ 目录下。

用法：
  python -m refactor.init [--db-url URL] [--no-dump]
  （亦可由顶层 `python -m refactor init` 路由到此）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 仓库根入路径（与包内其他模块一致）
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
_ROOT_STR = str(_ROOT)
if _ROOT_STR in sys.path:
    sys.path.remove(_ROOT_STR)
sys.path.insert(0, _ROOT_STR)

try:
    from psycopg import connect
except ImportError:  # pragma: no cover - 兼容 psycopg2
    from psycopg2 import connect  # type: ignore

from . import ddl as ddl_mod

DEFAULT_DB_URL = "postgresql+psycopg://root:5462@localhost:5432/test"
SCHEMA_SQL_PATH = _HERE / "schema.sql"


def _pg_url(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg://", "postgresql://")


def _extension_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (name,))
    return cur.fetchone() is not None


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (name,))
    return cur.fetchone() is not None


def _run_sql(cur, sql: str) -> None:
    """按分号切分执行（DDL 内无分号字面量，安全）。"""
    for stmt in sql.split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)


def _build_schema_sql() -> str:
    header = (
        "-- ===========================================================================\n"
        "-- refactor PostgreSQL 恢复用 DDL（灾备 / 从零重建）\n"
        "-- 本文件由 `python -m refactor.init` 自动生成，勿手工编辑；\n"
        "-- 权威来源：refactor/init/ddl.py\n"
        "-- 用法：psql \"$REFACTOR_DB_URL\" -f refactor/init/schema.sql\n"
        "-- ===========================================================================\n\n"
    )
    parts = ["-- 安装扩展（仅一次）"]
    for _label, ext in ddl_mod.EXTENSIONS:
        parts.append(f"CREATE EXTENSION IF NOT EXISTS {ext};")
    parts.append("")
    parts.append(ddl_mod.FULL_DDL)
    return header + "\n".join(parts) + "\n"


def cmd_init(args) -> int:
    db_url = args.db_url or os.environ.get("REFACTOR_DB_URL") or DEFAULT_DB_URL
    print(f"[init] 目标库: {_pg_url(db_url)}")
    try:
        conn = connect(_pg_url(db_url))
    except Exception as exc:  # noqa: BLE001 - 给清晰的错误而不是堆栈
        print(f"[init] 无法连接数据库: {exc}")
        return 2
    try:
        with conn.cursor() as cur:
            # 1) 扩展（age 等，未来向量在此追加）
            for label, ext in ddl_mod.EXTENSIONS:
                existed = _extension_exists(cur, ext)
                if not existed:
                    cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext};")
                print(f"  extension {label} ({ext}): {'已存在' if existed else '已创建'}")
            conn.commit()

            # 2) 表 + 索引（按依赖顺序；全部 IF NOT EXISTS）
            table_checks = [
                ("master_runs", ddl_mod.MASTER_RUNS_DDL),
                ("raw_items", ddl_mod.RAW_ITEMS_DDL),
                ("cleaned_items", ddl_mod.CLEANED_ITEMS_DDL),
                ("clauses", ddl_mod.CLAUSES_DDL),
                ("docgraph_edges", ddl_mod.DOCGRAPH_EDGES_DDL),
            ]
            for tname, tsql in table_checks:
                existed = _table_exists(cur, tname)
                _run_sql(cur, tsql)
                print(f"  table {tname}: {'已存在' if existed else '已创建'}")
            if ddl_mod.VECTOR_DDL.strip():
                _run_sql(cur, ddl_mod.VECTOR_DDL)
                print("  vector tables: 已创建")
            conn.commit()
    finally:
        conn.close()

    # 3) 导出 schema.sql 灾备文件（始终与权威 DDL 同步）
    if not args.no_dump:
        SCHEMA_SQL_PATH.write_text(_build_schema_sql(), encoding="utf-8")
        print(f"[init] 已导出灾备 DDL -> {SCHEMA_SQL_PATH}")
    print("[init] 完成（全部 IF NOT EXISTS，幂等安全）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="refactor init", description="初始化项目前置（扩展+表+索引，幂等）")
    ap.add_argument("--db-url", default=None, help="REFACTOR_DB_URL 形式；缺省取环境变量或内置默认")
    ap.add_argument("--no-dump", action="store_true", help="不导出 schema.sql")
    args = ap.parse_args(argv)
    return cmd_init(args)


if __name__ == "__main__":
    raise SystemExit(main())
