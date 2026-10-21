# -*- coding: utf-8 -*-
"""refactor 存储层权威 DDL 源（单一事实来源），归 init 包所有。

设计约定（来自 init 工具决策）：
  - 本模块是**唯一**的建表/建扩展定义处；init 工具与其生成的 schema.sql 都从这里导出，
    杜绝多处定义漂移（原 docgraph_loader.py / storage 内的重复 DDL 已收敛到此处）。
  - 所有语句均带 IF NOT EXISTS，可重复执行（幂等，不破坏已有数据）。
  - 扩展安装也归 init 管：现在装 Apache AGE（图查询底座）；未来接入向量等能力时，
    仅在 EXTENSIONS 追加一项、并在 VECTOR_DDL 补建表语句，init 会自动顺带创建。
"""
from __future__ import annotations

# 初始化时必须确保安装的扩展：(显示名, CREATE EXTENSION 用的标识)
# 新增向量数据能力时在此追加，例如 ("pgvector", "vector")，并在 VECTOR_DDL 补对应表。
EXTENSIONS: list[tuple[str, str]] = [
    ("age", "age"),  # Apache AGE 图扩展（Cypher 多跳查询底座）
]

# 向量库建表语句占位：待向量插件确定后填充，例如
#   CREATE TABLE IF NOT EXISTS document_vectors (
#       doc_id  TEXT PRIMARY KEY,
#       embedding vector(1536)
#   );
VECTOR_DDL = ""

# ── 采集阶段抓取状态机（单一事实来源）──
# 所有源共用同一组 fetch_status；取值须与 collectors/lib/fetch_status.py 的
# FETCH_STATUS_VALUES 逐字一致（下方 CHECK 约束在 PG 侧强制）。
FETCH_STATUS_VALUES = (
    "pending", "ok", "no_content", "parse_error", "fetch_failed",
    "download_failed", "attachment_skipped", "attachment_failed",
    "attachment_saved", "skipped_existing", "title_link_only", "offline_missing",
)
_FETCH_STATUS_CHECK = ", ".join(f"'{v}'" for v in FETCH_STATUS_VALUES)

# ── 采集阶段范式化 schema：主表公共列 + 各源副表（doc_id 关联）──
# 取代 raw_items 的整行 JSON 方案；raw_items 暂保留以兼容存量代码，迁移完成后移除。
RAW_DOCS_DDL = """
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
    doc_no         TEXT         NULL,
    summary        TEXT         NULL,
    fetch_status   TEXT         NOT NULL
                                CHECK (fetch_status IN (__FS_VALUES__)),
    captured_at    TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_raw_docs_run_id     ON raw_docs (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_docs_namespace  ON raw_docs (namespace);
CREATE INDEX IF NOT EXISTS ix_raw_docs_key_md5    ON raw_docs (key_md5);
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_docs_ns_key
    ON raw_docs (namespace, key_md5);
""".replace("__FS_VALUES__", _FETCH_STATUS_CHECK)

RAW_MOF_DDL = """
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
"""

RAW_GOV_DDL = """
CREATE TABLE IF NOT EXISTS raw_gov (
    doc_id           BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    page             INTEGER,
    source_sub       TEXT,
    content_text     TEXT,
    attachment_text  TEXT,
    attachments      JSONB,
    extra            JSONB
);
"""

RAW_PBC_DDL = """
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
"""

RAW_NFRA_DDL = """
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
"""

RAW_INTERNAL_DDL = """
CREATE TABLE IF NOT EXISTS raw_internal (
    doc_id        BIGINT       PRIMARY KEY REFERENCES raw_docs (id),
    content_text  TEXT,
    attachments   JSONB,
    extra         JSONB
);
"""

MASTER_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS master_runs (
    id           SERIAL       PRIMARY KEY,
    namespace    VARCHAR(64)  NOT NULL,
    captured_at  TIMESTAMPTZ  NOT NULL,
    count        INTEGER      NOT NULL,
    meta         JSONB        NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_runs_namespace ON master_runs (namespace);
"""

# 数据主体（按阶段分表）：每条记录 = 一行，整条字典塞进 payload（JSON）。
#  - raw_items   ：采集阶段产出（各源信封，源以 namespace 区分：gov/mof/nfra/pbc/internal…）
#  - cleaned_items：clean 阶段产出（统一 schema）
#  - clauses     ：clause 阶段产出（条文结构）
# 旧的单表 master_items（仅靠 namespace 分区）已废弃，不再创建。
RAW_ITEMS_DDL = """
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
"""

CLEANED_ITEMS_DDL = """
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
"""

CLAUSES_DDL = """
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
"""

# 文档引用边（最小图模型；与 Apache AGE 图查询共用同一份数据）。
DOCGRAPH_EDGES_DDL = """
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
"""

# 完整 DDL（按依赖顺序：runs → raw_items(兼容) → raw_docs + 各源副表 →
#              cleaned_items → clauses → edges → 向量表）。
FULL_DDL = (
    MASTER_RUNS_DDL
    + RAW_ITEMS_DDL
    + RAW_DOCS_DDL
    + RAW_MOF_DDL
    + RAW_GOV_DDL
    + RAW_PBC_DDL
    + RAW_NFRA_DDL
    + RAW_INTERNAL_DDL
    + CLEANED_ITEMS_DDL
    + CLAUSES_DDL
    + DOCGRAPH_EDGES_DDL
    + VECTOR_DDL
)
