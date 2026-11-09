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
    key_md5        VARCHAR(32)  NOT NULL,
    title          TEXT         NULL,
    detail_url     TEXT         NULL,
    publish_date   TEXT         NULL,
    effective_date TEXT         NULL,
    issuing_authority TEXT       NULL,
    category       TEXT         NULL,
    document_number TEXT         NULL,
    summary        TEXT         NULL,
    fetch_status   TEXT         NOT NULL
                                CHECK (fetch_status IN ('pending', 'ok', 'no_content', 'parse_error', 'fetch_failed', 'download_failed', 'attachment_skipped', 'attachment_failed', 'attachment_saved', 'skipped_existing', 'title_link_only', 'offline_missing')),
    captured_at    TIMESTAMPTZ  DEFAULT now(),
    content_ref    TEXT         NULL,
    CONSTRAINT uq_raw_docs_ns_key UNIQUE (namespace, key_md5)
);
CREATE INDEX IF NOT EXISTS ix_raw_docs_run_id     ON raw_docs (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_docs_namespace  ON raw_docs (namespace);
CREATE INDEX IF NOT EXISTS ix_raw_docs_key_md5    ON raw_docs (key_md5);

CREATE TABLE IF NOT EXISTS raw_mof (
    doc_id        BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    law_id        TEXT,
    category_id   TEXT,
    create_dept   TEXT,
    file_count    INTEGER,
    expire_date   TEXT,
    abolish_date  TEXT,
    status        TEXT,
    change        TEXT,
    active        BOOLEAN,
    api_link      TEXT,
    attachments   JSONB,
    extra         JSONB
);

CREATE TABLE IF NOT EXISTS raw_gov (
    doc_id           BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    page             INTEGER,
    source_sub       TEXT,
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
    attachment_errors  JSONB,
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
    attachments    JSONB,
    extra          JSONB
);

CREATE TABLE IF NOT EXISTS raw_internal (
    doc_id        BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    attachments   JSONB,
    extra         JSONB
);

CREATE TABLE IF NOT EXISTS cleaned_items (
    id                 BIGSERIAL    PRIMARY KEY,
    run_id             INTEGER      NOT NULL REFERENCES master_runs (id),
    namespace          VARCHAR(64)  NOT NULL,
    key_md5            VARCHAR(32)  NULL,
    url                TEXT         NULL,
    source             VARCHAR(64)  NULL,
    detail_url         TEXT         NULL,
    document_number    TEXT         NULL,
    title              TEXT         NULL,
    index_no           TEXT         NULL,
    content            TEXT         NULL,
    attachment_content TEXT         NULL,
    publish_date       TEXT         NULL,
    issuing_authority  TEXT         NULL,
    category           TEXT         NULL,
    dedup_key          VARCHAR(64)  NULL
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

CREATE TABLE IF NOT EXISTS fetch_dead_letter (
    id                  BIGSERIAL    PRIMARY KEY,
    source              VARCHAR(32)  NOT NULL,
    doc_key             VARCHAR(128) NOT NULL,
    entry               JSONB        NULL,
    endpoint            TEXT         NULL,
    params              JSONB        NULL,
    attempt_count       INTEGER      NOT NULL DEFAULT 0,
    max_attempts        INTEGER      NOT NULL DEFAULT 4,
    last_status         INTEGER      NULL,
    last_error          TEXT         NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'pending',
    needs_review        BOOLEAN      NOT NULL DEFAULT false,
    next_attempt_after  TIMESTAMPTZ  NULL,
    created_at          TIMESTAMPTZ  DEFAULT now(),
    updated_at          TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fetch_dead_letter_source     ON fetch_dead_letter (source);
CREATE INDEX IF NOT EXISTS ix_fetch_dead_letter_pending   ON fetch_dead_letter (status, next_attempt_after);
-- 索引名与 raw_store.FETCH_DEAD_LETTER 的 UniqueConstraint 名一致，避免
-- create_all 补建同名约束时出现两套去重定义。
CREATE UNIQUE INDEX IF NOT EXISTS uq_dead_letter_src_key
    ON fetch_dead_letter (source, doc_key);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id           BIGSERIAL    PRIMARY KEY,
    source       VARCHAR(32)  NOT NULL,
    started_at   TIMESTAMPTZ  NOT NULL,
    finished_at  TIMESTAMPTZ  NULL,
    attempted    INTEGER      NOT NULL DEFAULT 0,
    ok           INTEGER      NOT NULL DEFAULT 0,
    status_403   INTEGER      NOT NULL DEFAULT 0,
    status_429   INTEGER      NOT NULL DEFAULT 0,
    status_5xx   INTEGER      NOT NULL DEFAULT 0,
    status_404_perm INTEGER   NOT NULL DEFAULT 0,
    status_net   INTEGER      NOT NULL DEFAULT 0,
    parse_err    INTEGER      NOT NULL DEFAULT 0,
    recovered    INTEGER      NOT NULL DEFAULT 0,
    remaining    INTEGER      NOT NULL DEFAULT 0,
    summary      JSONB        NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fetch_runs_source ON fetch_runs (source);

