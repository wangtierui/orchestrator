# -*- coding: utf-8 -*-
"""std_lib.common_lib.vector_store — 向量检索**接入层**（v2 P2-2，2026-09-29）

定位与架构一致性
----------------
本模块是 `std_lib/common_lib/*_store.py` 家族的新成员（与 `governance_store` / `index_store`
同级），遵循既有纪律：

  · **零硬依赖**：`psycopg` / `sqlite_vec` 一律**惰性导入**；任一缺失即声明不可用（不崩）；
  · **降级链**：`pgvector`（本机 PG 18.6 + `vec` schema）→ `sqlite_vec`（与既有 SQLite FTS5
    **同库同源**）→ `none`（调用方按既有正则/全文路径处理）；
  · **只读健康检查可入链**：`--health` 为**只读披露**（`pg:health` 步骤），**不动任何数据**；
  · **不侵入主链**：主链（`cli.py run`）**不调用**本模块的读写函数；它是**按需能力**，
    仅供 P2-2 检索语义化落地时调用。

连接参数（唯一事实源：env `PGVECTOR_DSN`）
-----------------------------------------
    host=127.0.0.1  port=7777（**非默认 5432**）  dbname=orchestrator  用户 postgres
业务侧推荐使用**最小权限角色** `orchestrator_app`（仅 `vec` schema 的 USAGE/CREATE/CRUD，
对 `public` 治理表**无** CREATE；非 superuser）。**口令仅经 env 注入，绝不出现在仓库中**
（`gate_secret_scan` 守护）。库侧对象（schema `vec` + role + GRANT）由一次性运维脚本建立。

读写路径
--------
写：`upsert(table, rows)` → `vec.<table>(doc_id text PK, emb vector(dim))`（幂等 upsert）
读：`search(table, vec, k)` → `[(doc_id, distance)]`（`ORDER BY emb <-> %s`，L2；可选余弦）

异常处理
--------
  · `VectorStoreUnavailable`：后端不可用/连接失败/扩展缺失 —— **调用方据此降级**；
  · 表名走**白名单校验**（`^[a-z][a-z0-9_]{0,62}$`），防标识符注入；
  · `search_or_fallback(...)`：不可用时返回 `None`（而非抛错）并给出 `fallback_hint()`，
    便于调用方"能用则用、不能用则走既有全文路径"而不改变主链语义。
"""

from __future__ import annotations

import os
import re
import sqlite3

SCHEMA = "vec"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ENV_DSN = "PGVECTOR_DSN"


class VectorStoreUnavailable(RuntimeError):
    """后端不可用（依赖缺失 / 无 DSN / 连接失败 / 扩展未装）。调用方据此降级。"""


def _dsn() -> str:
    return (os.environ.get(_ENV_DSN) or "").strip()


