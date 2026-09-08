# -*- coding: utf-8 -*-
"""
scripts/build_draft_clause_view.py — 条款级端到端对照素材编排（二期收口，2026-09-08）

把「merged_view（制度↔监管 RFN 依据） × R21 clauses（条文级结构）」串成**条款级对照视图**，
输出到 modules/internal_policy_drafter/data/draft_clause/，供六件套之「条款对照表/立法依据」
起草时引用（自动化打底，终稿仍人工/LLM 完善）：

每制度输出 md：
  # 《title》 条款对照素材
  §1 引用监管依据（merged_view associated_rfns：RFN/名称/文号/时效由 rfn 权威）
  §2 逐条条款对照（clauses 条文 → 正文《X》书名号 + 监管文号核心 自动链接到依据；
      命中标注 RFN，未命中但为监管文号 → ⚠ 待核（疑漏关联或臆造））

用法：
  python build_draft_clause_view.py --ipn IPN-xxx    # 单制度
  python build_draft_clause_view.py --all            # 全部 107（幂等覆盖）
正则语义与 verify_regulatory_citations（drafter 门禁）共用，防止两套口径漂移。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import verify_regulatory_citations as vrf  # 同目录 drafter 门禁（复用 文号/机关词/书名号口径）

_HERE = os.path.dirname(os.path.abspath(__file__))
_DR = os.path.dirname(_HERE)                              # internal_policy_drafter/
_MODULES = os.path.dirname(os.path.dirname(_HERE))        # modules/
_ORCH_ROOT = os.path.dirname(_MODULES)
for _p in (_ORCH_ROOT, os.path.join(_MODULES, "regulatory_classifier")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from rfn import get_index  # noqa: E402

PROC = os.path.join(_MODULES, "internal_policy_base", "data", "processed")
MERGED = os.path.join(_MODULES, "internal_policy_base", "data", "merged_view.json")
OUT = os.path.join(_DR, "data", "draft_clause")

_ELIDE = 130


def _elide(s: str, n: int = _ELIDE) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def _strip_art_head(body: str) -> str:
    """剥离条文 body 行首「第X条」前缀（R21 render 同口径；clauses.json body 以起条文行开头）。"""
    m = re.match(r"^第\s*[0-9一二三四五六七八九十百千万零〇两]+\s*条[、．.．。\s]?", (body or "").strip())
    return (body or "").strip()[m.end():].strip() if m else (body or "").strip()


def _extract_docno_refs(body: str):
    """返回 [(year, seq, 原文, pre10)]——与 vrf 门禁同口径的括号式监管文号候选。"""
    out = []
    for m in vrf.DOCNO_CORE_PAT.finditer(body):
        year = m.group(1) or m.group(3)
        seq = m.group(2) or m.group(4)
        if not (year and seq):
            continue
        pre = body[max(0, m.start() - 10):m.start()]
        if any(pre.endswith(p) for p in vrf.ORGAN_PREFIXES):
            out.append((year, seq, m.group(0)))
    return out


def _link_assoc(body: str, assoc: list[dict]) -> list[str]:
    """body 内 书名号《X》/监管文号 与 associated_rfns 匹配，返回链接说明行。"""
    lines = []
    for t in vrf.TITLE_PAT.findall(body):
        nt = re.sub(r"[《》\s]", "", t)
        hit = next((a for a in assoc
                    if nt and (nt == re.sub(r"[《》\s]", "", a.get("title", ""))
                               or nt in a.get("title", "") or a.get("title", "") in nt)), None)
        if hit:
            lines.append(f"链接 {hit['rfn']}《{_elide(t, 40)}》(书名号)")
    for year, seq, raw in _extract_docno_refs(body):
        hit = next((a for a in assoc
                    if vrf._norm_docno(a.get("docno", "")).endswith(f"{year}{seq}")), None)
        if hit:
            lines.append(f"链接 {hit['rfn']} {raw}（文号核心 {year}{seq}）")
        else:
            lines.append(f"⚠ 监管文号 {raw} 未命中本制度 associated_rfns（待核：漏关联/需增补依据）")
    return lines


def build_one(ipn: str, meta: dict, clauses: dict, view_rec: dict, idx) -> tuple[dict, str]:
    """生成单制度素材 md，返回 (统计, md 文本)。"""
    title = meta.get("title") or view_rec.get("title") or ipn
    assoc = view_rec.get("associated_rfns", []) if view_rec else []
    rfns = [a.get("rfn", "") for a in assoc]
    lines = [f"# 《{title}》条款对照素材（端到端自动编排）", ""]
    lines.append(f"> {ipn} | {view_rec.get('docno', '')} | file_type={view_rec.get('file_type', '')} "
                 f"| 主题={view_rec.get('primary_theme', '')} | status={view_rec.get('status', '')}")
    lines.append("> 生成：build_draft_clause_view.py（merged_view × R21 clauses 条款级编排；草稿打底，终稿人工复核）")
    lines.append("")
    lines.append("## 1 引用监管依据（merged_view / RFN 权威）")
    lines.append("")
    if assoc:
        lines.append("| RFN | 监管文件 | 文号 | 匹配 |")
        lines.append("|---|---|---|---|")
        for a in assoc:
            rec = idx.by_rfn(a["rfn"])
            st = (rec or {}).get("时效状态", "")
            lines.append(f"| {a['rfn']} | {a.get('title', '')} | {a.get('docno', '')} "
                         f"| {a.get('matched_by', '')}（时效 {st or '?'}） |")
    else:
        lines.append("（无关联监管依据——制度纯内部治理或对齐待补）")
    lines.append("")

    arts = clauses.get("articles") or []
    linked_total = warn = 0
    lines.append(f"## 2 逐条条款对照（条文 {len(arts)} 条）")
    lines.append("")
    for a in arts:
        num = a.get("number", "")
        body = (a.get("body") or "").strip()
        if not num or not body:
            continue
        lines.append(f"### {num} {_elide(_strip_art_head(body), 90)}")
        links = _link_assoc(body, assoc)
        if links:
            linked_total += sum(1 for x in links if x.startswith("链接"))
            warn += sum(1 for x in links if x.startswith("⚠"))
            for x in links:
                lines.append(f"- {x}")
        else:
            lines.append("- （本条正文未见《X》/监管文号显式引用）")
        lines.append("")
    stat = {"ipn": ipn, "title": title, "articles": len(arts), "rfn_links": linked_total,
            "unlinked_regulatory_refs": warn, "assoc_rfns": rfns}
    return stat, "\n".join(lines).strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="条款级端到端对照素材编排（六件套打底）")
    ap.add_argument("--ipn", default="", help="单制度 IPN-xxx")
    ap.add_argument("--all", action="store_true", help="全部 merged 制度")
    a = ap.parse_args()

    if not os.path.exists(MERGED):
        print("[draft] merged_view 缺失：先运行 `python cli.py internal merged`")
        return 1
    os.makedirs(OUT, exist_ok=True)
    view = json.load(open(MERGED, encoding="utf-8"))
    idx = get_index()
    recs = {r.get("ipn"): r for r in view.get("records", [])}
    targets = [a.ipn] if a.ipn else sorted(recs)
    total = {"done": 0, "articles": 0, "links": 0, "warns": 0, "no_clause": []}
    for ipn in targets:
        rec = recs.get(ipn)
        if not rec:
            print(f"[draft] merged 无 {ipn}，跳过")
            continue
        meta_p = os.path.join(PROC, ipn + ".json")
        meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
        cl_p = os.path.join(PROC, ipn + "_clauses.json")
        if not os.path.exists(cl_p):
            total["no_clause"].append(ipn)
            continue
        clauses = json.load(open(cl_p, encoding="utf-8"))
        st, text = build_one(ipn, meta, clauses, rec, idx)
        out_p = os.path.join(OUT, f"{ipn}_条款对照素材.md")
        with open(out_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        total["done"] += 1
        total["articles"] += st["articles"]
        total["links"] += st["rfn_links"]
        total["warns"] += st["unlinked_regulatory_refs"]
        print(f"  {ipn} | {meta.get('title', rec.get('title', ''))[:26]} | 条文 {st['articles']} | "
              f"链接 {st['rfn_links']} | 待核文号 {st['unlinked_regulatory_refs']} → {os.path.relpath(out_p, _DR)}")
    print(f"[draft] 完成 {total['done']} 份 | 条文 {total['articles']} | RFN 条款链接 {total['links']} | "
          f"⚠ 待核监管文号 {total['warns']} | 无条文结构 {len(total['no_clause'])} {total['no_clause'][:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
