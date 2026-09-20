# -*- coding: utf-8 -*-
"""tools/extract_relations.py — 依据/废止关系抽取编排与**三类关系产物**生成（R-F01）

职责（编排层；文本抽取本身在 `std_lib/common_lib/relations.py` 唯一实现）
--------------------------------------------------------------------
1. 读**监管文件**正文（`cleaned/<src>_cleaned_<date>.jsonl` 的 `body_text`，权威轨）
   与**内部制度**正文（`internal_policy_base/data/processed/<ipn>_fulltext.json`）；
2. 调 `RelationPipeline` 抽取依据/废止关系；
3. **实体解析**：目标（名称/文号）→ `RFN`（监管实体）/ `IPN`（内部实体）；
   解析不到则 `matched_by="unresolved"` + **保留原文**（禁止臆造，与 drafter 引用核验同纪律）；
4. 汇总为**一张关系表**（唯一事实源）并导出三类视图。

三类关系（用户要求明确列明，均落在一张表内，靠 `src_kind`/`dst_kind`/`relation` 区分）
------------------------------------------------------------------------------------
| # | 类别 | 筛选 |
|---|------|------|
| 1 | 监管文件的依据关系与废止关系 | `src_kind=regulatory`（dst 通常亦为 regulatory） |
| 2 | 内部制度的依据关系与废止关系 | `src_kind=internal` 且 `dst_kind=internal` |
| 3 | 内部制度与监管文件之间的依据关系 | `src_kind=internal` 且 `dst_kind=regulatory` 且 `relation=basis` |

产物（**唯一事实源 + 派生视图**，均在 `modules/regulatory_classifier/data/relations/`）
------------------------------------------------------------------------------------
- `relations_index.jsonl`：**SSOT**，全部关系（三类混装，每行一关系）
- `cross_basis.jsonl`：**派生视图**（类别 3），供 drafter/base 高频消费
- `relations_stat.json`：生成元信息与质量指标（计数/解析率/未解析样例）
- `docs/reports/监管与制度依据废止关系图谱.md`（`--report` 时）：人类可读摘要

消费方：`regulatory_classifier`（报告/明细）· `internal_policy_base`（merged 关联）·
`internal_policy_drafter`（条款对照素材/引用核验）；统一读取入口 `interfaces/relations_api.py`。

用法
----
    python tools/extract_relations.py                      # 全量（5 源 + 内部制度）
    python tools/extract_relations.py --source nfra        # 单源（调试/增量）
    python tools/extract_relations.py --limit 50 --dry-run # 试跑不落盘
    python tools/extract_relations.py --report             # 额外生成关系图谱报告
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from glob import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "std_lib"), os.path.join(ROOT, "modules"),
           os.path.join(ROOT, "modules", "regulatory_classifier")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config.enums import (  # noqa: E402
    RELATION_KIND,
    RELATION_MATCH_METHOD,
)
from std_lib.common_lib.norm import norm_docno, norm_title_strict  # noqa: E402
from std_lib.common_lib.relations import (  # noqa: E402
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    TARGET_CLASSES,
    TARGET_CORPUS,
    TARGET_ENTITY,
    TARGET_EXTERNAL,
    RelationPipeline,
    classify_target,
    docno_signature,
)

_TZ = timezone(timedelta(hours=8))
GENERATED_BY = "tools/extract_relations.py"

CLEANED_DIR = os.path.join(ROOT, "modules", "regulatory_scrapers", "data", "cleaned")
IPB_DATA = os.path.join(ROOT, "modules", "internal_policy_base", "data")
OUT_DIR = os.path.join(ROOT, "modules", "regulatory_classifier", "data", "relations")
REPORT_PATH = os.path.join(ROOT, "docs", "reports", "监管与制度依据废止关系图谱.md")

# 解析方式 → 置信度（写入产物，供下游按阈值筛选）
CONFIDENCE = {
    "docno_exact": 1.00,
    "docno_sig": 0.95,
    "title": 0.90,
    "title_contains": 0.70,
    "unresolved": 0.00,
}
MIN_CONTAINS_LEN = 4          # 标题包含匹配的最短长度（防"办法"类短词误配）
MIN_SIG_LEN = 6               # 文号签名最短长度（四位年 + 至少两位序号）


# ===========================================================================
# 一、实体索引（监管域 RFN / 内部域 IPN）
# ===========================================================================
def _empty_index() -> dict:
    """实体索引容器。

    - **强索引**（`by_*`）：归属表/主索引 → RFN / IPN（可 join 底座与门禁）
    - **弱索引**（`weak_*`）：cleaned 全集 → `dedup_key`（"已采集但未登记 RFN"的线索；
      关系抽取中大量目标是法律/行政法规，不在 1060 份归属表内，弱索引可避免全部落入 unresolved）
    """
    return {"by_sig": {}, "by_docno": {}, "by_title": {}, "title_list": [], "refs": {},
            "weak_docno": {}, "weak_title": {}, "weak_list": []}


def _add_entity(ix: dict, *, ref: str, name: str, docno: str, extra: dict | None = None) -> None:
    if not ref:
        return
    ix["refs"][ref] = {"ref": ref, "name": name, "docno": docno, **(extra or {})}
    sig = docno_signature(docno)
    if len(sig) >= MIN_SIG_LEN:
        ix["by_sig"].setdefault(sig, ref)
    nd = norm_docno(docno)
    if nd:
        ix["by_docno"].setdefault(nd, ref)
    nt = norm_title_strict(name)
    if nt:
        ix["by_title"].setdefault(nt, ref)
        ix["title_list"].append((nt, ref))


def build_regulatory_index() -> dict:
    """监管域索引：归属表（RFN 事实源）→ by_sig / by_docno / by_title。"""
    from rfn import get_index  # noqa: PLC0415

    ix = _empty_index()
    for r in get_index().rows():
        _add_entity(ix, ref=r.get("监管文件编号", ""), name=r.get("文件名称", ""),
                    docno=r.get("发文字号", ""),
                    extra={"source": r.get("文件来源", ""), "status": r.get("时效状态", "")})
    ix["title_list"].sort(key=lambda t: -len(t[0]))
    return ix


def add_cleaned_weak_index(ix: dict, sources: list[str]) -> int:
    """把 cleaned 全集（title/document_number → dedup_key）加入**弱索引**。

    动机（实测）：关系抽取的目标大量是**法律/行政法规**（如《中华人民共和国银行业监督管理法》
    《民法典》），而归属表（RFN 事实源）只覆盖 1060 份"业务相关文件"，两者交集有限 →
    若只查归属表，解析率会低到 ~6%。弱索引把"**已采集但未登记 RFN**"的目标也接上，
    使关系可用于补登记线索与人工核对（`dst_ref` 仍为空，语义强度由字段区分）。
    """
    n = 0
    for src in sources:
        p = _latest_cleaned_jsonl(src)
        if not p:
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("dedup_key", "")
                if not key:
                    continue
                nt = norm_title_strict(rec.get("title", ""))
                nd = norm_docno(rec.get("document_number", ""))
                if nt:
                    ix["weak_title"].setdefault(nt, key)
                    ix["weak_list"].append((nt, key))
                if nd:
                    ix["weak_docno"].setdefault(nd, key)
                n += 1
    ix["weak_list"].sort(key=lambda t: -len(t[0]))
    return n


def build_internal_index() -> dict:
    """内部域索引：主索引（IPN 事实源）→ by_sig / by_docno / by_title。"""
    p = os.path.join(IPB_DATA, "internal_policy_index.json")
    ix = _empty_index()
    if not os.path.exists(p):
        return ix
    for r in json.load(open(p, encoding="utf-8")).get("records", []):
        _add_entity(ix, ref=r.get("ipn", ""), name=r.get("title", ""),
                    docno=r.get("docno", ""),
                    extra={"extension": r.get("extension", ""),
                           "dept": r.get("drafting_dept", "")})
    ix["title_list"].sort(key=lambda t: -len(t[0]))
    return ix


def resolve_target(name: str, docno: str, index: dict, *,
                   strict: bool = False) -> tuple[str, str, str]:
    """目标（名称/文号）→ `(strong_ref, matched_by, weak_key)`。

    - `strong_ref`：RFN / IPN（**强实体**，可 join 归属表/底座/门禁）；
    - `weak_key`：cleaned `dedup_key`（**弱引用**，文件已采集但未登记 RFN）；
    - 二者皆空且 `matched_by="unresolved"`：文本引用的名称未在语料中定位（如《民法典》），
      **保留原文**供人工/法规库补全（**禁止臆造**——与 drafter 引用核验同纪律）。

    `strict=True` 时**禁用** `title_contains`（只允许 `docno_exact/docno_sig/title`）：
    **跨域解析必须严格**——实测 `中华人民共和国发票管理办法`（法规）会经 contains 误配到
    内部制度 `…发票管理办法`，把法规当制度（语义污染）。同域（内部制度废止旧版）则允许
    contains（`…应急预案` vs `…应急预案（2024版）` 属真实版本差异）。
    """
    if docno:
        nd = norm_docno(docno)
        if nd and nd in index["by_docno"]:
            return index["by_docno"][nd], "docno_exact", ""
        sig = docno_signature(docno)
        if len(sig) >= MIN_SIG_LEN:
            # 签名尾匹配（归属表文号前缀机关代字与正文写法常不一致，取"数字尾"对齐）
            for k, ref in index["by_sig"].items():
                if k == sig or k.endswith(sig) or sig.endswith(k):
                    return ref, "docno_sig", ""
        if nd and nd in index.get("weak_docno", {}):
            return "", "docno_exact", index["weak_docno"][nd]
    nt = norm_title_strict(name)
    if nt:
        if nt in index["by_title"]:
            return index["by_title"][nt], "title", ""
        if not strict and len(nt) >= MIN_CONTAINS_LEN:
            for cand, ref in index["title_list"]:      # 已按长度降序 → 最长者优先
                if len(cand) < MIN_CONTAINS_LEN:
                    break
                if cand in nt or nt in cand:
                    return ref, "title_contains", ""
        if nt in index.get("weak_title", {}):
            return "", "title", index["weak_title"][nt]
        if not strict and len(nt) >= MIN_CONTAINS_LEN:
            for cand, key in index.get("weak_list", []):
                if len(cand) < MIN_CONTAINS_LEN:
                    break
                if cand in nt or nt in cand:
                    return "", "title_contains", key
    return "", "unresolved", ""


# ===========================================================================
# 二、正文迭代
# ===========================================================================
def _latest_cleaned_jsonl(source: str) -> str:
    fs = sorted(glob(os.path.join(CLEANED_DIR, f"{source}_cleaned_*.jsonl")))
    return fs[-1] if fs else ""


def iter_regulatory_docs(sources: list[str], limit: int = 0):
    """迭代监管文件（正文取 cleaned JSONL 的 body_text；权威轨）。"""
    for src in sources:
        p = _latest_cleaned_jsonl(src)
        if not p:
            print(f"[relations] 跳过源 {src}：无 cleaned JSONL")
            continue
        n = 0
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if limit and n >= limit:
                    break
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = rec.get("body_text") or rec.get("summary") or ""
                if not text.strip():
                    continue
                n += 1
                yield {
                    "doc_kind": "regulatory", "source": src,
                    "name": rec.get("title", ""), "docno": rec.get("document_number", ""),
                    "url": rec.get("source_url", ""), "text": text,
                    "extra_text": rec.get("attachment_content") or "",
                    "dedup_key": rec.get("dedup_key", ""),
                }
        print(f"[relations] 源 {src}：正文可抽取 {n} 份（{os.path.basename(p)}）")


def iter_internal_docs(limit: int = 0):
    """迭代内部制度（正文取 processed/<ipn>_fulltext.json）。"""
    p = os.path.join(IPB_DATA, "internal_policy_index.json")
    if not os.path.exists(p):
        print("[relations] 跳过内部制度：缺 internal_policy_index.json")
        return
    recs = json.load(open(p, encoding="utf-8")).get("records", [])
    n = 0
    for r in recs:
        if limit and n >= limit:
            break
        ipn = r.get("ipn", "")
        fp = os.path.join(IPB_DATA, "processed", f"{ipn}_fulltext.json")
        if not os.path.exists(fp):
            continue
        try:
            text = json.load(open(fp, encoding="utf-8")).get("text") or ""
        except (OSError, ValueError):
            continue
        if not text.strip():
            continue
        n += 1
        yield {
            "doc_kind": "internal", "source": "internal",
            "ref": ipn, "name": r.get("title", ""), "docno": r.get("docno", ""),
            "url": "", "text": text, "extra_text": "",
        }
    print(f"[relations] 内部制度：正文可抽取 {n} 份")


# ===========================================================================
# 三、关系行构建（统一 schema，三类关系同表）
# ===========================================================================
def _relation_id(src_kind: str, src_ref: str, relation: str,
                 dst_ref: str, normalized_name: str, *,
                 src_key: str = "", dst_kind: str = "", dst_docno: str = "",
                 article: str = "", action: str = "", scope: str = "",
                 reason: str = "") -> str:
    """关系行稳定去重键 `REL-<16hex>`。

    2026-09-20（相邻项修复，extractor 1.0 → 1.1）：纳入此前**遗漏的判别字段**
    （`article/action/scope/reason/dst_docno/dst_kind/src_key`）。旧式仅取 5 元组，
    同一对文件在同一文档的不同条款、或不同处置方式（废止/部分废止/另有规定）下会派生
    **同一 id**（实测 5266 行仅 4859 个 id、366 个 id 命中 2 行且行内容互不相同），
    迫使治理库改用合成 `row_key` 主键（保全了行数，但 id 语义失真）。
    """
    raw = "|".join([src_kind, src_ref, relation, dst_ref, normalized_name,
                    src_key, dst_kind, dst_docno, article, action, scope, reason])
    return "REL-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _ensure_unique_ids(rows: list[dict]) -> int:
    """关系 id **全局唯一化**（确定性）：完全同判别字段的重复引用按出现序加 `-N` 后缀。

    返回被改写的行数（写入统计，供审计；正常情况下应为 0）。
    """
    seen: dict[str, int] = {}
    n = 0
    for r in rows:
        rid = r.get("relation_id") or ""
        if rid not in seen:
            seen[rid] = 1
            continue
        seen[rid] += 1
        r["relation_id"] = f"{rid}-{seen[rid]}"
        n += 1
    return n


def build_rows(doc: dict, pipeline: RelationPipeline, reg_ix: dict, int_ix: dict,
               *, generated_at: str) -> tuple[list[dict], list[str], int]:
    """单篇文档 → `(关系行列表, 警告, 被过滤的泛指词目标数)`（含跨域解析）。"""
    text = doc["text"]
    res = pipeline.extractor.extract(text)
    src_kind = doc["doc_kind"]
    # 源侧实体：监管取归属表解析（本条记录的文号/标题），内部取 IPN
    src_key = ""
    if src_kind == "regulatory":
        src_ref, _mb, src_key = resolve_target(doc["name"], doc["docno"], reg_ix)
        src_ref = src_ref or ""
        src_key = src_key or doc.get("dedup_key", "")
    else:
        src_ref = doc.get("ref", "")

    rows: list[dict] = []
    # 同域优先、再跨域：监管侧只解析监管域；内部侧先内部域再监管域
    for item, relation in [(b, "basis") for b in res.basis] + [(r, "repeal") for r in res.repeal]:
        name = item.target_name
        docno = getattr(item, "target_docno", "")
        dst_ref, matched_by, dst_key = resolve_target(name, docno, reg_ix)
        dst_kind = "regulatory"
        if not dst_ref and src_kind == "internal":
            # 跨域回退：**严格匹配**（禁 contains，防"法规名 ⊃ 制度名"误配）
            i_ref, i_mb, _ = resolve_target(name, docno, int_ix, strict=True)
            if i_ref:
                dst_ref, matched_by, dst_kind, dst_key = i_ref, i_mb, "internal", ""
        rows.append({
            "relation_id": _relation_id(
                src_kind, src_ref or src_key or doc["name"], relation,
                dst_ref or dst_key, item.normalized_name,
                src_key=src_key, dst_kind=dst_kind, dst_docno=docno,
                article=item.article,
                action=getattr(item, "action", "") if relation == "repeal" else "",
                scope=getattr(item, "scope", "") if relation == "repeal" else "",
                reason=getattr(item, "reason", "") if relation == "repeal" else ""),
            "src_kind": src_kind, "src_ref": src_ref, "src_key": src_key,
            "src_name": doc["name"],
            "src_docno": doc["docno"], "src_source": doc["source"],
            "dst_kind": dst_kind, "dst_ref": dst_ref, "dst_key": dst_key,
            # 目标性质分类（2026-09-14）：区分"真·文件引用未定位"与"机关名/泛指词"
            "dst_class": classify_target(
                dst_ref=dst_ref, dst_key=dst_key, name=name,
                basis_type=getattr(item, "basis_type", "") if relation == "basis" else ""),
            "dst_name": name,
            "dst_docno": docno, "dst_normalized_name": item.normalized_name,
            "relation": relation,
            "basis_type": getattr(item, "basis_type", "") if relation == "basis" else "",
            "article": item.article,
            "is_explicit": bool(getattr(item, "is_explicit", True)),
            "action": getattr(item, "action", "") if relation == "repeal" else "",
            "scope": getattr(item, "scope", "") if relation == "repeal" else "",
            "reason": getattr(item, "reason", "") if relation == "repeal" else "",
            "matched_by": matched_by,
            "confidence": CONFIDENCE.get(matched_by, 0.0),
            "source_offset": item.offset,
            "source_snippet": item.source_snippet[:400],
            "generated_by": GENERATED_BY,
            "generated_at": generated_at,
        })
    return rows, list(res.warnings), int(res.filtered_generic or 0)


# ===========================================================================
# 四、门面：全流程
# ===========================================================================
def run(*, sources: list[str] | None = None, limit: int = 0, dry_run: bool = False,
        with_internal: bool = True, with_attachments: bool = False,
        report: bool = False) -> dict:
    cfg_sources = sources or ["gov", "mof", "nfra", "pbc", "supp"]
    generated_at = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")
    reg_ix, int_ix = build_regulatory_index(), build_internal_index()
    n_weak = add_cleaned_weak_index(reg_ix, cfg_sources)
    print(f"[relations] 实体索引：监管 {len(reg_ix['refs'])} 个 RFN / 内部 {len(int_ix['refs'])} 个 IPN "
          f"/ 弱索引 {n_weak} 份 cleaned（未登记 RFN 的引用目标可追溯到 dedup_key）")

    pipeline = RelationPipeline()
    rows: list[dict] = []
    warnings: list[str] = []
    docs_stat = {"regulatory": {}, "internal": 0}
    filtered_generic = 0

    for doc in iter_regulatory_docs(cfg_sources, limit=limit):
        docs_stat["regulatory"][doc["source"]] = docs_stat["regulatory"].get(doc["source"], 0) + 1
        if with_attachments and doc.get("extra_text"):
            doc["text"] = doc["text"] + "\n" + doc["extra_text"]
        r, w, fg = build_rows(doc, pipeline, reg_ix, int_ix, generated_at=generated_at)
        rows.extend(r)
        warnings.extend(w)
        filtered_generic += fg

    if with_internal:
        for doc in iter_internal_docs(limit=limit) or []:
            docs_stat["internal"] += 1
            r, w, fg = build_rows(doc, pipeline, reg_ix, int_ix, generated_at=generated_at)
            rows.extend(r)
            warnings.extend(w)
            filtered_generic += fg

    # 关系 id 唯一化（2026-09-20 相邻项修复）：判别字段完全相同的重复引用加确定性后缀
    n_id_dups = _ensure_unique_ids(rows)
    if n_id_dups:
        print(f"[relations] relation_id 撞车 {n_id_dups} 行（同判别字段重复引用）→ 已按出现序加后缀")

    stat = _build_stat(rows, docs_stat, warnings, generated_at,
                       filtered_generic=filtered_generic, id_dups=n_id_dups)
    if dry_run:
        print("[relations] dry-run：不落盘。")
        print(json.dumps({k: stat[k] for k in ("documents", "relations", "resolution")},
                         ensure_ascii=False, indent=2))
        return {"rows": rows, "stat": stat, "written": False}

    written = write_products(rows, stat)
    if report:
        write_report(rows, stat)
    print(f"[relations] 完成：{len(rows)} 条关系 → {written['index']}")
    print(json.dumps({k: stat[k] for k in ("relations", "resolution")}, ensure_ascii=False, indent=2))
    return {"rows": rows, "stat": stat, "written": True, "paths": written}


def _build_stat(rows: list[dict], docs_stat: dict, warnings: list[str],
                generated_at: str, *, filtered_generic: int = 0,
                id_dups: int = 0) -> dict:
    def _cnt(pred) -> int:
        return sum(1 for r in rows if pred(r))

    by_kind = {k: _cnt(lambda r, k=k: r["relation"] == k) for k in sorted(RELATION_KIND)}
    cross = {
        "regulatory->regulatory": _cnt(lambda r: r["src_kind"] == "regulatory" and r["dst_kind"] == "regulatory"),
        "internal->internal": _cnt(lambda r: r["src_kind"] == "internal" and r["dst_kind"] == "internal"),
        "internal->regulatory": _cnt(lambda r: r["src_kind"] == "internal" and r["dst_kind"] == "regulatory"),
        "regulatory->internal": _cnt(lambda r: r["src_kind"] == "regulatory" and r["dst_kind"] == "internal"),
    }
    resolution = {m: _cnt(lambda r, m=m: r["matched_by"] == m) for m in sorted(RELATION_MATCH_METHOD)}
    unresolved = [r for r in rows if r["matched_by"] == "unresolved"]
    n_ref = _cnt(lambda r: bool(r["dst_ref"]))          # 强关联：解析到 RFN/IPN
    n_any = _cnt(lambda r: r["matched_by"] != "unresolved")   # 含弱关联（cleaned dedup_key）

    # ---- 目标性质分层（2026-09-14 口径修正）------------------------------------
    # 只有 `external` 是"真·文件引用未定位"；`organ`（机关名）与 `generic`（泛指词）
    # 不是文件引用，混入分母会把真实覆盖度**系统性低估**。
    by_class = {c: _cnt(lambda r, c=c: r.get("dst_class") == c) for c in TARGET_CLASSES}
    file_denom = by_class[TARGET_ENTITY] + by_class[TARGET_CORPUS] + by_class[TARGET_EXTERNAL]
    file_resolved = round(by_class[TARGET_ENTITY] / file_denom, 4) if file_denom else 0.0
    file_located = (round((by_class[TARGET_ENTITY] + by_class[TARGET_CORPUS]) / file_denom, 4)
                    if file_denom else 0.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "generated_by": GENERATED_BY,
        "generated_at": generated_at,
        "documents": docs_stat,
        "relations": {"total": len(rows), "by_kind": by_kind, "by_cross": cross},
        # 关系 id 唯一性（2026-09-20）：行数 / 不同 id 数 / 撞车改写数 —— 三者须满足
        # `rows == distinct_ids` 且 `id_dups == 0`（gate_relations 判据 9 断言）。
        "relation_id": {"rows": len(rows),
                        "distinct": len({r.get("relation_id") for r in rows}),
                        "renamed": id_dups},
        "resolution": resolution,
        # 两级解析率（口径显式命名，避免"解析率"歧义）
        "resolved_to_entity_ratio": round(n_ref / len(rows), 4) if rows else 0.0,
        "located_in_corpus_ratio": round(n_any / len(rows), 4) if rows else 0.0,
        # ---- 目标性质分层与**文件级**覆盖率（分母排除 organ/generic，2026-09-14）----
        "target_class": by_class,
        "file_level_denominator": file_denom,
        "file_resolved_ratio": file_resolved,
        "file_located_ratio": file_located,
        "filtered_generic_total": filtered_generic,
        "warnings_total": len(warnings),
        "warnings_sample": sorted(set(warnings))[:10],
        "unresolved_sample": [
            {"src_ref": r["src_ref"] or r["src_key"][:12], "relation": r["relation"],
             "dst_class": r.get("dst_class", ""),
             "dst_name": r["dst_name"][:60], "dst_docno": r["dst_docno"][:40]}
            for r in unresolved[:20]
        ],
    }


def write_products(rows: list[dict], stat: dict) -> dict:
    """落盘：SSOT（relations_index.jsonl）+ 派生视图（cross_basis.jsonl）+ 统计。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    index_p = os.path.join(OUT_DIR, "relations_index.jsonl")
    cross_p = os.path.join(OUT_DIR, "cross_basis.jsonl")
    stat_p = os.path.join(OUT_DIR, "relations_stat.json")

    rows_sorted = sorted(rows, key=lambda r: (r["src_kind"], r["src_ref"], r["relation"],
                                              r["dst_ref"], r["dst_name"]))
    _write_jsonl(index_p, rows_sorted)
    cross = [r for r in rows_sorted
             if r["src_kind"] == "internal" and r["dst_kind"] == "regulatory"
             and r["relation"] == "basis"]
    _write_jsonl(cross_p, cross)
    stat["cross_basis_rows"] = len(cross)
    # 就地注入文件信息（调用方（write_report 等）在同一 stat 上继续消费）
    stat["files"] = {
        "relations_index": os.path.relpath(index_p, ROOT).replace(os.sep, "/"),
        "cross_basis": os.path.relpath(cross_p, ROOT).replace(os.sep, "/"),
        "relations_stat": os.path.relpath(stat_p, ROOT).replace(os.sep, "/"),
    }
    with open(stat_p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(stat, fh, ensure_ascii=False, indent=2)
    os.replace(stat_p + ".tmp", stat_p)
    return {"index": index_p, "cross": cross_p, "stat": stat_p, "cross_rows": len(cross)}


def _write_jsonl(path: str, rows: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def render_report(rows: list[dict], stat: dict) -> str:
    """渲染关系图谱正文（**纯函数，不落盘**）。

    F-L01 纳管（2026-09-14）：本工具 `--report`（`write_report`）与 analysis 交付库
    （`tools/gen_analysis_deliveries.py` 2.1.2.4）**共用本实现——单一渲染源，禁止分叉**。
    交付库侧从已落盘产物（`relations_index.jsonl` + `relations_stat.json`）重建，**零重抽取**。
    """
    lines = [
        "# 监管与制度依据·废止关系图谱",
        "",
        f"> 由 `{GENERATED_BY}` 生成于 {stat['generated_at']}（抽取器 {stat['extractor_version']}，"
        f"schema {stat['schema_version']}）。**本文件为派生报告**，事实源为 "
        f"`{stat['files']['relations_index']}`。",
        "",
        "## 一、总览",
        "",
        "| 维度 | 值 |",
        "| :--- | :--- |",
        f"| 关系总数 | {stat['relations']['total']} |",
        f"| 依据关系 / 废止关系 | {stat['relations']['by_kind'].get('basis', 0)} / "
        f"{stat['relations']['by_kind'].get('repeal', 0)} |",
        f"| **文件级强解析率**（RFN/IPN，可 join 底座） | {stat['file_resolved_ratio']:.1%} |",
        f"| **文件级定位率**（含 cleaned 弱引用） | {stat['file_located_ratio']:.1%} |",
        f"| 监管文件（可抽取） | {sum(stat['documents']['regulatory'].values())} 份 |",
        f"| 内部制度（可抽取） | {stat['documents']['internal']} 份 |",
        "",
        "> **口径（2026-09-14 修正）**：目标按**性质**分层（`dst_class`），只有 `external` 是"
        "「真·文件引用未定位」；`organ`（机关名）与 `generic`（泛指词）**不是文件引用**，"
        "不计入分母。",
        "",
        "| `dst_class` | 含义 | 条数 |",
        "| :--- | :--- | ---: |",
        f"| `entity` | 强实体：解析到 RFN / IPN | {stat['target_class'].get('entity', 0)} |",
        f"| `corpus` | 弱引用：已采集（cleaned 命中）但未登记 RFN | {stat['target_class'].get('corpus', 0)} |",
        f"| `organ` | 机关名（程序性依据目标，非文件） | {stat['target_class'].get('organ', 0)} |",
        f"| `generic` | 纯类型泛指词（已过滤，抽取侧不产出） | {stat['target_class'].get('generic', 0)} |",
        f"| `external` | **语料外文件**（法律法规等，客观未采集） | {stat['target_class'].get('external', 0)} |",
        "",
        f"> 文件级分母（entity+corpus+external）= **{stat['file_level_denominator']}** 条；"
        f"另有过滤的泛指词目标 {stat['filtered_generic_total']} 条（抽取侧净化，不产出关系）。",
        "> `corpus` 类即 **RFN 补登候选**：处置入口 `python tools/rfn_backlog.py`"
        "（清单 + `--apply` 批量登记；登记后本表重跑即升级为 `entity`）。",
        "",
        "## 二、三类关系（用户要求明确列明）",
        "",
        "| # | 类别 | 口径 | 关系数 |",
        "| :--- | :--- | :--- | ---: |",
        f"| 1 | 监管文件的依据关系与废止关系 | `src_kind=regulatory` | "
        f"{stat['relations']['by_cross']['regulatory->regulatory']} |",
        f"| 2 | 内部制度的依据关系与废止关系 | `src_kind=internal ∧ dst_kind=internal` | "
        f"{stat['relations']['by_cross']['internal->internal']} |",
        f"| 3 | 内部制度 → 监管文件的依据关系 | `src_kind=internal ∧ dst_kind=regulatory` | "
        f"{stat['relations']['by_cross']['internal->regulatory']} |",
        "",
        f"> 其中类别 3 的**纯依据**边（`relation=basis`）共 **{stat.get('cross_basis_rows', 0)}** 条，"
        f"另存为派生视图 `{stat['files']['cross_basis']}`（供 drafter / base 高频消费）；"
        "其余为「内部制度废止监管文件」的罕见组合（保留在同一事实源内，不另立视图）。",
        "",
        "## 三、实体解析分布",
        "",
        "| matched_by | 条数 | 置信度 |",
        "| :--- | ---: | ---: |",
    ]
    for m, c in sorted(stat["resolution"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{m}` | {c} | {CONFIDENCE.get(m, 0.0):.2f} |")
    lines += ["", "## 四、未解析样例（需人工/清单补全，**不得臆造**）", ""]
    if stat["unresolved_sample"]:
        lines += ["| 源 | 关系 | 目标名称 | 目标文号 |", "| :--- | :--- | :--- | :--- |"]
        for u in stat["unresolved_sample"]:
            lines.append(f"| {u['src_ref'] or '—'} | {u['relation']} | {u['dst_name']} | "
                         f"{u['dst_docno'] or '—'} |")
    else:
        lines.append("（无）")
    lines += ["", "## 五、按源统计", "", "| 源 | 关系数 |", "| :--- | ---: |"]
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["src_source"]] = by_src.get(r["src_source"], 0) + 1
    for k, v in sorted(by_src.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    return "\n".join(lines)


def write_report(rows: list[dict], stat: dict) -> str:
    """生成人类可读关系图谱摘要（classifier 报告族的一份；渲染见 `render_report`）。"""
    content = render_report(rows, stat)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return REPORT_PATH


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="依据/废止关系统一抽取（三类关系产物）")
    ap.add_argument("--source", action="append", default=None,
                    help="限定监管源（可多次；默认 gov/mof/nfra/pbc/supp）")
    ap.add_argument("--no-internal", action="store_true", help="不抽取内部制度")
    ap.add_argument("--with-attachments", action="store_true", help="并入附件正文抽取")
    ap.add_argument("--limit", type=int, default=0, help="每个源最多处理 N 份（0=全部）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    ap.add_argument("--report", action="store_true", help="额外生成关系图谱报告")
    a = ap.parse_args()
    run(sources=a.source, limit=a.limit, dry_run=a.dry_run,
        with_internal=not a.no_internal, with_attachments=a.with_attachments, report=a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
