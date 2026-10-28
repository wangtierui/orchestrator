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
    meta         JSONB        NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_runs_namespace ON master_runs (namespace);

CREATE TABLE IF NOT EXISTS raw_items (
    id           BIGSERIAL    PRIMARY KEY,
    run_id       INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace    VARCHAR(64)  NOT NULL,
    key_md5      VARCHAR(32)  NULL,
    url          TEXT         NULL,
    payload      JSON         NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_items_run_id     ON raw_items (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_items_namespace ON raw_items (namespace);
CREATE INDEX IF NOT EXISTS ix_raw_items_key_md5   ON raw_items (key_md5);
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_items_ns_key
    ON raw_items (namespace, key_md5);

CREATE TABLE IF NOT EXISTS raw_docs (
    id             BIGSERIAL    PRIMARY KEY,
    run_id         INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace      VARCHAR(64)  NOT NULL,
    key_md5        VARCHAR(32)  NULL,
    title          TEXT         NULL,
    detail_url     TEXT         NULL,
    publish_date   DATE         NULL,
    effective_date DATE         NULL,
    issuing_authority TEXT       NULL,
    category       TEXT         NULL,
    document_number TEXT         NULL,
    summary        TEXT         NULL,
    fetch_status   TEXT         NOT NULL
                                CHECK (fetch_status IN ('pending', 'ok', 'no_content', 'parse_error', 'fetch_failed', 'download_failed', 'attachment_skipped', 'attachment_failed', 'attachment_saved', 'skipped_existing', 'title_link_only', 'offline_missing')),
    captured_at    TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_raw_docs_run_id     ON raw_docs (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_docs_namespace  ON raw_docs (namespace);
CREATE INDEX IF NOT EXISTS ix_raw_docs_key_md5    ON raw_docs (key_md5);
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_docs_ns_key
    ON raw_docs (namespace, key_md5);

CREATE TABLE IF NOT EXISTS raw_mof (
    doc_id        BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    law_id        TEXT,
    category_id   TEXT,
    create_dept   TEXT,
    file_count    INTEGER,
    expire_date   DATE,
    abolish_date  DATE,
    status        TEXT,
    change        TEXT,
    active        BOOLEAN,
    api_link      TEXT,
    content_text  TEXT,
    attachments   JSONB,
    extra         JSONB
);

CREATE TABLE IF NOT EXISTS raw_gov (
    doc_id           BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    page             INTEGER,
    source_sub       TEXT,
    content_text     TEXT,
    attachment_text  TEXT,
    attachments      JSONB,
    extra            JSONB
);

CREATE TABLE IF NOT EXISTS raw_pbc (
    doc_id             BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    link_type          TEXT,
    file_type          TEXT,
    local_path         TEXT,
    error              TEXT,
    attachment_status  TEXT,
    attachment_errors  JSONB,
    content_text       TEXT,
    attachments        JSONB,
    extra              JSONB
);

CREATE TABLE IF NOT EXISTS raw_nfra (
    doc_id         BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    src_doc_id     TEXT,
    build_date     TEXT,
    index_no       TEXT,
    doc_file_url   TEXT,
    pdf_file_url   TEXT,
    content_text   TEXT,
    attachments    JSONB,
    extra          JSONB
);

CREATE TABLE IF NOT EXISTS raw_internal (
    doc_id        BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    content_text  TEXT,
    attachments   JSONB,
    extra         JSONB
);

CREATE TABLE IF NOT EXISTS cleaned_items (
    id           BIGSERIAL    PRIMARY KEY,
    run_id       INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace    VARCHAR(64)  NOT NULL,
    key_md5      VARCHAR(32)  NULL,
    url          TEXT         NULL,
    payload      JSON         NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cleaned_items_run_id     ON cleaned_items (run_id);
CREATE INDEX IF NOT EXISTS ix_cleaned_items_namespace ON cleaned_items (namespace);
CREATE INDEX IF NOT EXISTS ix_cleaned_items_key_md5   ON cleaned_items (key_md5);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cleaned_items_ns_key
    ON cleaned_items (namespace, key_md5);

CREATE TABLE IF NOT EXISTS clauses (
    id           BIGSERIAL    PRIMARY KEY,
    run_id       INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace    VARCHAR(64)  NOT NULL,
    key_md5      VARCHAR(32)  NULL,
    url          TEXT         NULL,
    payload      JSON         NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clauses_run_id     ON clauses (run_id);
CREATE INDEX IF NOT EXISTS ix_clauses_namespace ON clauses (namespace);
CREATE INDEX IF NOT EXISTS ix_clauses_key_md5   ON clauses (key_md5);
CREATE UNIQUE INDEX IF NOT EXISTS uq_clauses_ns_key
    ON clauses (namespace, key_md5);

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

