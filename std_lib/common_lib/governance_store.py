# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store — 治理库（governance.db）唯一读写实现

定位（见 `reports/数据流转与存储交互优化方案_20260917.md` §4）：
    本模块是 **阶段 1「治理库骨架」的唯一实现**，承载「三轨制」中的**治理轨**——
    只放"会被人改、会被跨模块读、需要历史"的**小体量元数据**（目标 < 50 MB），
    **不承载语料正文、原件、交付文档**（那些留在文件系统 = 内容轨）。

五张表（阶段 1 收敛范围）：
    run_log     —— 编排运行台账（每次 run 一行；失败/成功/耗时）
    watermark   —— 产物水位（**本次改造的核心**）：把「产物 ← 依赖」的隐式时序约束
                   变成机器可读的声明，供 gate_watermark 判「上游已推进、下游未重跑」
    artifact    —— 原件注册（路径集合 + inode + sha256 + 字节数）
                   ⚠️ 本项目 originals/ 与 corpus/ **跨层硬链接**（共享 inode）
                   → 同一份内容存**多路径**，故 `path_keys` 为 JSON 数组，禁按单路径建模
    audit_log   —— 受控修补审计（append-only；把"变更台账"从手工习惯升为技术留痕）
    gate_result —— 门禁运行结果（按 run_id 归档，支持历史对比）

设计纪律：
  1) **不硬编码绝对路径**：库路径经 `paths.DATA_DIR` 派生，环境变量
     `REG_ORCH_GOVERNANCE_DB` 可覆盖（测试隔离 / 非标准部署）。
  2) **失败不阻断主链**：本模块是**旁路观测设施**，任何写入异常在调用侧须降级为告警
     （编排/门禁的既有语义不因治理库故障而改变）。
  3) **WAL + busy_timeout**：读并发友好；写入经 sqlite 事务（跨表原子）。
  4) 主键/代理键**不可变**（RFN/IPN 教训）：本模块不重算任何现有代理键。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from std_lib.common_lib.io_atomic import sha256_file

__all__ = [
    "SCHEMA_VERSION", "TABLES", "db_path", "enabled", "connect", "init_db",
    "version_of_file", "file_version", "version_of_files", "now_iso",
    "run_start", "run_finish", "current_run_id", "list_runs",
    "record_watermark", "get_watermark", "list_watermarks", "dependency_edges",
    "check_dependencies",
    "log_audit", "list_audit",
    "upsert_artifacts", "list_artifacts",
    "record_gate_results", "list_gate_results",
    "snapshot",
]

SCHEMA_VERSION = "1.0"
_TZ = timezone(timedelta(hours=8))   # Asia/Shanghai，与 clean_index 时间基准一致

TABLES = ("run_log", "watermark", "artifact", "audit_log", "gate_result")

# 依赖键（inputs_json 的 key）命名约定：`<域>:<对象>`，与 watermark.artifact_key 同域。
#   cleaned:<src> / clauses:<src> / relations_index / internal_index / internal_processed
#   merged_view / published:external / published:internal / analysis:manifest
#   timeliness:state / rfn_attr / rfn_theme / clean_index
_DDL = """
CREATE TABLE IF NOT EXISTS run_log(
  run_id      TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  argv        TEXT NOT NULL DEFAULT '',
  ok          INTEGER,
  note        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS watermark(
  artifact_key   TEXT PRIMARY KEY,
  produced_by    TEXT NOT NULL,
  recorded_by    TEXT NOT NULL DEFAULT '',
  produced_at    TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  version        TEXT NOT NULL,
  inputs_json    TEXT NOT NULL DEFAULT '{}',
  record_count   INTEGER,
  run_id         TEXT
);
CREATE TABLE IF NOT EXISTS artifact(
  artifact_key  TEXT PRIMARY KEY,
  sha256        TEXT NOT NULL,
  bytes         INTEGER,
  kind          TEXT NOT NULL DEFAULT '',
  path_keys     TEXT NOT NULL DEFAULT '[]',
  inode         TEXT NOT NULL DEFAULT '',
  registered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_artifact_sha ON artifact(sha256);
CREATE TABLE IF NOT EXISTS audit_log(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  actor      TEXT NOT NULL DEFAULT '',
  action     TEXT NOT NULL,
  target     TEXT NOT NULL,
  target_key TEXT NOT NULL DEFAULT '',
  field      TEXT NOT NULL DEFAULT '',
  old_value  TEXT NOT NULL DEFAULT '',
  new_value  TEXT NOT NULL DEFAULT '',
  basis      TEXT NOT NULL DEFAULT '',
  run_id     TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_target ON audit_log(target, target_key);
CREATE TABLE IF NOT EXISTS gate_result(
  run_id      TEXT NOT NULL,
  gate        TEXT NOT NULL,
  descr       TEXT NOT NULL DEFAULT '',
  passed      INTEGER NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(run_id, gate)
);
"""


