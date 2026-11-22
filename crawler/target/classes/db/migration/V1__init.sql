CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    site_id       VARCHAR(64) NOT NULL,
    source_id     VARCHAR(64) NOT NULL,
    title         TEXT NOT NULL,
    doc_no        TEXT,
    issuing_body  TEXT,
    publish_date  TIMESTAMP,
    effect_date   TIMESTAMP,
    expire_date   TIMESTAMP,
    content       TEXT,
    summary       TEXT,
    keywords      TEXT,
    raw_json      TEXT,
    crawl_status  VARCHAR(20) DEFAULT 'pending',
    created_at    TIMESTAMP DEFAULT now(),
    updated_at    TIMESTAMP DEFAULT now(),
    UNIQUE (site_id, source_id)
);

CREATE TABLE IF NOT EXISTS crawl_checkpoint (
    site_id                VARCHAR(64) PRIMARY KEY,
    last_doc_id            VARCHAR(64),
    last_doc_publish_date  TIMESTAMP,
    last_crawl_at          TIMESTAMP,
    status                 VARCHAR(20),
    total_crawled          INT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_documents_site_publish ON documents (site_id, publish_date DESC);
