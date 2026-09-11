# -*- coding: utf-8 -*-
"""base_publish.build_fts — 发布件 SQLite + FTS5 索引构建（Base Contract v1 §4.1 索引件）

用法：
  python -m base_publish.build_fts external   # → scrapers/published/external_index.sqlite
  python -m base_publish.build_fts internal   # → internal_policy_base/published/internal_index.sqlite
  python -m base_publish.build_fts all

设计：发布件（JSONL）为权威；SQLite 为**派生索引**（可随时重建）。中文检索用 FTS5
`tokenize='trigram'`（子串匹配，适配中文；无需外部分词器）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULES = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_MODULES)
for _p in (_ROOT, _MODULES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXT_DIR = os.path.join(_MODULES, "regulatory_scrapers", "published")
INT_DIR = os.path.join(_MODULES, "internal_policy_base", "published")

_EXT_SCHEMA = """
DROP TABLE IF EXISTS records; DROP TABLE IF EXISTS clauses; DROP TABLE IF EXISTS attachments;
CREATE TABLE records(
  record_id TEXT PRIMARY KEY, rfn TEXT, title TEXT, document_number TEXT,
  issue_organ TEXT, publish_date TEXT, effective_date TEXT, timeliness_status TEXT,
  verification_source TEXT, source TEXT, url TEXT, body_len INT, body_text TEXT, theme TEXT);
CREATE TABLE clauses(
  record_id TEXT, rfn TEXT, document_number TEXT, title TEXT, article_no TEXT,
  article_body TEXT, publish_date TEXT, timeliness_status TEXT);
CREATE TABLE attachments(
  record_id TEXT, rfn TEXT, seq INT, file_name TEXT, kind TEXT, local_path TEXT, sha256 TEXT);
CREATE INDEX idx_records_rfn ON records(rfn);
CREATE INDEX idx_clauses_rfn ON clauses(rfn);
CREATE INDEX idx_clauses_record ON clauses(record_id);
CREATE VIRTUAL TABLE records_fts USING fts5(title, document_number, body_text,
  content='records', content_rowid='rowid', tokenize='trigram');
CREATE VIRTUAL TABLE clauses_fts USING fts5(article_body, article_no, title,
  content='clauses', content_rowid='rowid', tokenize='trigram');
"""

_INT_SCHEMA = """
DROP TABLE IF EXISTS policies; DROP TABLE IF EXISTS internal_clauses;
CREATE TABLE policies(
  ipn TEXT PRIMARY KEY, title TEXT, docno TEXT, eff_status TEXT, primary_theme TEXT,
  secondary_themes TEXT, associated_rfns TEXT, file_type TEXT, extension TEXT,
  sha256 TEXT, article_count INT);
CREATE TABLE internal_clauses(
  ipn TEXT, article_no TEXT, article_body TEXT, rfns TEXT);
CREATE INDEX idx_policies_theme ON policies(primary_theme);
CREATE INDEX idx_iclauses_ipn ON internal_clauses(ipn);
CREATE VIRTUAL TABLE policies_fts USING fts5(title, docno,
  content='policies', content_rowid='rowid', tokenize='trigram');
CREATE VIRTUAL TABLE iclauses_fts USING fts5(article_body, article_no,
  content='internal_clauses', content_rowid='rowid', tokenize='trigram');