# --------------------------------------------------------------------------- #
# 路径与连接
# --------------------------------------------------------------------------- #
def repo_root() -> str:
    """仓库根：优先 `paths.ROOT`（唯一路径事实源），退化时按本文件上溯三级。"""
    try:
        import paths  # noqa: PLC0415
        return paths.ROOT
    except Exception:  # noqa: BLE001  非源码树/未注入 sys.path 时的保守回退
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def db_path() -> str:
    """治理库路径：`REG_ORCH_GOVERNANCE_DB` 覆盖 > `paths.DATA_DIR/governance.db`。

    位于**仓根 data/**（`.gitignore` 的 `data/` 规则已忽略）——与"数据不入 git"纪律一致：
    DB 是机器本地态，跨机审计链由 `exports/` 文本快照与 `reports/` 台账承载（§7 R1）。
    """
    env = os.environ.get("REG_ORCH_GOVERNANCE_DB", "").strip()
    if env:
        return os.path.abspath(env)
    try:
        import paths  # noqa: PLC0415
        return os.path.join(paths.DATA_DIR, "governance.db")
    except Exception:  # noqa: BLE001
        return os.path.join(repo_root(), "data", "governance.db")


def enabled() -> bool:
    """治理库是否已启用（库文件存在）。未启用时所有读接口返回空、写接口为 no-op。"""
    return os.path.exists(db_path())


def connect(readonly: bool = False) -> sqlite3.Connection:
    """打开治理库连接（WAL + busy_timeout + Row 工厂）。

    readonly=True 时以 `mode=ro` URI 打开（库不存在即抛 sqlite3.OperationalError）。
    """
    p = db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if readonly:
        conn = sqlite3.connect(f"file:{p.replace(os.sep, '/')}?mode=ro", uri=True, timeout=5)
    else:
        conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> str:
    """建库建表（幂等）。返回库路径。"""
    p = db_path()
    conn = connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()
    return p


def _ensure() -> sqlite3.Connection:
    """写路径统一入口：库/表不存在则先建（幂等），返回连接。"""
    conn = connect()
    try:
        conn.executescript(_DDL)
    except sqlite3.Error:
        pass
    return conn


def now_iso() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


# --------------------------------------------------------------------------- #
# 版本（水位可比单位）
# --------------------------------------------------------------------------- #
def version_of_file(path: str, *, short: int = 16) -> str:
    """文件内容版本 = sha256(字节) 前 N 位；文件缺失返回 ""（调用方据此跳过登记）。"""
    if not path or not os.path.exists(path):
        return ""
    return sha256_file(path)[:short]


def file_version(path: str, *, short: int = 16) -> str:
    """`version_of_file` 别名（语义更贴合 watermark.version 语境）。"""
    return version_of_file(path, short=short)


def version_of_files(paths_list) -> str:
    """多文件联合版本（如某源的 csv + jsonl 视为一体）：以各自 sha 拼接入哈希。

    全部文件缺失（或全为空版本）→ 返回 ""，调用方据此跳过登记。
    """
    parts = []
    for p in paths_list:
        v = version_of_file(p)
        if not v:
            continue
        parts.append(f"{os.path.basename(p)}={v}")
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# run_log
# --------------------------------------------------------------------------- #
def current_run_id() -> str:
    """当前 run_id：编排经 `REG_ORCH_RUN_ID` env 下传给子进程（gates/各阶段共用）。"""
    return os.environ.get("REG_ORCH_RUN_ID", "").strip()


