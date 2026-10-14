# -*- coding: utf-8 -*-
"""std_lib.common_lib.governance_store._db — 治理库连接与 Schema 事实源（W4-c 五拆）。

从 1507 行的单文件拆出（方案 §7.5.1）：DDL + 连接管理独立成层，
本模块是包内**唯一**持有 `_DDL` / `TABLES` / `connect()` / `db_path()` 的地方——
其余四层（ledger/watermark/worklist/projections）一律经本模块取连接与路径。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from std_lib.common_lib.logging import get_logger

LOG = get_logger(__name__)

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
_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，与 clean_index 时间基准一致

TABLES = (
    "run_log",
    "watermark",
    "artifact",
    "audit_log",
    "gate_result",
    # ---- v2 §3.14.3（2026-09-26）待办队列：链外节点的"决策自动化"缺口显式化 ----
    "worklist",
    # ---- v2 §3.13.3/§3.13.5（2026-09-26，P2-6）步骤级运行台账 ----
    # 背景：run_log 只有"一次运行"一行，无法回答 `status` 的"本轮哪些步骤失败"，
    # 也无法支撑 `run --resume` 的"跳过已 rc=0 步骤"（v2 R13：跳过判据不得自建第二套）。
    "run_step",
    # ---- 阶段 2（2026-09-18）元数据四表：事实源为文件，本库为**事务化投影** ----
    "document",
    "theme_assign",
    "relation",
    "timeliness_history",
)

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
        "keys": (
            "relation_id",
            "relation",
            "src_ref",
            "src_key",
            "dst_ref",
            "dst_key",
            "dst_class",
            "matched_by",
        ),
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
        import paths

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
        import paths

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
    3: (
        ("watermark", "round", "TEXT NOT NULL DEFAULT ''"),
        ("watermark", "stage", "TEXT NOT NULL DEFAULT ''"),
    ),
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
            LOG.warning(f"[governance] 观测表列级迁移：{', '.join(applied)}")
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