"""


def _iter_jsonl(path: str):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def build_external() -> dict:
    rec_p = os.path.join(EXT_DIR, "external_records.jsonl")
    cl_p = os.path.join(EXT_DIR, "external_clauses.jsonl")
    att_p = os.path.join(EXT_DIR, "external_attachments.jsonl")
    db_p = os.path.join(EXT_DIR, "external_index.sqlite")
    if not os.path.exists(rec_p):
        raise FileNotFoundError("发布件缺失（先运行 base publish --base external）: " + rec_p)
    if os.path.exists(db_p):
        os.remove(db_p)
    conn = sqlite3.connect(db_p)
    conn.executescript(_EXT_SCHEMA)
    n_rec = 0
    for r in _iter_jsonl(rec_p):
        conn.execute(
            "INSERT INTO records(record_id,rfn,title,document_number,issue_organ,publish_date,"
            "effective_date,timeliness_status,verification_source,source,url,body_len,body_text,theme)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.get("record_id", ""), r.get("rfn", ""), r.get("title", ""), r.get("document_number", ""),
             r.get("issue_organ", ""), r.get("publish_date", ""), r.get("effective_date", ""),
             r.get("timeliness_status", ""), r.get("verification_source", ""), r.get("source", ""),
             r.get("url", ""), int(r.get("body_len") or 0), r.get("body_text", ""), r.get("theme", "")))
        n_rec += 1
    n_cl = 0
    if os.path.exists(cl_p):
        for c in _iter_jsonl(cl_p):
            conn.execute(
                "INSERT INTO clauses(record_id,rfn,document_number,title,article_no,article_body,"
                "publish_date,timeliness_status) VALUES(?,?,?,?,?,?,?,?)",
                (c.get("record_id", ""), c.get("rfn", ""), c.get("document_number", ""),
                 c.get("title", ""), c.get("article_no", ""), c.get("article_body", ""),
                 c.get("publish_date", ""), c.get("timeliness_status", "")))
            n_cl += 1
    n_att = 0
    if os.path.exists(att_p):
        for a in _iter_jsonl(att_p):
            conn.execute(
                "INSERT INTO attachments(record_id,rfn,seq,file_name,kind,local_path,sha256)"
                " VALUES(?,?,?,?,?,?,?)",
                (a.get("record_id", ""), a.get("rfn", ""), int(a.get("seq") or 0),
                 a.get("file_name", ""), a.get("kind", ""), a.get("local_path", ""), a.get("sha256", "")))
            n_att += 1
    conn.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO clauses_fts(clauses_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return {"db": db_p, "records": n_rec, "clauses": n_cl, "attachments": n_att}


def build_internal() -> dict:
    pol_p = os.path.join(INT_DIR, "internal_policies.jsonl")
    icl_p = os.path.join(INT_DIR, "internal_clauses.jsonl")
    db_p = os.path.join(INT_DIR, "internal_index.sqlite")
    if not os.path.exists(pol_p):
        raise FileNotFoundError("发布件缺失（先运行 base publish --base internal）: " + pol_p)
    if os.path.exists(db_p):
        os.remove(db_p)
    conn = sqlite3.connect(db_p)
    conn.executescript(_INT_SCHEMA)
    n_pol = 0
    for p in _iter_jsonl(pol_p):
        conn.execute(
            "INSERT INTO policies(ipn,title,docno,eff_status,primary_theme,secondary_themes,"
            "associated_rfns,file_type,extension,sha256,article_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (p.get("ipn", ""), p.get("title", ""), p.get("docno", ""), p.get("eff_status", ""),
             p.get("primary_theme", ""), json.dumps(p.get("secondary_themes") or [], ensure_ascii=False),
             json.dumps(p.get("associated_rfns") or [], ensure_ascii=False),
             p.get("file_type", ""), p.get("extension", ""), p.get("sha256", ""),
             int(p.get("article_count") or 0)))
        n_pol += 1
    n_cl = 0
    if os.path.exists(icl_p):
        for c in _iter_jsonl(icl_p):
            conn.execute("INSERT INTO internal_clauses(ipn,article_no,article_body,rfns) VALUES(?,?,?,?)",
                         (c.get("ipn", ""), c.get("article_no", ""), c.get("article_body", ""),
                          json.dumps(c.get("rfns") or [], ensure_ascii=False)))
            n_cl += 1
    conn.execute("INSERT INTO policies_fts(policies_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO iclauses_fts(iclauses_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return {"db": db_p, "policies": n_pol, "clauses": n_cl}


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = {}
    if what in ("external", "all"):
        out["external"] = build_external()
    if what in ("internal", "all"):
        out["internal"] = build_internal()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