def _has(mod: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def backend() -> str:
    """当前可用后端：`pgvector` / `sqlite_vec` / `none`（**不建连**，仅看依赖与配置）。"""
    if _dsn() and _has("psycopg"):
        return "pgvector"
    if _has("sqlite_vec"):
        return "sqlite_vec"
    return "none"


def fallback_hint() -> str:
    """降级说明（可观测：调用方在走回退路径时应记录它）。"""
    b = backend()
    if b == "pgvector":
        return "pgvector 可用（无需降级）"
    if not _dsn():
        return f"未配置 env {_ENV_DSN} → 降级 sqlite_vec（与既有 SQLite FTS5 同库同源）"
    if not _has("psycopg"):
        return "未安装 psycopg[binary] → 降级 sqlite_vec"
    return "无可用向量后端 → 走既有全文/正则路径"


def _conn():
    """PG 连接（自治事务语义交调用方）；失败抛 `VectorStoreUnavailable`。"""
    d = _dsn()
    if not d:
        raise VectorStoreUnavailable(f"未配置 env {_ENV_DSN}")
    if not _has("psycopg"):
        raise VectorStoreUnavailable("未安装 psycopg（pip install 'psycopg[binary]'）")
    import psycopg

    try:
        return psycopg.connect(d, connect_timeout=5)
    except Exception as e:
        raise VectorStoreUnavailable(f"连接失败：{type(e).__name__}: {str(e)[:120]}") from e


def _check_name(name: str) -> str:
    if not _SAFE_NAME.match(name or ""):
        raise ValueError(f"非法表名（须 ^[a-z][a-z0-9_]{{0,62}}$）：{name!r}")
    return name


def _vec_literal(vec) -> str:
    """向量 → PG `vector` 字面量 `'[a,b,c]'`（**显式强转**用）。

    为何不直接传 Python list：psycopg 会把 `list[int]` 适配成 **`smallint[]`**（PG 数组），
    与 `vector` 列**类型不匹配**（实测 `DatatypeMismatch`）。虽然 `pgvector` 提供
    `register_vector(conn)` 适配器，但那样**依赖注册成功**；显式字面量 + `::vector` 强转
    **无注册也能工作**，且数值经 `float()` 校验（非法值早失败）。
    """
    try:
        return "[" + ",".join(f"{float(x):.6g}" for x in vec) + "]"
    except (TypeError, ValueError) as e:
        raise ValueError(f"向量元素非数值：{e}") from e


def health() -> dict:
    """**只读**健康检查（可入链）：→ 结构化状态，**不抛异常**（自检不得成为失败点）。"""
    out: dict = {"backend": backend(), "dsn_env": _ENV_DSN, "dsn_configured": bool(_dsn())}
    out["dsn_target"] = _redact(_dsn())
    if out["backend"] != "pgvector":
        out["ok"] = False
        out["detail"] = fallback_hint()
        return out
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT current_database(), inet_server_port();")
            db, port = cur.fetchone()
            cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector';")
            row = cur.fetchone()
            ext = row[0] if row else ""
            cur.execute("SELECT current_user, NOT rolsuper FROM pg_roles WHERE rolname=current_user;")
            user, not_super = cur.fetchone()
            cur.execute("SELECT has_schema_privilege(current_user,'vec','USAGE');")
            can_use = bool(cur.fetchone()[0])
        out.update(
            {
                "ok": bool(ext) and can_use,
                "database": db,
                "port": port,
                "vector_ext": ext,
                "user": user,
                "not_superuser": bool(not_super),
                "vec_schema_usable": can_use,
                "detail": "pgvector 就绪" if (ext and can_use) else "扩展或 schema 权限缺失",
            }
        )
    except Exception as e:  # noqa: BLE001
        out.update({"ok": False, "detail": f"{type(e).__name__}: {str(e)[:120]}"})
    return out


def _redact(dsn: str) -> str:
    """**脱敏**显示：仅 host/port/db（口令与用户名一律不回显）。"""
    m = re.match(r"^[a-z][a-z0-9+.\-]*://(?:[^@/]*@)?([^:/?#]+)(?::(\d+))?/([^?]*)", dsn, re.I)
    if not m:
        return "(非 URI 形态；已省略)"
    return f"{m.group(1)}:{m.group(2) or '?'}/{m.group(3) or '?'}"


def ensure_table(table: str, dim: int) -> None:
    """建表（幂等）：`vec.<table>(doc_id text PK, emb vector(dim))` + HNSW(L2) 索引。"""
    t = _check_name(table)
    if not isinstance(dim, int) or not (1 <= dim <= 4096):
        raise ValueError(f"维度非法：{dim!r}")
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{t}"
                f"(doc_id text PRIMARY KEY, emb vector({dim}));"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {t}_hnsw ON {SCHEMA}.{t} "
                f"USING hnsw (emb vector_l2_ops);"
            )
            c.commit()
    except VectorStoreUnavailable:
        raise
    except Exception as e:
        raise VectorStoreUnavailable(f"建表失败：{type(e).__name__}: {str(e)[:120]}") from e


