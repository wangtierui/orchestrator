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

MASTER_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS master_runs (
    id           SERIAL       PRIMARY KEY,
    namespace    VARCHAR(64)  NOT NULL,
    captured_at  TIMESTAMPTZ  NOT NULL,
    count        INTEGER      NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_runs_namespace ON master_runs (namespace);
"""

# 数据主体：每条记录 = 一行，整条字典塞进 payload（JSON）。
MASTER_ITEMS_DDL = """
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

# 完整 DDL（按依赖顺序：runs → items → edges → 向量表）。
FULL_DDL = MASTER_RUNS_DDL + MASTER_ITEMS_DDL + DOCGRAPH_EDGES_DDL + VECTOR_DDL