def run_start(argv=None, note: str = "") -> str:
    """登记一次运行，返回 run_id（并**同时置入 os.environ** 供子进程继承）。"""
    rid = "RUN-" + datetime.now(_TZ).strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    os.environ["REG_ORCH_RUN_ID"] = rid
    conn = _ensure()
    try:
        conn.execute("INSERT OR REPLACE INTO run_log(run_id,started_at,argv,ok,note)"
                     " VALUES(?,?,?,NULL,?)",
                     (rid, now_iso(), " ".join(str(a) for a in (argv or [])), note))
        conn.commit()
    finally:
        conn.close()
    return rid


def run_finish(run_id: str, ok: bool, note: str = "") -> None:
    if not run_id:
        return
    conn = _ensure()
    try:
        conn.execute("UPDATE run_log SET finished_at=?, ok=?, note=? WHERE run_id=?",
                     (now_iso(), 1 if ok else 0, note, run_id))
        conn.commit()
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[dict]:
    if not enabled():
        return []
    with connect(readonly=True) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT ?", (int(limit),))]


# --------------------------------------------------------------------------- #
# watermark（核心）
# --------------------------------------------------------------------------- #
def record_watermark(artifact_key: str, produced_by: str, version: str, *,
                     inputs: dict | None = None, produced_at: str = "",
                     record_count: int | None = None,
                     schema_version: str = SCHEMA_VERSION,
                     recorded_by: str = "", run_id: str | None = None) -> dict | None:
    """登记/更新产物水位（幂等 upsert）。

    inputs: {依赖 artifact_key: 该依赖**登记时**的 version} —— 即"我是在哪个上游版本上算出来的"。
            调用方只应登记**当前实际可得**的依赖版本；取不到（如上游未登记）则不要放进 inputs，
            否则会造出"永远对不上"的假边（门禁会判陈旧）。
    返回登记后的行（dict）；`version` 为空（产物缺失）时**不写**并返回 None。
    """
    if not version:
        return None
    row = {
        "artifact_key": artifact_key,
        "produced_by": produced_by,
        "recorded_by": recorded_by or produced_by,
        "produced_at": produced_at or now_iso(),
        "schema_version": schema_version,
        "version": version,
        "inputs_json": json.dumps(inputs or {}, ensure_ascii=False, sort_keys=True),
        "record_count": record_count,
        "run_id": run_id if run_id is not None else current_run_id(),
    }
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO watermark(artifact_key,produced_by,recorded_by,produced_at,"
            "schema_version,version,inputs_json,record_count,run_id)"
            " VALUES(:artifact_key,:produced_by,:recorded_by,:produced_at,"
            ":schema_version,:version,:inputs_json,:record_count,:run_id)"
            " ON CONFLICT(artifact_key) DO UPDATE SET"
            " produced_by=excluded.produced_by, recorded_by=excluded.recorded_by,"
            " produced_at=excluded.produced_at, schema_version=excluded.schema_version,"
            " version=excluded.version, inputs_json=excluded.inputs_json,"
            " record_count=excluded.record_count, run_id=excluded.run_id",
            row)
        conn.commit()
    finally:
        conn.close()
    return row


def get_watermark(artifact_key: str) -> dict | None:
    if not enabled():
        return None
    with connect(readonly=True) as c:
        r = c.execute("SELECT * FROM watermark WHERE artifact_key=?",
                      (artifact_key,)).fetchone()
        return _wm_row(r) if r else None


def list_watermarks() -> list[dict]:
    if not enabled():
        return []
    with connect(readonly=True) as c:
        return [_wm_row(r) for r in c.execute("SELECT * FROM watermark ORDER BY artifact_key")]


def _wm_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["inputs"] = json.loads(d.get("inputs_json") or "{}")
    except ValueError:
        d["inputs"] = {}
    return d


def dependency_edges() -> list[dict]:
    """把 watermark 表展开为有向边列表（`reports/…方案_20260917.md` §1.4 的机器可读形态）。

    每行：{artifact, produced_by, version, dep, declared_version, current_version, status}
    status ∈ ok（版本一致）/ stale（上游已推进，产物未重跑）/ unregistered（上游未登记水位）。
    """
    wms = {w["artifact_key"]: w for w in list_watermarks()}
    edges: list[dict] = []
    for key, w in sorted(wms.items()):
        ins = w.get("inputs") or {}
        if not ins:
            continue
        for dep, declared in sorted(ins.items()):
            cur = (wms.get(dep) or {}).get("version", "")
            if not cur:
                status = "unregistered"
            elif str(cur) == str(declared):
                status = "ok"
            else:
                status = "stale"
            edges.append({
                "artifact": key, "produced_by": w.get("produced_by", ""),
                "version": w.get("version", ""),
                "dep": dep, "declared_version": declared, "current_version": cur,
                "status": status,
            })
    return edges


