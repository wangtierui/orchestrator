# -*- coding: utf-8 -*-
"""Refactor 原生文档引用图抽取（docgraph）：cleaned_items → docgraph_edges。

  - 源文档来自 cleaned_items（namespace 恒为 'cleaned'）；
  - 实体解析索引由 cleaned_items 语料构建（归一标题 → doc_id），
    未命中即外部引用 name:<canonical>；
  - 抽取器由 docgraph/relations.py 提供（可注入，见 extract 的 extractor_factory）。

落点：docgraph_edges 6 列（edge_id/src_id/src_name/dst_id/dst_name/relation）。
持久化由 collectors.storage.docgraph_loader 负责（本模块只做抽取，不碰存储）。

独立项目约束：不注入 sys.path，模块解析依赖 cwd=refactor。
"""
import argparse
import hashlib
import re

from collectors.storage.docgraph_loader import load_rows
from config import psycopg_dsn  # 统一 URL 归一化（消除手工 .replace）
from docgraph.relations import build_extractor


def _norm(s: str) -> str:
    """归一标题/书名号，用于解析匹配（去空白、去书名号/括号）。"""
    return re.sub(r"\s+", "", (s or "").strip("《》〈〉（）()「」"))


def _doc_id(payload: dict) -> str:
    return payload.get("index_no") or payload.get("id") or payload.get("title") or ""


def _payload_to_doc(p: dict) -> dict:
    """cleaned payload → 文档表示（doc_id / title / text）。

    正文对齐采集规范信封 content；对旧版 cleaned 残留的 body_text / text 保留读取
    回退（原则9 兼容续跑）。source 在 docgraph 抽取中无消费，不纳入（最小化携带）。
    """
    return {
        "doc_id": _doc_id(p),
        "title": p.get("title", "") or "",
        "text": p.get("content") or p.get("body_text") or p.get("text") or "",
    }


def load_corpus(db_url: str, namespaces=("cleaned",), limit: int = 0) -> list:
    """从 cleaned_items 读出文档 (doc_id, title, text)，供 RelationExtractor 抽取。

    正文字段为 clean 规范信封 content（兼容旧版 body_text / text 回退）。
    """
    try:
        from psycopg import connect
    except ImportError:  # pragma: no cover
        from psycopg2 import connect  # type: ignore
    pg = psycopg_dsn(db_url)
    docs = []
    with connect(pg) as conn:
        with conn.cursor() as cur:
            for ns in namespaces:
                cur.execute(
                    "SELECT id, index_no, title, content, source, publish_date, "
                    "issuing_authority, category, detail_url, document_number, "
                    "dedup_key, attachment_content, key_md5, url "
                    "FROM cleaned_items WHERE namespace = %s", (ns,)
                )
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    p = dict(zip(cols, row))
                    docs.append(_payload_to_doc(p))
    if limit:
        docs = docs[:limit]
    return docs


def build_index(docs: list) -> dict:
    """归一标题 → doc_id 解析索引（同标题取首个）。"""
    idx = {}
    for d in docs:
        k = _norm(d["title"])
        if k and k not in idx:
            idx[k] = d["doc_id"]
    return idx


def _resolve(name: str, index: dict):
    k = _norm(name)
    if k and k in index:
        return index[k], "title"
    return "name:" + (k or name), "unresolved"


def extract(db_url: str, namespaces=("cleaned",), limit: int = 0,
            extractor_factory=None) -> list:
    """从 cleaned_items 抽全部关系边，返回 6 列 dict 列表。

    extractor_factory：可注入的抽取器工厂；独立项目未内置该算法，缺省走
    docgraph/relations.build_extractor，缺依赖时给出可操作的错误。
    """
    docs = load_corpus(db_url, namespaces=namespaces, limit=limit)
    index = build_index(docs)
    ext = (extractor_factory or build_extractor)()
    seen = set()
    rows = []
    for d in docs:
        if not d["text"]:
            continue
        res = ext.extract(d["text"])
        for item, relation in [(b, "basis") for b in res.basis] + [
            (r, "repeal") for r in res.repeal
        ]:
            name = item.target_name
            dst_id, _matched = _resolve(name, index)
            if relation == "basis":
                basis_type = getattr(item, "basis_type", "") or ""
                action = ""
            else:
                basis_type = ""
                action = getattr(item, "action", "") or ""
            article = getattr(item, "article", "") or ""
            edge_key = "|".join([d["doc_id"], dst_id, relation, article, basis_type, action])
            edge_id = hashlib.sha1(edge_key.encode("utf-8")).hexdigest()[:16]
            if edge_id in seen:
                continue
            seen.add(edge_id)
            rows.append(
                {
                    "edge_id": edge_id,
                    "src_id": d["doc_id"],
                    "src_name": d["title"],
                    "dst_id": dst_id,
                    "dst_name": name,
                    "relation": relation,
                }
            )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Refactor-native relation extraction → docgraph_edges")
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--namespaces", default="cleaned", help="逗号分隔的源 namespace（默认 cleaned_items 的 cleaned）")
    ap.add_argument("--limit", type=int, default=0, help="仅抽取前 N 篇（验证用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不落库")
    args = ap.parse_args(argv)
    nss = [x.strip() for x in args.namespaces.split(",") if x.strip()]
    rows = extract(args.db_url, namespaces=nss, limit=args.limit)
    if args.dry_run:
        for r in rows[:30]:
            print(r)
        print("total edges:", len(rows))
        return
    n = load_rows(rows, args.db_url)
    print(f"upserted {n} edges into docgraph_edges")


if __name__ == "__main__":
    main()
