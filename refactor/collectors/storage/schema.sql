-- ===========================================================================
-- refactor PostgreSQL 恢复用 DDL（灾备 / 从零重建）
-- ===========================================================================
-- 本文件是 refactor 存储层在 PostgreSQL 中创建的全部表结构，是**权威可重建副本**。
--
-- 单一事实源代码：refactor/collectors/storage/sql_backend.py
--   RUNS / ITEMS（SQLAlchemy Table 定义）+ SqlStore.init_schema()
--   （init_schema 还含旧 schema 兼容迁移：列名 source→namespace、补 key_md5/created_at；
--     那些 ALTER 仅用于升级老库，从零重建不需要，本文件只给干净的最终结构。）
--
-- 用法（PG 数据损坏/丢失时从零重建空表；数据需另行从 file 后端或备份恢复）：
--   psql "$REFACTOR_DB_URL" -f refactor/collectors/storage/schema.sql
-- 或已连库时：
--   \i refactor/collectors/storage/schema.sql
--
-- ⚠️ 同步纪律：sql_backend.py 中 RUNS/ITEMS 列定义变更时，本文件必须同步修改，
--    否则重建出的表与实际写入端不一致。
-- ===========================================================================

-- 批次记录：每次 save 生成一个 run（含"最新版本"语义）
CREATE TABLE IF NOT EXISTS master_runs (
    id           SERIAL       PRIMARY KEY,
    namespace    VARCHAR(64)  NOT NULL,
    captured_at  TIMESTAMPTZ  NOT NULL,
    count        INTEGER      NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_runs_namespace ON master_runs (namespace);

-- 数据主体：每条记录 = 一行，整条字典塞进 payload（JSON）
-- ⚠️ payload 实际类型为 JSON（非 JSONB；SQLAlchemy JSON 在 PG 下编译为 JSON）。
CREATE TABLE IF NOT EXISTS master_items (
    id           BIGSERIAL    PRIMARY KEY,
    run_id       INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace    VARCHAR(64)  NOT NULL,
    key_md5      VARCHAR(32)  NULL,
    url          TEXT         NULL,
    payload      JSON         NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_master_items_run_id     ON master_items (run_id);
CREATE INDEX IF NOT EXISTS ix_master_items_namespace ON master_items (namespace);
CREATE INDEX IF NOT EXISTS ix_master_items_key_md5   ON master_items (key_md5);

-- 去重键：同一 namespace 下按 url（归一 md5）去重。
-- key_md5 为 NULL 时 Postgres 视 NULL 互异 → 允许多行（无 URL 键的记录不被去重，
-- 与 SqlStore 写入语义一致）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_master_ns_key
    ON master_items (namespace, key_md5);

-- ===========================================================================
-- 关系边（最小图模型；与 Apache AGE 图查询共用同一份数据）
-- ---------------------------------------------------------------------------
-- 节点即 cleaned 文档（master_items）；外部法/机关无文档，用 name:<canonical> 作 id。
-- 每条边 = 两个端点（各含 id + name）+ 关系类型。其余抽取属性暂留 jsonl SSOT，
-- 后续按需在本表加列回填（PG 仅作可查询图，非事实源）。
-- AGE 图：顶点 = master_items(doc_id) ∪ rel_edges 外部端点；边 = 本表。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS rel_edges (
    edge_id   TEXT PRIMARY KEY,                      -- 复用抽取产物 relation_id（幂等去重）
    src_id    TEXT NOT NULL,                         -- 上节点 id：in-corpus=文档 id(RFN/IPN/dedup_key)
    src_name  TEXT NOT NULL,                         -- 上节点名，如《XX办法》
    dst_id    TEXT NOT NULL,                         -- 下节点 id：in-corpus=文档 id；外部/机关=name:<canonical>
    dst_name  TEXT NOT NULL,                         -- 下节点名，如《保险法》/ 国务院
    relation  TEXT NOT NULL                         -- basis | repeal
);
CREATE INDEX IF NOT EXISTS ix_rel_edges_src       ON rel_edges (src_id);
CREATE INDEX IF NOT EXISTS ix_rel_edges_dst       ON rel_edges (dst_id);
CREATE INDEX IF NOT EXISTS ix_rel_edges_relation  ON rel_edges (relation);

-- 可选：接入 Apache AGE 做 Cypher 图查询时，目标库先建扩展（仅一次）：
--   CREATE EXTENSION IF NOT EXISTS age;
