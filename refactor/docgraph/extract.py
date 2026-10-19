# -*- coding: utf-8 -*-
"""Refactor 原生文档引用图抽取（docgraph）：从 master_items 读 clean 文档 → RelationExtractor → docgraph_edges。

与旧管线（extract_relations.py 读 legacy 文件 + legacy 索引）的区别：
  - 源文档来自 master_items（默认 namespace='cleaned'），不是 legacy cleaned jsonl；
  - 实体解析索引由 master_items 自身构建（归一标题 → doc_id），未命中即外部引用 name:<canonical>；
  - 核心正则抽取逻辑 RelationExtractor 原样复用（源无关）。

落点：docgraph_edges 6 列（edge_id/src_id/src_name/dst_id/dst_name/relation），与 schema.sql 一致。
持久化由 refactor.collectors.storage.docgraph_loader 负责（本模块只做抽取，不碰存储）。
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

# 让仓库根的 std_lib / refactor / config 包均可导入，并确保仓库根优先于脚本所在目录
# （否则脚本目录下的 config.py 会抢在仓库根 config 包前，导致 `from config.enums` 失败）
_ROOT = Path(__file__).resolve().parents[2]
_ROOT_STR = str(_ROOT)
if _ROOT_STR in sys.path:
    sys.path.remove(_ROOT_STR)
sys.path.insert(0, _ROOT_STR)

from std_lib.common_lib.relations import RelationExtractor  # 核心抽取逻辑（源无关）
try:
    from refactor.collectors.storage.docgraph_loader import load_rows
except ImportError:  # pragma: no cover - 作为脚本直接运行时
    from docgraph_loader import load_rows  # type: ignore


def _norm(s: str) -> str:
    """归一标题/书名号，用于解析匹配（去空白、去书名号/括号）。"""
    return re.sub(r"\s+", "", (s or "").strip("《》〈〉（）()「」"))


def _doc_id(payload: dict) -> str:
    return payload.get("index_no") or payload.get("id") or payload.get("title") or ""


def load_corpus(db_url: str, namespaces=("cleaned",), limit: int = 0) -> list:
    """从 master_items 读出文档 (doc_id, title, source, text)。"""
    try:
        from psycopg import connect
    except ImportError:  # pragma: no cover
        from psycopg2 import connect  # type: ignore
    pg = db_url.replace("postgresql+psycopg://", "postgresql://")
    docs = []
    with connect(pg) as conn:
        with conn.cursor() as cur:
            for ns in namespaces:
                cur.execute(
                    "SELECT payload FROM master_items WHERE namespace = %s", (ns,)
                )
                for (pl,) in cur.fetchall():
                    p = dict(pl) if not isinstance(pl, dict) else pl
                    text = p.get("body_text") or p.get("text") or ""
                    docs.append(
                        {
                            "doc_id": _doc_id(p),
                            "title": p.get("title", "") or "",
                            "source": p.get("source", "") or "",
                            "text": text,
                        }
                    )
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


def extract(db_url: str, namespaces=("cleaned",), limit: int = 0) -> list:
    """从 master_items 抽全部关系边，返回 6 列 dict 列表。"""
    docs = load_corpus(db_url, namespaces=namespaces, limit=limit)
    index = build_index(docs)
    ext = RelationExtractor()
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
    ap.add_argument("--namespaces", default="cleaned", help="逗号分隔的 master_items namespace")
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
