# -*- coding: utf-8 -*-
"""tools/semantic_search.py — 分析交付库语义检索视图（W4 §6.4，pgvector 接线）

定位（方案 §6.4 + ADR-0007 L-b 派生层）
--------------------------------------
把「素材就绪但无消费」的 pgvector 落到**低风险场景**：分析交付库的**只读**语义
检索视图——对 17 项交付物（docs/reports/*.md）+ 发布件记录（限量）做嵌入索引，
支持「按意思找交付物/记录」。三条边界纪律：

  1. **不触碰事实源**：只读消费交付物与发布件；向量索引是派生层（可随时重建）；
  2. **不阻断主链**：后端/嵌入链不可用 → rc=0 显式披露（增强层合法状态，同
     `semantic:assist` 纪律——ADR-0003「无度量不上线」的延伸）；
  3. **先披露质量再扩大使用面**：与 FTS5 的 A/B 相关性（overlap@k）随产物落盘，
     进 BENCHMARK §6.6；证据充分前不把向量路径升为检索主路径。

降级链（`std_lib.common_lib.vector_store`）
-------------------------------------------
pgvector（env `PGVECTOR_DSN`，127.0.0.1:7777）→ sqlite_vec → none（调用方走既有
`base_api.search_external` FTS5 路径）。口令仅经 env 注入（`gate_secret_scan` 守护）。

用法
----
    python -m tools.semantic_search                # = --build（链上调用点同此）
    python -m tools.semantic_search --build --records-limit 2000
    python -m tools.semantic_search --query "保险资金运用 关联交易" --k 5
    python -m tools.semantic_search --ab           # 只跑 A/B 探针（不重建索引）

产物：`reports/semantic_search/semantic_search_<date>.json`（可用性 + 计数 + A/B）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os

# 引导：**必须经 `bootstrap`（唯一引导点）** —— `gate_import_bootstrap` 断言 tools/ 层
from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

OUT_DIR = os.path.join(paths.ROOT, "reports", "semantic_search")
DELIVERY_DIR = os.path.join(paths.ROOT, "docs", "reports")

#: 向量索引表名（`vec` schema 下；白名单校验见 vector_store._check_name）
T_DOCS = "delivery_docs"
T_RECORDS = "delivery_records"

#: A/B 探针查询集（固定 8 条**单一连贯短语**，覆盖监管高频主题；**不随数据漂移**
#: ——探针本身是度量器具的一部分，换探针即换尺子，须显式改这里并在产物中留痕）。
#: 为什么用短语而非关键词袋：FTS5 侧经 `base_api.search_external`（trigram 短语
#: 匹配，接口契约不改）；同一字符串喂两个检索器才是公平对比。
AB_PROBES = (
    "保险资金运用管理",
    "偿付能力风险管理",
    "关联交易管理",
    "互联网保险业务",
    "公司治理",
    "股权管理",
    "再保险业务",
    "行政处罚",
)
AB_K = 10

_AB_LIMIT_NOTE = "A/B 仅在**发布件记录**面进行（FTS5 索引只覆盖 records；交付物侧无全文索引可比）"


# --------------------------------------------------------------------------- #
# 输入加载（只读）
# --------------------------------------------------------------------------- #
def _load_delivery_docs() -> list[dict]:
    """17 项交付物 → [{doc_id, title, text}]（正文限量截断，防超大文档撑爆嵌入批）。"""
    if not os.path.isdir(DELIVERY_DIR):
        return []
    out: list[dict] = []
    for fn in sorted(os.listdir(DELIVERY_DIR)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(DELIVERY_DIR, fn)
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        title = fn
        for ln in text.splitlines():
            if ln.startswith("# "):
                title = ln[2:].strip() or fn
                break
        out.append({"doc_id": f"doc:{fn}", "title": title, "text": text[:_DOC_CHARS]})
    return out


#: 单交付物嵌入字符上限（~512 token 量级；bge-zh 支持 512 token）
_DOC_CHARS = 1200


def _load_published_records(limit: int) -> list[dict]:
    """发布件记录（限量）→ [{doc_id, title, text}]（标题 + 正文前缀；只取嵌入所需字段）。

    路径经 `interfaces.clean_index_api.published_dir()`（访问器纪律，不拼兄弟模块目录）。
    """
    from interfaces.clean_index_api import published_dir

    p = os.path.join(published_dir(), "external_records.jsonl")
    if not os.path.exists(p):
        return []
    out: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if len(out) >= limit:
                break
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rid = str(rec.get("record_id") or "").strip()
            title = str(rec.get("title") or "").strip()
            if not rid or not title:
                continue
            body = str(rec.get("body_text") or "")[:_DOC_CHARS]
            out.append({"doc_id": f"rec:{rid}", "title": title, "text": f"{title}\n{body}"})
    return out


# --------------------------------------------------------------------------- #
# 构建 / 检索
# --------------------------------------------------------------------------- #
def _fingerprint() -> dict:
    """嵌入链指纹（模型 + 依赖版本 + 权重 sha；随产物落盘，断点幂等可判）。"""
    try:
        from std_lib.common_lib import semantic_models as sm

        name = _choose_model()
        return sm.embedder_fingerprint(name) if name else {"model": "", "ready": False}
    except Exception as e:  # noqa: BLE001  指纹失败不阻断（披露即可）
        return {"model": "", "ready": False, "error": f"{type(e).__name__}: {e}"}


def _choose_model() -> str | None:
    """候选链中第一个就绪的嵌入模型（无则 None → 调用方走降级披露）。"""
    try:
        from std_lib.common_lib import semantic_enhance as se

        return se.choose_embedder()
    except Exception:  # noqa: BLE001
        return None


def _embed(texts: list[str]) -> tuple[bool, list, str, str]:
    """→ (ok, vectors, model, notice)。不可用不抛（ok=False 供调用方披露）。"""
    from std_lib.common_lib import semantic_enhance as se

    res = se.embed(texts)
    return bool(res.ok), list(res.vectors or []), str(res.model or ""), str(res.notice or "")


def build(*, records_limit: int = 1000, with_ab: bool = True) -> dict:
    """构建/刷新语义检索索引 + 落披露产物。**永不抛断主链**（不可用 → rc=0 披露）。"""
    from std_lib.common_lib import vector_store as vs

    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(OUT_DIR, f"semantic_search_{stamp}.json")
    result: dict = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "out": os.path.relpath(out_path, paths.ROOT).replace(os.sep, "/"),
        "backend": vs.backend(),
        "tables": [T_DOCS, T_RECORDS],
    }

    docs = _load_delivery_docs()
    records = _load_published_records(records_limit)
    result["inputs"] = {"delivery_docs": len(docs), "published_records": len(records)}

    if vs.backend() != "pgvector":
        # 降级披露（合法状态）：sqlite_vec/none 均不在本工具写路径实现范围内
        # （vector_store.upsert/search 仅实现 PG；sqlite_vec 为声明级降级档）。
        result.update(
            {
                "ok": False,
                "degraded": True,
                "note": vs.fallback_hint() + "；本次未建索引（既有 FTS5 检索路径不受影响）",
                "fingerprint": _fingerprint(),
            }
        )
        _write(out_path, result)
        return result

    ok_d, vec_d, model, notice = _embed([d["text"] for d in docs]) if docs else (True, [], "", "")
    ok_r, vec_r, _m2, notice_r = _embed([r["text"] for r in records]) if records else (True, [], "", "")
    result["fingerprint"] = _fingerprint()
    if not (ok_d and ok_r):
        result.update(
            {
                "ok": False,
                "degraded": True,
                "note": f"嵌入链不可用（{notice}{' | ' + notice_r if notice_r else ''}）→ 未建索引",
            }
        )
        _write(out_path, result)
        return result

    try:
        # 表自供给（vec schema CREATE 权已授 orchestrator_app；L-b 派生层语义：
        # 可随时重建，不靠人工脚本）。维度取实际向量宽度（同一模型两表同宽）。
        if docs and vec_d:
            vs.ensure_table(T_DOCS, len(vec_d[0]))
        if records and vec_r:
            vs.ensure_table(T_RECORDS, len(vec_r[0]))
        n_docs = vs.upsert(T_DOCS, [(d["doc_id"], v) for d, v in zip(docs, vec_d, strict=True)])
        n_recs = vs.upsert(T_RECORDS,
                           [(r["doc_id"], v) for r, v in zip(records, vec_r, strict=True)])
    except Exception as e:  # noqa: BLE001  写失败 = 降级披露（不抛断主链）
        result.update(
            {
                "ok": False,
                "degraded": True,
                "note": f"向量写入失败（{type(e).__name__}: {str(e)[:160]}）→ 未建索引",
            }
        )
        _write(out_path, result)
        return result

    result.update({"ok": True, "degraded": False, "model": model,
                   "embedded": {"docs": n_docs, "records": n_recs}})
    if with_ab:
        result["ab"] = _ab_probe(model, {r["doc_id"] for r in records})
        result["ab_note"] = _AB_LIMIT_NOTE
    _write(out_path, result)
    return result


def _write(path: str, result: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


def query(text: str, *, k: int = 5) -> dict:
    """只读检索：交付物 + 发布件记录两个表联合 top-k。"""
    from std_lib.common_lib import vector_store as vs

    if vs.backend() != "pgvector":
        return {"ok": False, "note": vs.fallback_hint(), "hits": []}
    ok, vecs, _model, notice = _embed([text])
    if not ok or not vecs:
        return {"ok": False, "note": f"嵌入不可用：{notice}", "hits": []}
    hits: list[dict] = []
    for table in (T_DOCS, T_RECORDS):
        try:
            for doc_id, dist in vs.search(table, vecs[0], k, metric="cosine"):
                hits.append({"table": table, "doc_id": doc_id, "distance": round(float(dist), 6)})
        except Exception as e:  # noqa: BLE001  单表失败不拖垮另一表
            hits.append({"table": table, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    hits = [h for h in hits if "error" not in h]
    hits.sort(key=lambda h: h["distance"])
    return {"ok": True, "query": text, "k": k, "hits": hits[: k * 2]}


def _ab_probe(model: str, indexed_ids: set[str]) -> dict:
    """A/B 探针：固定查询集 × {向量 top-k, FTS5 top-k} → overlap@k（均值）。

    **同子集对比**（公平性前提）：FTS5 侧检索全库后**过滤到本次索引的记录子集**
    再取 top-k——否则"向量只在 1000 条子集里排序、FTS 在 1.3 万条里排序"的
    overlap 恒低，度量失真。子集规模随产物披露（`corpus`）。

    度量意义：向量检索与既有全文检索的**结果集重合度**。高重合 = 向量路径当前
    无增量信息（不值得切主路径）；低重合 + 人工抽样认可 = 有增量（再议扩大）。
    """
    from interfaces import base_api
    from std_lib.common_lib import vector_store as vs

    if not indexed_ids:
        return {"ok": False, "note": "索引子集为空，A/B 未执行"}
    per_query: list[dict] = []
    overlaps: list[float] = []
    for q in AB_PROBES:
        ok, vecs, _m, _n = _embed([q])
        if not ok or not vecs:
            return {"ok": False, "note": "嵌入不可用，A/B 未执行"}
        vec_ids: set[str] = set()
        try:
            for doc_id, _dist in vs.search(T_RECORDS, vecs[0], AB_K, metric="cosine"):
                vec_ids.add(doc_id)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"向量检索失败：{type(e).__name__}: {str(e)[:120]}"}
        try:
            # FTS 侧放大取池（5×k）后过滤到索引子集，保持 FTS 自身排序取 top-k
            pool = base_api.search_external(q, limit=AB_K * 5, kind="records")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "note": f"FTS5 检索失败：{type(e).__name__}: {str(e)[:120]}"}
        fts_ids = [f"rec:{r.get('record_id')}" for r in pool if r.get("record_id")]
        fts_ids = [i for i in fts_ids if i in indexed_ids][:AB_K]
        fts_set = set(fts_ids)
        inter = len(vec_ids & fts_set)
        union = len(vec_ids | fts_set) or 1
        jaccard = inter / union
        overlaps.append(jaccard)
        per_query.append(
            {"query": q, "vec_k": len(vec_ids), "fts_k": len(fts_set),
             "intersection": inter, "jaccard": round(jaccard, 4)}
        )
    return {
        "ok": True,
        "k": AB_K,
        "model": model,
        "probes": len(AB_PROBES),
        "corpus": f"索引子集 {len(indexed_ids)} 条（FTS 过滤到同子集后取 top-k）",
        "mean_jaccard": round(sum(overlaps) / len(overlaps), 4) if overlaps else None,
        "per_query": per_query,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分析交付库语义检索视图（W4 §6.4）")
    ap.add_argument("--build", action="store_true", help="构建/刷新索引（默认动作）")
    ap.add_argument("--query", default="", help="只读检索（自然语言）")
    ap.add_argument("--k", type=int, default=5, help="检索返回条数（默认 5）")
    ap.add_argument("--records-limit", type=int,
                    default=int(os.environ.get("REG_ORCH_SEMANTIC_SEARCH_LIMIT", "1000")),
                    help="发布件记录嵌入上限（默认 1000；env 可调）")
    ap.add_argument("--ab", action="store_true", help="只跑 A/B 探针（不重建索引）")
    ap.add_argument("--no-ab", action="store_true", help="构建时跳过 A/B 探针")
    args = ap.parse_args(argv)

    if args.query:
        res = query(args.query, k=args.k)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return ExitCode.OK

    if args.ab:
        model = _choose_model() or ""
        # 索引子集 = 当前 PG 表中已有的 rec:* 行（不重建索引，只比对现状）
        indexed: set[str] = set()
        try:
            from std_lib.common_lib import vector_store as _vs

            ok_v, qv, _m, _n = _embed(["子集探针"])
            if ok_v and qv:
                # 取全表 top-N 拿 id 集（N 取大的池；距离不影响成员枚举）
                for doc_id, _d in _vs.search(T_RECORDS, qv[0], 100000, metric="cosine"):
                    indexed.add(doc_id)
        except Exception:  # noqa: BLE001  探针降级：拿不到子集就如实披露
            indexed = set()
        res = _ab_probe(model, indexed)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return ExitCode.OK

    res = build(records_limit=args.records_limit, with_ab=not args.no_ab)
    print(json.dumps({k: v for k, v in res.items() if k != "ab"}, ensure_ascii=False, indent=1))
    if res.get("ab"):
        ab = res["ab"]
        print(f"[semantic_search] A/B（k={ab.get('k')}）：mean_jaccard="
              f"{ab.get('mean_jaccard')}（{ab.get('probes')} 探针）")
    if res.get("ok"):
        print(f"[semantic_search] 索引已刷新 → {res['out']}")
    else:
        # 降级是合法状态：显式披露但不失败（增强层纪律）
        print(f"[semantic_search] 降级披露：{res.get('note', '')}（rc=0，既有 FTS5 路径不受影响）")
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
