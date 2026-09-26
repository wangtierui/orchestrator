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
    "SCHEMA_VERSION", "TABLES", "SYNC_TABLES", "db_path", "enabled", "connect", "init_db",
    "version_of_file", "file_version", "version_of_files", "now_iso",
    "run_start", "run_finish", "current_run_id", "list_runs",
    "record_watermark", "get_watermark", "list_watermarks", "dependency_edges",
    "check_dependencies",
    # v2 §3.14.3：待办队列
    "worklist_add", "worklist_list", "worklist_resolve", "worklist_stats",
    "log_audit", "list_audit",
    "upsert_artifacts", "list_artifacts",
    "record_gate_results", "list_gate_results",
    # 阶段 2：元数据投影四表
    "project_metadata", "table_rows", "table_count", "table_digest", "verify_projection",
    "export_snapshot",
    "snapshot",
]

SCHEMA_VERSION = "1.2"
# PRAGMA user_version 语义（只增不改）：
#   1 = 阶段 1 五表
#   2 = 阶段 2 增四表（relation 用 row_key）
#   3 = v2 §3.3.1：watermark 增 `round` / `stage`（观测表**只 ALTER 不 DROP**）
#   4 = v2 §3.14.3：增 `worklist` 待办队列表（P1-6）
# 注意：投影表（document/theme_assign/relation/timeliness_history）版本落后时**DROP 重建**；
#      观测表（run_log/watermark/artifact/audit_log/gate_result/worklist）一律 ALTER 增量迁移
#      （新增整表由 `_DDL` 的 CREATE TABLE IF NOT EXISTS 完成，无需迁移项）。
_SCHEMA_INT = 5
_TZ = timezone(timedelta(hours=8))   # Asia/Shanghai，与 clean_index 时间基准一致

TABLES = ("run_log", "watermark", "artifact", "audit_log", "gate_result",
          # ---- v2 §3.14.3（2026-09-26）待办队列：链外节点的"决策自动化"缺口显式化 ----
          "worklist",
          # ---- v2 §3.13.3/§3.13.5（2026-09-26，P2-6）步骤级运行台账 ----
          # 背景：run_log 只有"一次运行"一行，无法回答 `status` 的"本轮哪些步骤失败"，
          # 也无法支撑 `run --resume` 的"跳过已 rc=0 步骤"（v2 R13：跳过判据不得自建第二套）。
          "run_step",
          # ---- 阶段 2（2026-09-18）元数据四表：事实源为文件，本库为**事务化投影** ----
          "document", "theme_assign", "relation", "timeliness_history")

# 待办队列的**开放态唯一性**（v2 §3.14.3）：同 (kind, subject) 只允许一条 open，
# 使产生方可重复调用 `worklist_add` 而不产生重复待办（幂等）。
WORKLIST_OPEN_UNIQUE = ("kind", "subject")

# 阶段 2 四表的「事实源 → 表」映射与**比对断言键**（库/文件一致性判据）。
# 断言键取"身份 + 语义"字段：不含 generated_at/synced_at 等时间戳（它们天然每轮变化），
# 也不含可空文本（正文片段/reason），以保证断言**只对内容负责**。
SYNC_TABLES: dict[str, dict] = {
    # `mode`：`replace` = 库应等于源那一刻的精确快照（摘要相等）；
    #         `append`  = 库是源的历史累积（只要求源行**都在**库中，不要求相等）。
    "document": {
        "source": "classifier 归属表 + internal_policy_index.json",
        "keys": ("doc_ref", "kind", "title", "docno", "timeliness_status", "source", "theme"),
        "mode": "replace",
    },
    "theme_assign": {
        "source": "classifier 主题归属表",
        "keys": ("doc_ref", "theme"),
        "mode": "replace",
    },
    "relation": {
        "source": "relations_index.jsonl",
        "keys": ("relation_id", "relation", "src_ref", "src_key", "dst_ref", "dst_key",
                 "dst_class", "matched_by"),
        "mode": "replace",
    },
    "timeliness_history": {
        "source": "timeliness_review/verification_state.json",
        "keys": ("state_key", "status", "last_checked_at"),
        "mode": "append",
    },
}

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
  run_id         TEXT,
  -- v2 §3.3.1（_SCHEMA_INT 3）：轮次与阶段。`round` = 登记时的 run_id（同值即"同一轮"），
  -- `stage` = 产生该产物的阶段 id。用途：dependency_edges 的 `ok_within_round` 判据
  -- （同轮内"先算后用 + 上游被再次推进"不再误判 stale）。
  round          TEXT NOT NULL DEFAULT '',
  stage          TEXT NOT NULL DEFAULT ''
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