def check_dependencies() -> tuple[bool, dict]:
    """一致性判据（供 gate_watermark 消费）：任一边 stale 即 False。

    - `stale` → 阻断（上游已推进、产物未重跑 —— 正是 mtime 判据想抓却抓不准的情形）
    - `unregistered` / 无 inputs → 不阻断（过渡期覆盖度不足属预期，只在 detail 披露）
    """
    if not enabled():
        return True, {"enabled": False, "note": "治理库未启用（阶段 1 未运行），本判据跳过"}
    edges = dependency_edges()
    stale = [e for e in edges if e["status"] == "stale"]
    unreg = sorted({e["dep"] for e in edges if e["status"] == "unregistered"})
    wms = list_watermarks()
    bad_rows = [w["artifact_key"] for w in wms
                if not str(w.get("produced_at") or "").strip() or not str(w.get("version") or "").strip()]
    problems = []
    if stale:
        problems.append(
            "水位陈旧（上游已推进、产物未重跑）：" + "; ".join(
                f"{e['artifact']} 声明 {e['dep']}={e['declared_version']} "
                f"但当前 {e['dep']}={e['current_version']}"
                for e in stale[:5]) + f"（共 {len(stale)} 条）")
    if bad_rows:
        problems.append(f"水位行缺 produced_at/version：{bad_rows[:5]}")
    detail = {
        "enabled": True, "db": db_path(),
        "artifacts": len(wms), "edges": len(edges),
        "stale_edges": len(stale), "unregistered_deps": unreg,
        "problems": problems,
        "note": "判据=已登记产物的每条声明依赖边版本一致（unregistered 不阻断，仅披露）",
    }
    return (not problems), detail


# --------------------------------------------------------------------------- #
# audit_log（append-only）
# --------------------------------------------------------------------------- #
def log_audit(action: str, target: str, *, target_key: str = "", field: str = "",
              old: str = "", new: str = "", basis: str = "", actor: str = "",
              run_id: str | None = None, ts: str = "") -> None:
    """追加一条审计（受控修补留痕：只改目标字段 + 记录 old/new/basis）。"""
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO audit_log(ts,actor,action,target,target_key,field,old_value,"
            "new_value,basis,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ts or now_iso(), actor, action, target, target_key, field,
             str(old), str(new), basis, run_id if run_id is not None else current_run_id()))
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = 200, *, target: str = "", target_key: str = "") -> list[dict]:
    if not enabled():
        return []
    sql = "SELECT * FROM audit_log"
    where, args = [], []
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
            cur = conn.execute("SELECT path_keys FROM artifact WHERE artifact_key=?",
                               (key,)).fetchone()
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
                (key, sha, int(r.get("bytes") or 0), r.get("kind") or "",
                 json.dumps(sorted(keys), ensure_ascii=False),
                 str(r.get("inode") or ""), now_iso()))
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
                (run_id, r.get("module") or r.get("gate") or r.get("desc", ""),
                 r.get("desc", ""), 1 if r.get("passed") else 0,
                 json.dumps(r.get("detail", {}), ensure_ascii=False, default=str), ts))
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
# 快照（供 cli governance status / 导出）
# --------------------------------------------------------------------------- #
def snapshot() -> dict:
    """治理库概览（只读；未启用时 `enabled=False` 且各计数为 0）。"""
    if not enabled():
        return {"enabled": False, "db": db_path(),
                "note": "未启用：先运行 `python cli.py governance init`"}
    out: dict = {"enabled": True, "db": db_path(), "schema_version": SCHEMA_VERSION, "counts": {}}
    with connect(readonly=True) as c:
        for t in TABLES:
            out["counts"][t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        ok, det = check_dependencies()
        out["watermark_check"] = det
        out["runs"] = [dict(r) for r in c.execute(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT 5")]
    return out


if __name__ == "__main__":  # pragma: no cover - 手工冒烟
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
