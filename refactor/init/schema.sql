-- ===========================================================================
-- refactor PostgreSQL 恢复用 DDL（灾备 / 从零重建）
-- 本文件由 `python -m refactor.init` 自动生成，勿手工编辑；
-- 权威来源：refactor/init/ddl.py
-- 用法：psql "$REFACTOR_DB_URL" -f refactor/init/schema.sql
-- ===========================================================================

-- 安装扩展（仅一次）
CREATE EXTENSION IF NOT EXISTS age;


CREATE TABLE IF NOT EXISTS master_runs (
    id           SERIAL       PRIMARY KEY,
    namespace    VARCHAR(64)  NOT NULL,
    captured_at  TIMESTAMPTZ  NOT NULL,
    count        INTEGER      NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_runs_namespace ON master_runs (namespace);

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
CREATE UNIQUE INDEX IF NOT EXISTS uq_master_ns_key
    ON master_items (namespace, key_md5);

CREATE TABLE IF NOT EXISTS docgraph_edges (
    edge_id   TEXT PRIMARY KEY,                      -- 复用抽取产物 relation_id（幂等去重）
    src_id    TEXT NOT NULL,                         -- 上节点 id
    src_name  TEXT NOT NULL,                         -- 上节点名，如《XX办法》
    dst_id    TEXT NOT NULL,                         -- 下节点 id；外部/机关=name:<canonical>
    dst_name  TEXT NOT NULL,                         -- 下节点名，如《保险法》/ 国务院
    relation  TEXT NOT NULL                         -- basis | repeal
);
CREATE INDEX IF NOT EXISTS ix_docgraph_edges_src       ON docgraph_edges (src_id);
CREATE INDEX IF NOT EXISTS ix_docgraph_edges_dst       ON docgraph_edges (dst_id);
CREATE INDEX IF NOT EXISTS ix_docgraph_edges_relation  ON docgraph_edges (relation);

