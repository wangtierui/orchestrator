# -*- coding: utf-8 -*-
"""interfaces/base_api — 底座发布件统一读取契约（Base Contract v1 §5）

**三模块唯一允许的底座访问面**（禁止裸读底座内部目录——根治 E-08/A-03 直引）：

  manifest(base)                  发布清单（sha256/计数/快照日/schema_version）
  query_external(**filters)       外部记录精确过滤（rfn/document_number/source/theme/timeliness_status）
  search_external(text, limit)    外部全文检索（FTS5 trigram，命中 title/document_number/body）
  get_external(record_id)         单条外部记录
  query_internal(theme="")        内部制度过滤（theme/ipn）
  search_internal(text, limit)    内部全文检索（policies + internal_clauses）
  get_policy(ipn)                 单条内部制度

数据源：`modules/regulatory_scrapers/published/`（external）与
`modules/internal_policy_base/published/`（internal）——JSONL 权威 + SQLite 派生索引。
索引缺失时明确报错指引 `orchestrator base publish`，不做兜底裸读。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # interfaces/
_ROOT = os.path.dirname(_HERE)                                # orchestrator 根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_MODULES = os.path.join(_ROOT, "modules")
EXT_DIR = os.path.join(_MODULES, "regulatory_scrapers", "published")
INT_DIR = os.path.join(_MODULES, "internal_policy_base", "published")


class BaseUnavailable(RuntimeError):
    """发布件/索引未就绪（指引先运行 `orchestrator base publish`）。"""


def _db(base: str) -> str:
    if base == "external":
        p = os.path.join(EXT_DIR, "external_index.sqlite")
        hint = "orchestrator base publish --base external"
    else:
        p = os.path.join(INT_DIR, "internal_index.sqlite")
        hint = "orchestrator base publish --base internal"
    if not os.path.exists(p):
        raise BaseUnavailable(f"发布索引不存在: {p}（先运行 {hint}）")
    return p


def _conn(base: str) -> sqlite3.Connection:
    c = sqlite3.connect(_db(base))
    c.row_factory = sqlite3.Row
    return c


def manifest(base: str = "external") -> dict:
    d = EXT_DIR if base == "external" else INT_DIR
    p = os.path.join(d, "publish_manifest.json")
    if not os.path.exists(p):
        raise BaseUnavailable(f"发布清单不存在: {p}（先运行 orchestrator base publish）")
    return json.load(open(p, encoding="utf-8"))


def query_external(*, rfn: str = "", document_number: str = "", source: str = "",
                   theme: str = "", timeliness_status: str = "", limit: int = 100,
                   with_body: bool = False) -> list[dict]:
    """外部记录精确过滤（等值匹配；空参数忽略）。with_body=False 时省略正文（默认轻量）。"""
    cols = ("record_id,rfn,title,document_number,issue_organ,publish_date,effective_date,"
            "timeliness_status,verification_source,source,url,body_len,theme")
    if with_body:
        cols += ",body_text"
    where, args = [], []
    for k, v in (("rfn", rfn), ("document_number", document_number), ("source", source),
                 ("theme", theme), ("timeliness_status", timeliness_status)):
        if v:
            where.append(f"{k}=?")
            args.append(v)
    sql = f"SELECT {cols} FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    args.append(int(limit))
    with _conn("external") as c:
        return [dict(r) for r in c.execute(sql, args)]


def search_external(text: str, limit: int = 20, kind: str = "records") -> list[dict]:
    """外部全文检索（FTS5 trigram）。kind=records|clauses。"""
    q = '"' + (text or "").replace('"', "") + '"'
    with _conn("external") as c:
        if kind == "clauses":
            sql = ("SELECT cl.record_id, cl.rfn, cl.title, cl.article_no, cl.article_body "
                   "FROM clauses_fts f JOIN clauses cl ON cl.rowid=f.rowid "
                   "WHERE clauses_fts MATCH ? LIMIT ?")
            return [dict(r) for r in c.execute(sql, (q, int(limit)))]
        sql = ("SELECT r.record_id, r.rfn, r.title, r.document_number, r.publish_date, "
               "r.timeliness_status, r.source, r.body_len FROM records_fts f "
               "JOIN records r ON r.rowid=f.rowid WHERE records_fts MATCH ? LIMIT ?")
        return [dict(r) for r in c.execute(sql, (q, int(limit)))]


def get_external(record_id: str) -> dict | None:
    with _conn("external") as c:
        row = c.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
        return dict(row) if row else None


def query_internal(*, theme: str = "", ipn: str = "", limit: int = 200) -> list[dict]:
    where, args = [], []
    if theme:
        where.append("primary_theme=?")
        args.append(theme)
    if ipn:
        where.append("ipn=?")
        args.append(ipn)
    sql = "SELECT * FROM policies"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    args.append(int(limit))
    with _conn("internal") as c:
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            for k in ("secondary_themes", "associated_rfns"):
                try:
                    d[k] = json.loads(d.get(k) or "[]")
                except ValueError:
                    d[k] = []
            out.append(d)
        return out


def search_internal(text: str, limit: int = 20, kind: str = "policies") -> list[dict]:
    """内部全文检索。kind=policies|clauses。"""
    q = '"' + (text or "").replace('"', "") + '"'
    with _conn("internal") as c:
        if kind == "clauses":
            sql = ("SELECT ic.ipn, ic.article_no, ic.article_body, p.title "
                   "FROM iclauses_fts f JOIN internal_clauses ic ON ic.rowid=f.rowid "
                   "LEFT JOIN policies p ON p.ipn=ic.ipn WHERE iclauses_fts MATCH ? LIMIT ?")
            return [dict(r) for r in c.execute(sql, (q, int(limit)))]
        sql = ("SELECT p.ipn, p.title, p.docno, p.primary_theme FROM policies_fts f "
               "JOIN policies p ON p.rowid=f.rowid WHERE policies_fts MATCH ? LIMIT ?")
        return [dict(r) for r in c.execute(sql, (q, int(limit)))]


def get_policy(ipn: str) -> dict | None:
    rows = query_internal(ipn=ipn, limit=1)
    return rows[0] if rows else None


def version_chain(docno: str, limit: int = 50) -> list[dict]:
    """同文号（归一）多版本链（按发布日期升序）——F-K08 版本链视图的最小可用实现。

    view_active 语义 = `query_external(timeliness_status="valid")`（现行视图）；
    本函数提供"同文号演进链"（历史版本 → 现行）显性化。
    """
    from std_lib.common_lib.norm import norm_docno  # noqa: PLC0415
    nd = norm_docno(docno)
    if not nd:
        return []
    with _conn("external") as c:
        rows = [dict(r) for r in c.execute(
            "SELECT record_id,rfn,title,document_number,publish_date,effective_date,"
            "timeliness_status,source,body_len FROM records")]
    out = [r for r in rows if norm_docno(r.get("document_number") or "") == nd]
    out.sort(key=lambda r: r.get("publish_date") or "")
    return out[:limit]


def view_active(limit: int = 200) -> list[dict]:
    """现行有效视图（timeliness_status=valid 的发布记录，轻量列）。"""
    return query_external(timeliness_status="valid", limit=limit)
