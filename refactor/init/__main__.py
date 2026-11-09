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
try:
    from refactor.config import settings, psycopg_dsn
except ImportError:  # 兼容从 refactor/ 目录直接运行（collectors 上下文）
    from config import settings, psycopg_dsn

# 默认库址统一取自 refactor/config.yaml（不再散落硬编码）
DEFAULT_DB_URL = settings.database.url
SCHEMA_SQL_PATH = _HERE / "schema.sql"


def _pg_url(db_url: str) -> str:
    # SQLAlchemy 风格串（postgresql+psycopg://）转 psycopg 直连串
    return psycopg_dsn(db_url)


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

            # 2) 表 + 索引：唯一权威源 = ddl.FULL_DDL（按依赖顺序，全部 IF NOT EXISTS）。
            #    早期只执行了 5 张阶段表，raw_docs / 各源副表 / 容错账本要靠 RawStore
            #    的 create_all 补建，导致表结构与 ddl.py 漂移；现统一从权威 DDL 重建。
            managed_tables = [
                "master_runs", "raw_items", "raw_docs",
                "raw_mof", "raw_gov", "raw_pbc", "raw_nfra", "raw_internal",
                "cleaned_items", "clauses", "docgraph_edges",
                "fetch_dead_letter", "fetch_runs",
            ]
            existed = {t for t in managed_tables if _table_exists(cur, t)}
            _run_sql(cur, ddl_mod.FULL_DDL)
            for tname in managed_tables:
                print(f"  table {tname}: {'已存在' if tname in existed else '已创建'}")
            if ddl_mod.VECTOR_DDL.strip():
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
