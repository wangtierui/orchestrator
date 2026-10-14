# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store.projections — 元数据投影层（W4-c 五拆）。

audit_log / artifact / document / theme_assign / relation / timeliness_history
的投影与校验（事实源为文件，本库为事务化投影）；快照导出同层。
连接与时间基准经 `._db`；指纹经 `io_atomic.sha256_file`（SSOT）。
"""

from __future__ import annotations

import hashlib
import json
import os

from ._db import (
    SCHEMA_VERSION,
    SYNC_TABLES,
    TABLES,
    _ensure,
    connect,
    db_path,
    enabled,
    now_iso,
    repo_root,
)
from .ledger import current_run_id, version_of_file
from .watermark import check_dependencies, list_watermarks


def log_audit(
    action: str,
    target: str,
    *,
    target_key: str = "",
    field: str = "",
    old: str = "",
    new: str = "",
    basis: str = "",
    actor: str = "",
    run_id: str | None = None,
    ts: str = "",
) -> None:
    """追加一条审计（受控修补留痕：只改目标字段 + 记录 old/new/basis）。"""
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,target_key,field,old_value,"
            "new_value,basis,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ts or now_iso(),
                actor,
                action,
                target,
                target_key,
                field,
                str(old),
                str(new),
                basis,
                run_id if run_id is not None else current_run_id(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = 200, *, target: str = "", target_key: str = "") -> list[dict]:
    if not enabled():
        return []
    sql = "SELECT * FROM audit_log"
    where: list[str] = []
    args: list[object] = []   # SQL 参数可为 str/int（如 LIMIT ? 的 int）
    if target:
        where.append("target=?")
        args.append(target)
    if target_key:
        where.append("target_key=?")
        args.append(target_key)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with connect(readonly=True) as c:
        return [dict(r) for r in c.execute(sql, args)]


# --------------------------------------------------------------------------- #
# artifact（原件注册；跨层硬链接 → path_keys 为集合）
# --------------------------------------------------------------------------- #
def upsert_artifacts(rows) -> int:
    """批量登记原件。rows: iterable of dict(artifact_key|sha256, path, kind, bytes, inode)。

    - artifact_key 缺省取 sha256[:16]
    - 同一 sha 命中既有行 → **并入 path_keys**（跨层硬链接：originals/ ↔ corpus/ 共享 inode），
      不新增行（这正是"删任一路径不丢数据"这一事实的表征）。
    """
    n = 0
    conn = _ensure()
    try:
        for r in rows:
            sha = (r.get("sha256") or "").strip()
            key = (r.get("artifact_key") or "").strip() or sha[:16]
            if not key:
                continue
            path = os.path.abspath(r.get("path") or "")
            cur = conn.execute(
                "SELECT path_keys FROM artifact WHERE artifact_key=?", (key,)
            ).fetchone()
            keys = []
            if cur:
                try:
                    keys = json.loads(cur["path_keys"] or "[]")
                except ValueError:
                    keys = []
            if path and path not in keys:
                keys.append(path)
            conn.execute(
                "INSERT INTO artifact(artifact_key,sha256,bytes,kind,path_keys,inode,registered_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(artifact_key) DO UPDATE SET"
                " sha256=excluded.sha256, bytes=excluded.bytes, kind=excluded.kind,"
                " path_keys=excluded.path_keys, inode=excluded.inode",
                (
                    key,
                    sha,
                    int(r.get("bytes") or 0),
                    r.get("kind") or "",
                    json.dumps(sorted(keys), ensure_ascii=False),
                    str(r.get("inode") or ""),
                    now_iso(),
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def list_artifacts(limit: int = 0, *, kind: str = "") -> list[dict]:
    if not enabled():
        return []
    sql = "SELECT * FROM artifact"
    args: list = []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY artifact_key"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    with connect(readonly=True) as c:
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            try:
                d["paths"] = json.loads(d.get("path_keys") or "[]")
            except ValueError:
                d["paths"] = []
            out.append(d)
        return out


# --------------------------------------------------------------------------- #
# gate_result
# --------------------------------------------------------------------------- #
def record_gate_results(run_id: str, results, *, recorded_at: str = "") -> int:
    """归档一次门禁运行的全部结果（同 run_id 幂等覆盖）。results: [{desc,passed,detail}]。"""
    if not run_id or not results:
        return 0
    conn = _ensure()
    n = 0
    try:
        ts = recorded_at or now_iso()
        for r in results:
            conn.execute(
                "INSERT OR REPLACE INTO gate_result(run_id,gate,descr,passed,detail_json,recorded_at)"
                " VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    r.get("module") or r.get("gate") or r.get("desc", ""),
                    r.get("desc", ""),
                    1 if r.get("passed") else 0,
                    json.dumps(r.get("detail", {}), ensure_ascii=False, default=str),
                    ts,
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def list_gate_results(run_id: str = "", limit: int = 100) -> list[dict]:
    if not enabled():
        return []
    sql = "SELECT * FROM gate_result"
    args: list = []
    if run_id:
        sql += " WHERE run_id=?"
        args.append(run_id)
    sql += " ORDER BY recorded_at DESC, gate LIMIT ?"
    args.append(int(limit))
    with connect(readonly=True) as c:
        return [dict(r) for r in c.execute(sql, args)]


# --------------------------------------------------------------------------- #
# 阶段 2：元数据四表写入/读取（投影器唯一入口 tools/governance_sync.py 调用）
# --------------------------------------------------------------------------- #
_DOC_COLS = (
    "doc_ref",
    "kind",
    "title",
    "docno",
    "docno_norm",
    "issue_organ",
    "publish_date",
    "effective_date",
    "source",
    "timeliness_status",
    "verification_source",
    "last_verified_at",
    "theme",
    "file_type",
    "extension",
    "origin_path",
    "body_len",
    "article_count",
    "row_json",
    "synced_at",
)
_THEME_COLS = ("doc_ref", "theme", "basis", "decided_at", "synced_at")
_REL_COLS = (
    "row_key",
    "relation_id",
    "relation",
    "src_kind",
    "src_ref",
    "src_key",
    "src_name",
    "src_docno",
    "src_source",
    "dst_kind",
    "dst_ref",
    "dst_key",
    "dst_class",
    "dst_name",
    "basis_type",
    "action",
    "scope",
    "matched_by",
    "confidence",
    "generated_at",
    "row_json",
    "synced_at",
)
_TH_COLS = (
    "state_key",
    "status",
    "prev_status",
    "replacement",
    "verification_source",
    "last_checked_at",
    "changed_at",
    "synced_at",
)


# 允许为 NULL 的数值列（其余列缺失一律归一为 ""，防 NOT NULL 约束把"源未提供"当成错误）
_NULLABLE = {"body_len", "article_count", "confidence", "source_offset"}


def _replace_table(conn, table: str, cols, rows) -> int:
    """全量替换（投影语义：库 = 文件那一刻的精确快照，不留孤儿行）。

    四表体量均为千级，全量替换代价可忽略；相较增量 upsert，它**从根本上消除
    "源已删/改名而库残留"这类静默分叉**——这正是双写期最需要防的失败模式。
    """
    conn.execute(f"DELETE FROM {table}")
    conn.executemany(
        f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [
            tuple(r.get(c) if (c in _NULLABLE or r.get(c) is not None) else "" for c in cols)
            for r in rows
        ],
    )
    return len(rows)


def project_metadata(
    *, documents, theme_assigns, relations, timeliness_rows, synced_at: str = ""
) -> dict:
    """一次事务内投影四表（全有或全无）。

    - `documents` / `theme_assigns` / `relations`：**全量替换**；
    - `timeliness_rows`：**追加 + 去重**（(state_key,status,last_checked_at) 唯一）——
      时效表的价值在"历史观测"，故不做全量替换。
    """
    ts = synced_at or now_iso()
    conn = _ensure()
    counts = {}
    try:
        conn.execute("BEGIN")
        counts["document"] = _replace_table(
            conn, "document", _DOC_COLS, [{**r, "synced_at": ts} for r in documents]
        )
        counts["theme_assign"] = _replace_table(
            conn, "theme_assign", _THEME_COLS, [{**r, "synced_at": ts} for r in theme_assigns]
        )
        counts["relation"] = _replace_table(
            conn, "relation", _REL_COLS, [{**r, "synced_at": ts} for r in relations]
        )
        before = conn.execute("SELECT COUNT(*) FROM timeliness_history").fetchone()[0]
        conn.executemany(
            f"INSERT OR IGNORE INTO timeliness_history({','.join(_TH_COLS)})"
            f" VALUES({','.join('?' * len(_TH_COLS))})",
            [tuple({**r, "synced_at": ts}.get(c) for c in _TH_COLS) for r in timeliness_rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM timeliness_history").fetchone()[0]
        counts["timeliness_history"] = after
        counts["timeliness_history_added"] = after - before
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def table_rows(table: str, limit: int = 0) -> list[dict]:
    """读投影表（`row_json` 自动解回原行，附于 `_src`）。"""
    if not enabled() or table not in SYNC_TABLES:
        return []
    sql = f"SELECT * FROM {table}"
    args: list = []
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    with connect(readonly=True) as c:
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            rj = d.pop("row_json", "")
            if rj:
                try:
                    d["_src"] = json.loads(rj)
                except ValueError:
                    d["_src"] = {}
            out.append(d)
        return out


def table_count(table: str) -> int:
    if not enabled():
        return 0
    with connect(readonly=True) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _canon_items(rows, keys) -> list[str]:
    return [
        "\x1f".join(str(r.get(k, "") if r.get(k) is not None else "") for k in keys) for r in rows
    ]


def _canon(rows, keys) -> str:
    """规范化摘要：按 keys 取值 → str 归一 → 排序 → sha256（库/文件一致性判据）。"""
    blob = "\x1e".join(sorted(_canon_items(rows, keys)))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def table_digest(table: str, rows=None) -> str:
    """表/行集的规范化摘要（`rows=None` 时取库内全量）。"""
    keys = SYNC_TABLES[table]["keys"]
    if rows is None:
        rows = table_rows(table)
    return _canon(rows, keys)


def verify_projection(*, documents, theme_assigns, relations, timeliness_rows) -> dict:
    """**比对断言**（双写期核心）：库内容 vs 由文件重算的内容，逐表比对。

    - `mode=replace`（document / theme_assign / relation）：**摘要相等**（库 = 源那一刻快照）；
    - `mode=append`（timeliness_history）：**源行须都在库中**（库是历史累积，不要求相等）。

    返回 {table: {db, src, ok, db_rows, src_rows[, missing]}}。不一致即"库与文件分叉"
    （须重跑 `governance sync`）——正是阶段 2 验收「连续 3 次生产刷新、断言 0 失败」所判的量。
    """
    if not enabled():
        return {}
    src = {
        "document": documents,
        "theme_assign": theme_assigns,
        "relation": relations,
        "timeliness_history": timeliness_rows,
    }
    out = {}
    for t, rows in src.items():
        spec = SYNC_TABLES[t]
        keys = spec["keys"]
        d = table_digest(t)
        s = _canon(rows, keys)
        item = {
            "db": d,
            "src": s,
            "db_rows": table_count(t),
            "src_rows": len(rows),
            "mode": spec["mode"],
        }
        if spec["mode"] == "append":
            have = set(_canon_items(table_rows(t), keys))
            missing = [x for x in _canon_items(rows, keys) if x not in have]
            item["ok"] = not missing
            item["missing"] = len(missing)
        else:
            item["ok"] = d == s
        out[t] = item
    return out


def export_snapshot(out_dir: str, *, tables=None) -> dict:
    """导出治理库文本快照 + manifest（交换轨；供人工 diff 与跨机审计）。

    只导出**小体量元数据表**（document / theme_assign / timeliness_history）；
    `relation`（7 MB 级）与语料正文不导出——它们已有各自的事实源文件。
    路径一律写**仓库相对 POSIX**（防 gate_hardcoded_paths 命中盘符字面量）。
    """
    if not enabled():
        return {"enabled": False}
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    names = list(tables or ("document", "theme_assign", "timeliness_history"))
    items = []
    for t in names:
        rows = table_rows(t)
        p = os.path.join(out_dir, f"governance_{t}.csv")
        cols = [c for c in (rows[0].keys() if rows else []) if c != "_src"]
        buf = [",".join(cols)]
        for r in rows:
            buf.append(",".join(_csv_cell(r.get(c)) for c in cols))
        # 经 fs_lock 原子写（与全仓原子写纪律一致）
        from std_lib.common_lib.fs_lock import atomic_write_text

        atomic_write_text(p, "\n".join(buf) + "\n", encoding="utf-8-sig")
        try:
            rel = os.path.relpath(p, repo_root()).replace("\\", "/")
        except ValueError:
            # 跨盘符（如测试 tmp_path 在 C:、仓库在 D:）→ 退化为文件名（仍不含盘符字面量）
            rel = os.path.basename(p)
        items.append(
            {
                "name": t,
                "path": rel,
                "rows": len(rows),
                "content_sha256": version_of_file(p, short=64),
                "keys": list(SYNC_TABLES[t]["keys"]),
            }
        )
    wms = {w["artifact_key"]: w["version"] for w in list_watermarks()}
    man = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "generator_run_id": current_run_id(),
        "note": "治理库元数据快照（只读交换层）。事实源仍为各模块文件；本目录为派生只读视图。",
        "watermarks": wms,
        "items": items,
    }
    mp = os.path.join(out_dir, "manifest.json")
    from std_lib.common_lib.fs_lock import atomic_write_json

    atomic_write_json(mp, man)
    return {"enabled": True, "out_dir": out_dir, "manifest": mp, "items": items}


def _csv_cell(v) -> str:
    s = "" if v is None else str(v)
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


# --------------------------------------------------------------------------- #
# 快照（供 cli governance status / 导出）
# --------------------------------------------------------------------------- #
def snapshot() -> dict:
    """治理库概览（只读；未启用时 `enabled=False` 且各计数为 0）。"""
    if not enabled():
        return {
            "enabled": False,
            "db": db_path(),
            "note": "未启用：先运行 `python cli.py governance init`",
        }
    out: dict = {"enabled": True, "db": db_path(), "schema_version": SCHEMA_VERSION, "counts": {}}
    with connect(readonly=True) as c:
        for t in TABLES:
            out["counts"][t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        ok, det = check_dependencies()
        out["watermark_check"] = det
        out["runs"] = [
            dict(r) for r in c.execute("SELECT * FROM run_log ORDER BY started_at DESC LIMIT 5")
        ]
    return out


if __name__ == "__main__":  # pragma: no cover - 手工冒烟
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