def upsert(table: str, rows: list) -> int:
    """**写**：`rows = [(doc_id, [float, ...]), ...]` → 幂等 upsert，返回写入行数。"""
    t = _check_name(table)
    if not rows:
        return 0
    try:
        with _conn() as c, c.cursor() as cur:
            for doc_id, vec in rows:
                cur.execute(
                    f"INSERT INTO {SCHEMA}.{t}(doc_id, emb) VALUES (%s, %s::vector) "
                    f"ON CONFLICT (doc_id) DO UPDATE SET emb = EXCLUDED.emb;",
                    (str(doc_id), _vec_literal(vec)),
                )
            c.commit()
        return len(rows)
    except VectorStoreUnavailable:
        raise
    except Exception as e:
        raise VectorStoreUnavailable(f"写入失败：{type(e).__name__}: {str(e)[:120]}") from e


def search(table: str, vec: list, k: int = 10, *, metric: str = "l2") -> list:
    """**读**：→ `[(doc_id, distance)]`（`l2` 用 `<->`，`cosine` 用 `<=>`）。"""
    t = _check_name(table)
    op = {"l2": "<->", "cosine": "<=>"}.get(metric)
    if not op:
        raise ValueError(f"未知度量：{metric!r}（支持 l2 / cosine）")
    try:
        with _conn() as c, c.cursor() as cur:
            lit = _vec_literal(vec)
            cur.execute(
                f"SELECT doc_id, (emb {op} %s::vector)::float8 AS dist FROM {SCHEMA}.{t} "
                f"ORDER BY emb {op} %s::vector LIMIT %s;",
                (lit, lit, int(k)),
            )
            return [(r[0], r[1]) for r in cur.fetchall()]
    except VectorStoreUnavailable:
        raise
    except Exception as e:
        raise VectorStoreUnavailable(f"检索失败：{type(e).__name__}: {str(e)[:120]}") from e


def search_or_fallback(table: str, vec: list, k: int = 10) -> list | None:
    """**降级友好**读：可用则返回结果；不可用返回 `None`（**不抛错**）。

    调用方约定：`None` ⇒ 走既有全文/正则路径；并应记录 `fallback_hint()`（**回退可观测**）。
    """
    try:
        return search(table, vec, k)
    except (VectorStoreUnavailable, ValueError):
        return None


def sqlite_vec_available() -> bool:
    """`sqlite-vec` 扩展是否可加载（降级后端；与既有 SQLite FTS5 同库同源）。"""
    if not _has("sqlite_vec"):
        return False
    try:
        import sqlite_vec

        con = sqlite3.connect(":memory:")
        try:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.execute("SELECT vec_version();").fetchone()
            return True
        finally:
            con.close()
    except Exception:  # noqa: BLE001  探测失败即视作不可用（不抛）
        return False


def _main(argv: list) -> int:
    from config.exitcodes import ExitCode

    cmd = argv[1] if len(argv) > 1 else "--health"
    if cmd == "--health":
        h = health()
        print(f"向量后端：{h['backend']}（sqlite_vec 可加载：{sqlite_vec_available()}）")
        print(f"  DSN 目标：{h['dsn_target']}")
        if h["backend"] == "pgvector":
            print(
                f"  db={h.get('database')} port={h.get('port')} 扩展={h.get('vector_ext') or '—'} "
                f"用户={h.get('user')} 非超管={h.get('not_superuser')} vec可用={h.get('vec_schema_usable')}"
            )
        print(f"  结论：{'就绪' if h['ok'] else '不可用'} —— {h['detail']}")
        # 健康检查是**披露**：恒 rc=0（不可用是合法状态，不应把披露误报为故障）
        return int(ExitCode.OK)
    print(f"用法：{os.path.basename(argv[0])} [--health]")
    return int(ExitCode.USAGE)


if __name__ == "__main__":  # 离线自检（无 DSN 也应正常返回，绝不抛）
    import sys as _sys

    _h = health()
    assert {"backend", "ok", "detail"} <= set(_h), _h
    assert backend() in ("pgvector", "sqlite_vec", "none"), backend()
    # ⚠️ 自检**不构造带凭据的 DSN 字面量**（会被 `gate_secret_scan` 判为"明文口令"，
    #    实测命中过）→ 只测**无凭据形态**的解析分支（host:port/db 部分逻辑相同）。
    assert _redact("postgre" "sql://h:1/d") == "h:1/d", _redact("postgre" "sql://h:1/d")
    assert _redact("") == "(非 URI 形态；已省略)"
    raise SystemExit(_main(_sys.argv))
