# -*- coding: utf-8 -*-
"""RelationsLoader：把关系抽取产物（relations_index.jsonl 每行）投影为最小边表 rel_edges 并幂等落库。

投影规则（6 列，与 schema.sql 的 rel_edges 一致）：
  edge_id  <- relation_id
  src_id   <- src_ref 或 src_key
  src_name <- src_name
  dst_id   <- dst_ref 或 dst_key；都没有则 name:<dst_normalized_name>
  dst_name <- dst_name
  relation <- relation

设计约束（来自关系存储讨论）：
  - 最小可用：图只依赖 (src_id, dst_id, relation) 拓扑；节点即 cleaned 文档。
  - 抽取产物的其余 20+ 字段暂留 JSONL SSOT，不进 PG；后续不够用时在 rel_edges 加列并从 JSONL 回填。
  - 幂等：edge_id 为主键，重复加载 ON CONFLICT DO NOTHING。

用法：
  python relations_loader.py \
      --jsonl modules/regulatory_classifier/data/relations/relations_index.jsonl \
      --db-url postgresql+psycopg://root:5462@localhost:5444/test
"""
import argparse
import json

try:
    from psycopg import connect  # psycopg v3（refactor 默认驱动）
except ImportError:  # pragma: no cover - 兼容 psycopg2
    from psycopg2 import connect  # type: ignore


def project_row(r: dict) -> tuple:
    """将一条 relations_index.jsonl 记录投影为 rel_edges 的 6 列元组。"""
    src_id = r.get("src_ref") or r.get("src_key") or ""
    src_name = r.get("src_name") or ""
    dst_ref = r.get("dst_ref") or r.get("dst_key")
    if dst_ref:
        dst_id = dst_ref
    else:
        canon = r.get("dst_normalized_name") or r.get("dst_name") or ""
        dst_id = "name:" + canon
    dst_name = r.get("dst_name") or ""
    relation = r.get("relation") or ""
    return (r.get("relation_id"), src_id, src_name, dst_id, dst_name, relation)


DDL = """
CREATE TABLE IF NOT EXISTS rel_edges (
    edge_id   TEXT PRIMARY KEY,
    src_id    TEXT NOT NULL,
    src_name  TEXT NOT NULL,
    dst_id    TEXT NOT NULL,
    dst_name  TEXT NOT NULL,
    relation  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rel_edges_src       ON rel_edges (src_id);
CREATE INDEX IF NOT EXISTS ix_rel_edges_dst       ON rel_edges (dst_id);
CREATE INDEX IF NOT EXISTS ix_rel_edges_relation  ON rel_edges (relation);
"""


def _pg_url(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg://", "postgresql://")


def load_rows(rows: list, db_url: str) -> int:
    """将已投影好的边（dict 或 6 元组）批量幂等写入 rel_edges。"""
    params = [
        (r["edge_id"], r["src_id"], r["src_name"], r["dst_id"], r["dst_name"], r["relation"])
        if isinstance(r, dict)
        else tuple(r)
        for r in rows
    ]
    with connect(_pg_url(db_url)) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.executemany(
                "INSERT INTO rel_edges (edge_id, src_id, src_name, dst_id, dst_name, relation) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (edge_id) DO NOTHING",
                params,
            )
        conn.commit()
    return len(params)


def load(jsonl_path: str, db_url: str) -> int:
    rows = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(project_row(json.loads(line)))
    return load_rows(rows, db_url)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load relations_index.jsonl into rel_edges")
    ap.add_argument("--jsonl", required=True, help="relations_index.jsonl 路径")
    ap.add_argument("--db-url", dest="db_url", required=True, help="REFACTOR_DB_URL 形式")
    args = ap.parse_args(argv)
    n = load(args.jsonl, args.db_url)
    print(f"upserted {n} edges into rel_edges")


if __name__ == "__main__":
    main()