-- ================= worklist：待办队列（v2 §3.14.3，_SCHEMA_INT 4）=================
-- 设计：把"需要人处置、但系统不告诉人"的散落清单（CSV 台账 / *_pending 计数 / 人工台账）
-- 统一为可查询队列。写方 = 各产生方（kind 必须 ∈ config.enums.WORKLIST_KIND）；
-- 读方 = `cli.py worklist` / `cli.py governance status`。
-- 纪律：① open 项**不阻断门禁**（同 gate_rfn_drift 的存量治理口径）；
--      ② payload_json 存决策所需上下文，处置时无需二次查询；
--      ③ 同 (kind, subject) 的 open 项唯一（部分唯一索引）→ 产生方可幂等重复写入。
CREATE TABLE IF NOT EXISTS worklist(
  item_id      TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  stage        TEXT NOT NULL DEFAULT '',
  artifact_key TEXT NOT NULL DEFAULT '',
  round        TEXT NOT NULL DEFAULT '',
  subject      TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  suggestion   TEXT NOT NULL DEFAULT '',
  confidence   REAL,
  status       TEXT NOT NULL DEFAULT 'open',
  resolved_by  TEXT NOT NULL DEFAULT '',
  resolved_at  TEXT NOT NULL DEFAULT '',
  resolution   TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wl_status ON worklist(status);
CREATE INDEX IF NOT EXISTS ix_wl_kind   ON worklist(kind);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wl_open ON worklist(kind, subject) WHERE status='open';

-- ================= run_step：步骤级运行台账（v2 §3.13.3/§3.13.5，_SCHEMA_INT 5）=================
-- 设计：`run_log` 记录"一次运行"，本表记录"这次运行里每个步骤的 rc/耗时/是否被续跑跳过"。
-- 写方 = `tools/run_production_refresh._run`（每步一次 upsert）；读方 = `cli.py status`
-- （披露本轮失败步骤）、`cli.py run --resume`（跳过已 rc=0 步骤）。
-- 纪律：① 与水位同源判据（v2 R13：`--resume` 的跳过逻辑不得自建第二套判据）；
--      ② `skipped=1` 表示"因续跑而跳过"（非失败），rc 沿用上次的 0；
--      ③ 观测表 → 一律 ALTER/新建迁移，不清表。
CREATE TABLE IF NOT EXISTS run_step(
  run_id      TEXT NOT NULL,
  step        TEXT NOT NULL,
  rc          INTEGER NOT NULL DEFAULT -1,
  exit_code   TEXT NOT NULL DEFAULT '',
  elapsed_s   REAL NOT NULL DEFAULT 0,
  skipped     INTEGER NOT NULL DEFAULT 0,
  note        TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(run_id, step)
);
CREATE INDEX IF NOT EXISTS ix_step_run ON run_step(run_id);

-- ================= 阶段 2：元数据四表（2026-09-18）=================
-- 纪律：本四表是**事实源文件的投影**（文件仍为权威、读方仍读文件，双写期语义）。
-- 写入唯一入口 = tools/governance_sync.py；比对断言键见 SYNC_TABLES。
-- `row_json` 保留原始行，供消费方零损耗取回全部字段（避免类型往返失真）。
CREATE TABLE IF NOT EXISTS document(
  doc_ref           TEXT PRIMARY KEY,     -- RFN-<16hex>（监管） / IPN-<16hex>（内部）
  kind              TEXT NOT NULL CHECK(kind IN ('regulatory','internal')),
  title             TEXT NOT NULL DEFAULT '',
  docno             TEXT NOT NULL DEFAULT '',
  docno_norm        TEXT NOT NULL DEFAULT '',
  issue_organ       TEXT NOT NULL DEFAULT '',
  publish_date      TEXT NOT NULL DEFAULT '',
  effective_date    TEXT NOT NULL DEFAULT '',
  source            TEXT NOT NULL DEFAULT '',
  timeliness_status TEXT NOT NULL DEFAULT '',
  verification_source TEXT NOT NULL DEFAULT '',
  last_verified_at  TEXT NOT NULL DEFAULT '',
  theme             TEXT NOT NULL DEFAULT '',
  file_type         TEXT NOT NULL DEFAULT '',
  extension         TEXT NOT NULL DEFAULT '',
  origin_path       TEXT NOT NULL DEFAULT '',
  body_len          INTEGER,
  article_count     INTEGER,
  row_json          TEXT NOT NULL DEFAULT '{}',
  synced_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_doc_norm  ON document(docno_norm);
CREATE INDEX IF NOT EXISTS ix_doc_kind  ON document(kind);
CREATE INDEX IF NOT EXISTS ix_doc_title ON document(title);
CREATE INDEX IF NOT EXISTS ix_doc_theme ON document(theme);

CREATE TABLE IF NOT EXISTS theme_assign(
  doc_ref    TEXT PRIMARY KEY,
  theme      TEXT NOT NULL DEFAULT '',
  basis      TEXT NOT NULL DEFAULT '',
  decided_at TEXT NOT NULL DEFAULT '',
  synced_at  TEXT NOT NULL DEFAULT ''
);

-- ⚠️ 主键为**合成行键** `row_key`，**不是** `relation_id`（纵深防御，保留）：
-- 实测（2026-09-18）`relations_index.jsonl` 5266 行仅有 4859 个不同 `relation_id`
-- （366 个 id 命中 2 次、共 407 行内容互不相同）——`relation_id` 在事实源中
-- **并非唯一**（与 contract 注释"稳定去重键"的实际语义有出入，已登记为待治理项）。
-- 若以 relation_id 作主键会**静默丢弃 407 行**并污染下游读取，故改合成键保全全部行，
-- `relation_id` 降为普通索引列。
CREATE TABLE IF NOT EXISTS relation(
  row_key      TEXT PRIMARY KEY,
  relation_id  TEXT NOT NULL DEFAULT '',
  relation     TEXT NOT NULL DEFAULT '',
  src_kind     TEXT NOT NULL DEFAULT '',
  src_ref      TEXT NOT NULL DEFAULT '',
  src_key      TEXT NOT NULL DEFAULT '',
  src_name     TEXT NOT NULL DEFAULT '',
  src_docno    TEXT NOT NULL DEFAULT '',
  src_source   TEXT NOT NULL DEFAULT '',
  dst_kind     TEXT NOT NULL DEFAULT '',
  dst_ref      TEXT NOT NULL DEFAULT '',
  dst_key      TEXT NOT NULL DEFAULT '',
  dst_class    TEXT NOT NULL DEFAULT '',
  dst_name     TEXT NOT NULL DEFAULT '',
  basis_type   TEXT NOT NULL DEFAULT '',
  action       TEXT NOT NULL DEFAULT '',
  scope        TEXT NOT NULL DEFAULT '',
  matched_by   TEXT NOT NULL DEFAULT '',
  confidence   REAL,
  generated_at TEXT NOT NULL DEFAULT '',
  row_json     TEXT NOT NULL DEFAULT '{}',
  synced_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_rel_rid   ON relation(relation_id);
CREATE INDEX IF NOT EXISTS ix_rel_src   ON relation(src_ref);
CREATE INDEX IF NOT EXISTS ix_rel_srck  ON relation(src_key);
CREATE INDEX IF NOT EXISTS ix_rel_dst   ON relation(dst_ref);
CREATE INDEX IF NOT EXISTS ix_rel_dstk  ON relation(dst_key);
CREATE INDEX IF NOT EXISTS ix_rel_kind  ON relation(relation);
CREATE INDEX IF NOT EXISTS ix_rel_class ON relation(dst_class);

CREATE TABLE IF NOT EXISTS timeliness_history(
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  state_key           TEXT NOT NULL DEFAULT '',   -- doc:<归一化文号> / title:<标题前40字>
  status              TEXT NOT NULL DEFAULT '',
  prev_status         TEXT NOT NULL DEFAULT '',
  replacement         TEXT NOT NULL DEFAULT '',
  verification_source TEXT NOT NULL DEFAULT '',
  last_checked_at     TEXT NOT NULL DEFAULT '',
  changed_at          TEXT NOT NULL DEFAULT '',
  synced_at           TEXT NOT NULL DEFAULT '',
  UNIQUE(state_key, status, last_checked_at)      -- 幂等：重复投影不追加重复观测
);
CREATE INDEX IF NOT EXISTS ix_th_key ON timeliness_history(state_key);
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


# --------------------------------------------------------------------------- #
# 观测表列级迁移（v2 §3.3.1）
# --------------------------------------------------------------------------- #
# 语义：观测表承载历史、**不可 DROP 重建**，结构演进只能 `ALTER TABLE ADD COLUMN`。
# 键 = 目标 `_SCHEMA_INT`；值 = ((表, 列, 列定义), ...)。新增版本时**只追加**，不改历史项。
_OBSERVATION_MIGRATIONS: dict[int, tuple[tuple[str, str, str], ...]] = {
    # 3 = v2 §3.3.1：watermark 增 round/stage（供 dependency_edges 的 ok_within_round 判据）
    3: (("watermark", "round", "TEXT NOT NULL DEFAULT ''"),
        ("watermark", "stage", "TEXT NOT NULL DEFAULT ''")),
}


def _migrate_observations(conn: sqlite3.Connection, cur: int) -> list[str]:
    """按 `_OBSERVATION_MIGRATIONS` 补齐观测表缺失列（幂等：先查 `PRAGMA table_info`）。"""
    applied: list[str] = []
    for ver in sorted(v for v in _OBSERVATION_MIGRATIONS if v > cur):
        for table, col, ddl in _OBSERVATION_MIGRATIONS[ver]:
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                continue
            if not cols or col in cols:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                applied.append(f"{table}.{col}")
            except sqlite3.Error:
                continue
    return applied


def init_db() -> str:
    """建库建表（幂等，含投影表 schema 升级 + 观测表列级迁移）。返回库路径。

    schema 版本经 `PRAGMA user_version` 记录（当前 `_SCHEMA_INT`）：版本落后时
    **只 DROP 四张投影表**再重建——投影表是纯派生数据（文件仍是事实源），
    丢弃零代价；而 run_log/watermark/artifact/audit_log/gate_result 五张**观测表
    一律保留**（它们承载历史，不可重建），其结构演进走 `_migrate_observations()`
    的 `ALTER TABLE ADD COLUMN`（v2 §3.3.1）。
    """
    p = db_path()
    conn = connect()
    try:
        cur = conn.execute("PRAGMA user_version").fetchone()[0]
        if cur < _SCHEMA_INT:
            for t in ("document", "theme_assign", "relation", "timeliness_history"):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(_DDL)
        applied = _migrate_observations(conn, cur)
        conn.execute(f"PRAGMA user_version={_SCHEMA_INT}")
        conn.commit()
        if applied:
            print(f"[governance] 观测表列级迁移：{', '.join(applied)}")
    finally:
        conn.close()
    return p


def _ensure() -> sqlite3.Connection:
    """写路径统一入口：库/表不存在则先建（幂等）+ 观测表列级迁移，返回连接。"""
    conn = connect()
    try:
        conn.executescript(_DDL)
    except sqlite3.Error:
        pass
    try:
        cur = conn.execute("PRAGMA user_version").fetchone()[0]
        if _migrate_observations(conn, cur):
            conn.execute(f"PRAGMA user_version={_SCHEMA_INT}")
        conn.commit()
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


def step_record(step: str, rc: int, *, exit_code: str = "", elapsed_s: float = 0,
                skipped: bool = False, note: str = "", run_id: str | None = None) -> None:
    """登记一个步骤的结果（**幂等 upsert**：同 (run_id, step) 覆盖）。

    由 `tools/run_production_refresh._run` 调用；治理库未启用时静默返回（不中断主链）。
    """
    if not enabled() or not step:
        return
    rid = run_id if run_id is not None else current_run_id()
    if not rid:
        return
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO run_step(run_id,step,rc,exit_code,elapsed_s,skipped,note,recorded_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(run_id,step) DO UPDATE SET rc=excluded.rc,"
            " exit_code=excluded.exit_code, elapsed_s=excluded.elapsed_s,"
            " skipped=excluded.skipped, note=excluded.note, recorded_at=excluded.recorded_at",
            (rid, step, int(rc), exit_code, float(elapsed_s), 1 if skipped else 0,
             note, now_iso()))
        conn.commit()
    except sqlite3.Error as e:
        print(f"[run_step] WARN 登记失败（不影响主链）: {type(e).__name__}: {e}")
    finally:
        conn.close()


def steps_of(run_id: str) -> list[dict]:
    """某次运行的步骤表（按 step 排序）。表未建（旧库未迁移）→ 空列表（读侧容错）。"""
    if not enabled() or not run_id:
        return []
    try:
        with connect(readonly=True) as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM run_step WHERE run_id=? ORDER BY step", (run_id,))]
    except sqlite3.Error as e:
        # 旧库（_SCHEMA_INT < 5）未迁移 → 读侧不得因此失败（写侧 init_db 会补建表）
        print(f"[run_step] WARN 步骤表不可读（跑 `cli.py governance init` 迁移）: {e}")
        return []


def last_run_with_steps() -> tuple[str, list[dict]]:
    """最近一次**有步骤记录**的运行 → (run_id, steps)。无则 ("", [])。"""
    if not enabled():
        return "", []
    try:
        with connect(readonly=True) as c:
            row = c.execute("SELECT run_id FROM run_step GROUP BY run_id"
                            " ORDER BY MAX(recorded_at) DESC LIMIT 1").fetchone()
            if not row:
                return "", []
            rid = row["run_id"]
    except sqlite3.Error as e:
        print(f"[run_step] WARN 步骤表不可读（跑 `cli.py governance init` 迁移）: {e}")
        return "", []
    return rid, steps_of(rid)


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
                     recorded_by: str = "", run_id: str | None = None,
                     round_id: str | None = None, stage: str = "") -> dict | None:
    """登记/更新产物水位（幂等 upsert）。

    inputs: {依赖 artifact_key: 该依赖**登记时**的 version} —— 即"我是在哪个上游版本上算出来的"。
            调用方只应登记**当前实际可得**的依赖版本；取不到（如上游未登记）则不要放进 inputs，
            否则会造出"永远对不上"的假边（门禁会判陈旧）。
    round_id: 轮次标识（列名 `round`，取 `run_id`）；同值即"同一轮"，供
            `dependency_edges` 的 `ok_within_round` 判据使用（v2 §3.3.1）。
    stage:    产生该产物的阶段 id（如 `"2.6"`）；仅审计与排障用，不参与判据。

    **校验收紧（v2 §3.3.1，2026-09-26）**：`produced_by` 为空即**拒绝写入**并告警
    （原实现允许任意字符串入库，导致"谁产的"不可追溯）；此时返回 None。
    返回登记后的行（dict）；`version` 为空（产物缺失）时**不写**并返回 None。
    """
    if not version:
        return None
    if not str(produced_by or "").strip():
        print(f"[governance] WARN 拒绝登记 {artifact_key}：produced_by 为空"
              "（水位必须可追溯到唯一产出方）")
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
        "round": round_id if round_id is not None else current_run_id(),
        "stage": stage,
    }
    conn = _ensure()
    try:
        conn.execute(
            "INSERT INTO watermark(artifact_key,produced_by,recorded_by,produced_at,"
            "schema_version,version,inputs_json,record_count,run_id,round,stage)"
            " VALUES(:artifact_key,:produced_by,:recorded_by,:produced_at,"
            ":schema_version,:version,:inputs_json,:record_count,:run_id,:round,:stage)"
            " ON CONFLICT(artifact_key) DO UPDATE SET"
            " produced_by=excluded.produced_by, recorded_by=excluded.recorded_by,"
            " produced_at=excluded.produced_at, schema_version=excluded.schema_version,"
            " version=excluded.version, inputs_json=excluded.inputs_json,"
            " record_count=excluded.record_count, run_id=excluded.run_id,"
            " round=excluded.round, stage=excluded.stage",
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

    每行：{artifact, produced_by, version, round, dep, declared_version, current_version,
           dep_round, status}
    **四态判据**（v2 §3.3.1，2026-09-26 增第 4 态）：
      - `ok`              ：声明版本 == 当前版本；
      - `ok_within_round` ：版本不一致，但上游与本产物**同一轮**（`round` 相同且非空）
                            —— 链内"先算后用 + 同轮内上游被再次推进"属正常顺序特性
                            （典型：`clauses:{src}` 在阶段 1 登记，`cleaned:{src}` 在
                            阶段 2 时效回写后才重新登记），不再误判为陈旧；
      - `stale`           ：上游在**更晚轮次**推进，或双方轮次不可比 → 阻断；
      - `unregistered`    ：上游未登记水位 → 披露不阻断。
    """
    wms = {w["artifact_key"]: w for w in list_watermarks()}
    edges: list[dict] = []
    for key, w in sorted(wms.items()):
        ins = w.get("inputs") or {}
        if not ins:
            continue
        a_round = str(w.get("round") or "")
        for dep, declared in sorted(ins.items()):
            dep_w = wms.get(dep) or {}
            cur = dep_w.get("version", "")
            dep_round = str(dep_w.get("round") or "")
            if not cur:
                status = "unregistered"
            elif str(cur) == str(declared):
                status = "ok"
            elif a_round and dep_round and a_round == dep_round:
                status = "ok_within_round"
            else:
                status = "stale"
            edges.append({
                "artifact": key, "produced_by": w.get("produced_by", ""),
                "version": w.get("version", ""), "round": a_round,
                "dep": dep, "declared_version": declared, "current_version": cur,
                "dep_round": dep_round,
                "status": status,
            })
    return edges


def check_dependencies() -> tuple[bool, dict]:
    """一致性判据（供 gate_watermark 消费）：任一边 stale 即 False。

    - `stale` → 阻断（上游已推进、产物未重跑 —— 正是 mtime 判据想抓却抓不准的情形）
    - `ok_within_round` → **不阻断**（同轮顺序特性；计数在 detail 披露）
    - `unregistered` / 无 inputs → 不阻断（过渡期覆盖度不足属预期，只在 detail 披露）
    """
    if not enabled():
        return True, {"enabled": False, "note": "治理库未启用（阶段 1 未运行），本判据跳过"}
    edges = dependency_edges()
    stale = [e for e in edges if e["status"] == "stale"]
    within = [e for e in edges if e["status"] == "ok_within_round"]
    unreg = sorted({e["dep"] for e in edges if e["status"] == "unregistered"})
    wms = list_watermarks()
    bad_rows = [w["artifact_key"] for w in wms
                if not str(w.get("produced_at") or "").strip() or not str(w.get("version") or "").strip()]
    no_round = [w["artifact_key"] for w in wms if not str(w.get("round") or "").strip()]
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
        "stale_edges": len(stale),
        "within_round_edges": len(within),
        "unregistered_deps": unreg,
        "problems": problems,
        "note": "判据=已登记产物的每条声明依赖边版本一致；`ok_within_round`（同轮顺序特性）与 "
                "`unregistered`（覆盖度不足）均不阻断，仅在 detail 披露",
    }
    if within:
        detail["within_round_deps"] = sorted({f"{e['artifact']}<-{e['dep']}" for e in within})
    if no_round and wms:
        # 过渡期披露：迁移前登记的历史水位缺 round，无法参与同轮判据（会按 stale 处理）
        detail["rows_without_round"] = len(no_round)
    return (not problems), detail


# --------------------------------------------------------------------------- #
# worklist（待办队列，v2 §3.14.3 / P1-6）
# --------------------------------------------------------------------------- #
def _wl_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["payload"] = json.loads(d.get("payload_json") or "{}")
    except ValueError:
        d["payload"] = {}
    return d


def worklist_add(kind: str, subject: str, *, stage: str = "", artifact_key: str = "",
                 payload: dict | None = None, suggestion: str = "",
                 confidence: float | None = None, round_id: str | None = None,
                 item_id: str = "") -> str | None:
    """登记一条待办（**幂等**：同 `(kind, subject)` 的 open 项已存在则更新上下文并复用 id）。

    kind      : 必须 ∈ `config.enums.WORKLIST_KIND`。本模块做**软校验**——不在集合内仅打印
                告警仍写入（保持治理库与 config 解耦）；硬断言由 `gate_config_integrity` 承担。
    subject   : 目标标识（RFN / IPN / dedup_key / 冲突键 / 文件路径）——与 kind 共同构成 open 唯一键。
    payload   : 决策所需上下文（建议 / 票数 / 置信度 / 冲突双方 / change_trace…）。
    suggestion: 机器建议（可为空）；confidence: 建议置信度。

    返回 item_id；治理库未启用或写入异常时返回 None（**调用方不得因此中断**）。
    """
    if not kind or not subject:
        return None
    try:
        from config.enums import WORKLIST_KIND  # noqa: PLC0415
        if kind not in WORKLIST_KIND:
            print(f"[worklist] WARN 未登记的 kind={kind!r}（应加入 config.enums.WORKLIST_KIND）")
    except Exception:  # noqa: BLE001  非源码树/未注入 sys.path：跳过软校验
        pass
    if not enabled():
        return None

    rid = round_id if round_id is not None else current_run_id()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    conn = _ensure()
    try:
        existing = conn.execute(
            "SELECT item_id FROM worklist WHERE kind=? AND subject=? AND status='open'",
            (kind, subject)).fetchone()
        if existing:
            # 幂等刷新：上下文可能随数据变化（票数/冲突双方），但保留 created_at 与 item_id
            conn.execute(
                "UPDATE worklist SET stage=?, artifact_key=?, round=?, payload_json=?,"
                " suggestion=?, confidence=? WHERE item_id=?",
                (stage, artifact_key, rid, payload_json, suggestion, confidence,
                 existing["item_id"]))
            conn.commit()
            return existing["item_id"]

        # item_id 需在"同一秒内处置后重新登记"时仍唯一 → 带毫秒 + 冲突自增后缀。
        # （曾被单测检出：秒级时间戳 + 同 pid + 同 kind|subject 哈希 → 重开时主键冲突）
        base = ("WL-" + datetime.now(_TZ).strftime("%Y%m%d%H%M%S%f")[:-3]
                + f"-{os.getpid()}-"
                + hashlib.sha256(f"{kind}|{subject}".encode()).hexdigest()[:6])
        iid = item_id or base
        _n = 1
        while conn.execute("SELECT 1 FROM worklist WHERE item_id=?", (iid,)).fetchone():
            _n += 1
            iid = f"{base}-{_n}"
        conn.execute(
            "INSERT INTO worklist(item_id,kind,stage,artifact_key,round,subject,payload_json,"
            "suggestion,confidence,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?, 'open', ?)",
            (iid, kind, stage, artifact_key, rid, subject, payload_json, suggestion,
             confidence, now_iso()))
        conn.commit()
        return iid
    except sqlite3.Error as e:
        print(f"[worklist] WARN 登记失败（不影响主链）: {type(e).__name__}: {e}")
        return None
    finally:
        conn.close()


def worklist_list(*, status: str = "open", kind: str = "", limit: int = 0) -> list[dict]:
    """列出待办（默认仅 open）；`status=""` 表示全部状态。"""
    if not enabled():
        return []
    sql = "SELECT * FROM worklist"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at, kind, subject"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with connect(readonly=True) as c:
        return [_wl_row(r) for r in c.execute(sql, args)]


def worklist_resolve(item_id: str, resolution: str, *, by: str = "",
                     status: str = "resolved") -> bool:
    """处置一条待办（`resolved` = 已裁决；`dismissed` = 判为无需处置）。

    返回是否命中并更新（未命中返回 False，调用方据此提示）。
    """
    if not enabled() or not item_id:
        return False
    if status not in ("resolved", "dismissed"):
        raise ValueError(f"非法 status={status!r}（仅 resolved / dismissed）")
    conn = _ensure()
    try:
        cur = conn.execute(
            "UPDATE worklist SET status=?, resolved_by=?, resolved_at=?, resolution=?"
            " WHERE item_id=? AND status='open'",
            (status, by or current_run_id() or "cli", now_iso(), resolution, item_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def worklist_stats() -> dict:
    """待办统计（供 `cli.py status` / 告警联动）：按 kind 计数 + 最老 open 滞留天数。"""
    if not enabled():
        return {"enabled": False, "open": 0, "by_kind": {}}
    rows = worklist_list(status="open")
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    oldest_days = 0
    if rows:
        try:
            first = min(r["created_at"] for r in rows)
            t0 = datetime.strptime(first[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_TZ)
            oldest_days = max(0, (datetime.now(_TZ) - t0).days)
        except (ValueError, TypeError):
            oldest_days = 0
    return {"enabled": True, "open": len(rows), "by_kind": dict(sorted(by_kind.items())),
            "oldest_open_days": oldest_days,
            "all": {s: len(worklist_list(status=s)) for s in ("resolved", "dismissed")}}


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
# 阶段 2：元数据四表写入/读取（投影器唯一入口 tools/governance_sync.py 调用）
# --------------------------------------------------------------------------- #
_DOC_COLS = ("doc_ref", "kind", "title", "docno", "docno_norm", "issue_organ", "publish_date",
             "effective_date", "source", "timeliness_status", "verification_source",
             "last_verified_at", "theme", "file_type", "extension", "origin_path",
             "body_len", "article_count", "row_json", "synced_at")
_THEME_COLS = ("doc_ref", "theme", "basis", "decided_at", "synced_at")
_REL_COLS = ("row_key", "relation_id", "relation", "src_kind", "src_ref", "src_key", "src_name",
             "src_docno", "src_source", "dst_kind", "dst_ref", "dst_key", "dst_class", "dst_name",
             "basis_type", "action", "scope", "matched_by", "confidence", "generated_at",
             "row_json", "synced_at")
_TH_COLS = ("state_key", "status", "prev_status", "replacement", "verification_source",
            "last_checked_at", "changed_at", "synced_at")


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
        [tuple(r.get(c) if (c in _NULLABLE or r.get(c) is not None) else ""
               for c in cols) for r in rows])
    return len(rows)


def project_metadata(*, documents, theme_assigns, relations, timeliness_rows,
                     synced_at: str = "") -> dict:
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
            conn, "document", _DOC_COLS,
            [{**r, "synced_at": ts} for r in documents])
        counts["theme_assign"] = _replace_table(
            conn, "theme_assign", _THEME_COLS,
            [{**r, "synced_at": ts} for r in theme_assigns])
        counts["relation"] = _replace_table(
            conn, "relation", _REL_COLS,
            [{**r, "synced_at": ts} for r in relations])
        before = conn.execute("SELECT COUNT(*) FROM timeliness_history").fetchone()[0]
        conn.executemany(
            f"INSERT OR IGNORE INTO timeliness_history({','.join(_TH_COLS)})"
            f" VALUES({','.join('?' * len(_TH_COLS))})",
            [tuple({**r, "synced_at": ts}.get(c) for c in _TH_COLS) for r in timeliness_rows])
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
    return ["\x1f".join(str(r.get(k, "") if r.get(k) is not None else "") for k in keys)
            for r in rows]


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
    src = {"document": documents, "theme_assign": theme_assigns,
           "relation": relations, "timeliness_history": timeliness_rows}
    out = {}
    for t, rows in src.items():
        spec = SYNC_TABLES[t]
        keys = spec["keys"]
        d = table_digest(t)
        s = _canon(rows, keys)
        item = {"db": d, "src": s, "db_rows": table_count(t), "src_rows": len(rows),
                "mode": spec["mode"]}
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
        from std_lib.common_lib.fs_lock import atomic_write_text  # noqa: PLC0415
        atomic_write_text(p, "\n".join(buf) + "\n", encoding="utf-8-sig")
        try:
            rel = os.path.relpath(p, repo_root()).replace("\\", "/")
        except ValueError:
            # 跨盘符（如测试 tmp_path 在 C:、仓库在 D:）→ 退化为文件名（仍不含盘符字面量）
            rel = os.path.basename(p)
        items.append({"name": t, "path": rel, "rows": len(rows),
                      "content_sha256": version_of_file(p, short=64),
                      "keys": list(SYNC_TABLES[t]["keys"])})
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
    from std_lib.common_lib.fs_lock import atomic_write_json  # noqa: PLC0415
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
