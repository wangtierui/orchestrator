# -*- coding: utf-8 -*-
"""tools.business_review_list — 生成**业务裁定清单**（可交付 Markdown，只读）。

为什么需要它
------------
待办积压里有大量条目**机器不可判**：相关性边界（是否属监管相关性范围）与
内部制度未对齐（是否需对齐外部法规）都需要业务口径。直接甩 277 条给业务
等于没交付——必须**先聚类、再给裁定问题、附判定依据**，让业务能**按组批量裁定**
而不是逐条读。

分组依据（**有据，不臆造**）
--------------------------
`relevance_boundary` 的 payload 带 `hits`，形如
`[[组1命中词…], [组2…], [组3…], [组4…]]`（`filter_clean_relevance` 的
`hit_a/b/c/x`）。**空数组 = 该组未命中**。故「命中组集合」天然构成分类维度：
同组的边界案例，其"相关性不足"的原因是同一类 ⇒ 可批量裁定。

`internal_unaligned` 的 payload 只有 `ipn/method/title`（`method` 恒 `unaligned`）
⇒ 无机器维度，按**标题关键词**聚类（税务/人事/财务/业务/合规…），并如实标注
"聚类为辅助阅读，非判定依据"。

输出
----
`reports/business_review/business_review_<date>.md`（Markdown，含：
每组的条数、裁定问题、判定依据样例、逐条名称/文号/访问地址）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode
from interfaces import worklist_fields as WF

#: `relevance_boundary` 的 hits 组的**可读名**（按 `filter_clean_relevance` 的
#: `hit_a/hit_b/hit_c/hit_x` 顺序）。若上游改了组语义，本表需同步——故只在
#: **展示层**用它，聚类逻辑本身只依赖"哪几组命中"（不依赖组名）。
HIT_GROUP_NAMES = ("主体类", "渠道类", "产品类", "机构类")

#: `internal_unaligned` 的标题主题词（**辅助阅读**，非判定依据）。
_INTERNAL_TOPICS = (
    ("税务/发票", ("税", "发票", "增值税")),
    ("人事/员工", ("员工", "人事", "入离司", "考勤", "薪酬", "培训", "招聘")),
    ("财务/费用", ("费用", "预算", "报销", "核算", "财务", "资金")),
    ("采购/外包", ("采购", "供应商", "外包", "招标")),
    ("业务/核保", ("核保", "承保", "理赔", "保全", "保单", "销售", "渠道", "银保")),
    ("合规/风控", ("合规", "风控", "风险", "反洗钱", "内控", "审计", "问责")),
    ("信息/数据", ("信息", "数据", "系统", "网络", "安全", "档案")),
    ("行政/会议", ("会议", "行政", "印章", "公文", "档案", "车辆", "接待")),
)


def _conn() -> sqlite3.Connection:
    return sqlite3.connect("file:" + os.path.join(paths.DATA_DIR, "governance.db")
                           + "?mode=ro", uri=True)


def _load(conn, kind: str) -> list[dict]:
    rows = conn.execute(
        "SELECT item_id, kind, subject, payload_json FROM worklist "
        "WHERE status='open' AND kind=? ORDER BY created_at", (kind,)).fetchall()
    out = []
    for iid, k, subj, pj in rows:
        try:
            p = json.loads(pj or "{}")
        except ValueError:
            p = {}
        it = {"item_id": iid, "kind": k, "subject": subj, "payload": p}
        it["fields"] = WF.derive(it)
        out.append(it)
    return out


def _hit_pattern(p: dict) -> str:
    """把 `hits` 压成「命中了哪几组」的短标签（聚类键）。"""
    hits = p.get("hits")
    if not isinstance(hits, list) or not hits:
        return "无 hits"
    idx = [i + 1 for i, g in enumerate(hits) if g]
    if not idx:
        return "四组全空"
    return "＋".join(f"组{i}" for i in idx)


def _kw_sample(p: dict, limit: int = 6) -> str:
    """取该条命中的关键词样例（业务据此判断"这类命中算不算相关"）。"""
    hits = p.get("hits") or []
    words: list[str] = []
    for g in hits:
        if isinstance(g, list):
            words.extend(str(x) for x in g)
    uniq = list(dict.fromkeys(words))
    return "、".join(uniq[:limit]) + ("…" if len(uniq) > limit else "")


def _internal_topic(title: str) -> str:
    t = str(title or "")
    for name, kws in _INTERNAL_TOPICS:
        if any(k in t for k in kws):
            return name
    return "未归类（需人工看标题）"


def _md_escape(s: object) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _row(it: dict) -> str:
    f = it["fields"]
    title = f.get("title") or it.get("subject") or ""
    doc = f.get("document_number") or ""
    url = f.get("url") or ""
    if f.get("aggregate"):
        return f"| （汇总项） | {_md_escape(title)} | — | — |"
    url_cell = f"[打开]({url})" if url else (
        "未查到" if f.get("url_src") == "not_found" else "—")
    return (f"| `{it['item_id']}` | {_md_escape(title[:80])} | "
            f"{_md_escape(doc)} | {url_cell} |")


def build(out_dir: str) -> dict:
    conn = _conn()
    try:
        rb = _load(conn, "relevance_boundary")
        iu = _load(conn, "internal_unaligned")
        tmb = _load(conn, "trigger_manual_breakpoint")
        drift = _load(conn, "rfn_clean_drift_c2")
        tconf = _load(conn, "timeliness_conflict")
        qt = _load(conn, "clean_quarantine_triage")
    finally:
        conn.close()

    now = datetime.now().astimezone()
    lines: list[str] = []
    A = lines.append
    A(f"# 业务裁定清单（{now:%Y-%m-%d %H:%M}）")
    A("")
    A("> 本清单由 `tools/business_review_list.py` 生成，**只读、不改任何数据**。")
    A("> 处置方式：在确认台 P4 按 kind 筛选后批量处置，或用")
    A("> `python -m tools.backlog_governance decide --kind <kind> --by <你> --reason <口径>`。")
    A("")
    A("## 总览")
    A("")
    A("| kind | open 条数 | 是否机器可判 | 需要业务给的口径 |")
    A("|---|---|---|---|")
    A(f"| `relevance_boundary` | {len(rb)} | ❌ | 这类命中模式是否属「监管相关性」范围 |")
    A(f"| `internal_unaligned` | {len(iu)} | ❌ | 内部制度是否**必须**对齐外部法规（或可豁免） |")
    A(f"| `trigger_manual_breakpoint` | {len(tmb)} | ⚠️ 逐条 | 历史技术决策：删/改/维持（见附件 A） |")
    A(f"| `rfn_clean_drift_c2` | {len(drift)} | ⚠️ 需核实 | 归属表与 clean 冲突时**以哪侧为准** |")
    A(f"| `timeliness_conflict` | {len(tconf)} | ⚠️ 需定夺 | 同一文号多个冲突时效结论，取哪个 |")
    A(f"| `clean_quarantine_triage` | {len(qt)} | ✅ 已定 | 源侧无正文，隔离正确（见附件 C） |")
    A("")

    # ── 1. relevance_boundary ──────────────────────────────────────────
    A("## 一、`relevance_boundary`：相关性边界（需业务定「是否纳入监管相关性范围」）")
    A("")
    A("**判定依据从哪来**：每条 payload 带 `hits` = 四组关键词的命中结果，"
      "空数组表示该组未命中。**同一「命中模式」的条目，其相关性不足的原因同类** ⇒ "
      "可按组批量裁定。")
    A("")
    agg = [it for it in rb if it["fields"].get("aggregate")]
    indiv = [it for it in rb if not it["fields"].get("aggregate")]
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in indiv:
        groups[_hit_pattern(it["payload"])].append(it)
    A(f"共 **{len(rb)}** 条（其中 {len(agg)} 条为「超过单次登记上限」的汇总项，"
      f"不含单条文件）。")
    A("")
    A("| 命中模式 | 条数 | 代表关键词（样例） |")
    A("|---|---|---|")
    for pat, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        A(f"| {pat} | {len(items)} | {_md_escape(_kw_sample(items[0]['payload']))} |")
    A("")
    for pat, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        A(f"### 组：{pat}（{len(items)} 条）")
        A("")
        A("**需裁定**：上述命中模式下，该文件是否应纳入「人身保险监管相关性」体系？")
        A("")
        A("| item_id | 名称 | 文号 | 访问地址 |")
        A("|---|---|---|---|")
        for it in items[:40]:
            A(_row(it))
        if len(items) > 40:
            A(f"| … | 其余 {len(items) - 40} 条（同组，裁定口径相同） | | |")
        A("")
    if agg:
        A("### 汇总项（非单条文件，仅提示规模）")
        A("")
        A("| item_id | 说明 | 文号 | 访问地址 |")
        A("|---|---|---|---|")
        for it in agg:
            A(_row(it))
        A("")

    # ── 2. internal_unaligned ─────────────────────────────────────────
    A("## 二、`internal_unaligned`：内部制度未对齐（需业务定「是否必须对齐外部法规」）")
    A("")
    A("**说明**：`method` 恒为 `unaligned`——即该内部制度**没有找到对应的外部法规依据**。"
      "payload 只有 `ipn/method/title`，无机器可用的判定维度 ⇒ 下方按**标题关键词**"
      "聚类，**仅为便于阅读，不是判定依据**。")
    A("")
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for it in iu:
        by_topic[_internal_topic(it["payload"].get("title")
                                 or it["subject"])].append(it)
    A("| 主题（辅助聚类） | 条数 |")
    A("|---|---|")
    for t, items in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        A(f"| {t} | {len(items)} |")
    A("")
    for t, items in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        A(f"### {t}（{len(items)} 条）")
        A("")
        A("**需裁定**：本组内部制度**必须**对齐到外部法规，还是可以「无外部依据、按内部管理需要」豁免？")
        A("")
        A("| item_id | 名称 | 文号 | 访问地址 |")
        A("|---|---|---|---|")
        for it in items[:40]:
            A(_row(it))
        if len(items) > 40:
            A(f"| … | 其余 {len(items) - 40} 条 | | |")
        A("")

    # ── 3. 需逐条核实的 ───────────────────────────────────────────────
    A("## 三、需逐条核实/定夺的事项")
    A("")
    A(f"### 3.1 `rfn_clean_drift_c2`（{len(drift)} 条）：归属表与 clean 冲突，以哪侧为准")
    A("")
    A("| item_id | RFN | clean 侧标题 | clean 文号 | 冲突类型 |")
    A("|---|---|---|---|---|")
    for it in drift:
        p = it["payload"]
        A(f"| `{it['item_id']}` | {_md_escape(p.get('rfn'))} | "
          f"{_md_escape(str(p.get('clean_title'))[:60])} | "
          f"{_md_escape(p.get('clean_docno'))} | {_md_escape(p.get('reason'))} |")
    A("")
    A(f"### 3.2 `timeliness_conflict`（{len(tconf)} 条）：同一文号多个冲突时效结论")
    A("")
    A("| item_id | 文号 | 名称 | 当前取值 | 冲突原因 |")
    A("|---|---|---|---|---|")
    for it in tconf:
        p = it["payload"]
        A(f"| `{it['item_id']}` | {_md_escape(p.get('document_number'))} | "
          f"{_md_escape(str(p.get('title'))[:50])} | "
          f"{_md_escape(p.get('final_status'))} | {_md_escape(p.get('reason'))} |")
    A("")
    A(f"### 3.3 `clean_quarantine_triage`（{len(qt)} 条）：**已定，无需业务裁定**")
    A("")
    A("四条源侧无正文（附件也为空）⇒ 隔离正确；隔离区机制已验证未污染交付。")
    A("仅 1 条「正文空但附件有文本」已由清洗逻辑修复（下次 clean 自动回填）。")
    A("")
    A("| item_id | 类别 | 条数 | 样例 |")
    A("|---|---|---|---|")
    for it in qt:
        p = it["payload"]
        A(f"| `{it['item_id']}` | {_md_escape(p.get('class'))} | "
          f"{_md_escape(p.get('count'))} | {_md_escape(str(p.get('sample'))[:56])} |")
    A("")

    # ── 4. 附件 A：历史技术决策 ───────────────────────────────────────
    A("## 附件 A：`trigger_manual_breakpoint`（历史技术决策，逐条结论见表）")
    A("")
    A("> 这些是**技术**决策项（非业务口径），评估结论见 `docs/tech_debt.md`；"
      "本清单只列出以便一并处置。")
    A("")
    A("| item_id | 事项 | 待裁决（原文摘要） |")
    A("|---|---|---|")
    for it in tmb:
        p = it["payload"]
        ask = p.get("待裁决") or p.get("建议") or p.get("处置") or ""
        A(f"| `{it['item_id']}` | {_md_escape(it['subject'][:44])} | "
          f"{_md_escape(str(ask)[:120])} |")
    A("")

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"business_review_{now:%Y%m%d_%H%M}.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"path": path, "counts": {
        "relevance_boundary": len(rb), "internal_unaligned": len(iu),
        "trigger_manual_breakpoint": len(tmb), "rfn_clean_drift_c2": len(drift),
        "timeliness_conflict": len(tconf), "clean_quarantine_triage": len(qt),
    }, "groups": {k: len(v) for k, v in groups.items()},
        "topics": {k: len(v) for k, v in by_topic.items()}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成业务裁定清单（只读）")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = a.out_dir or os.path.join(paths.ROOT, "reports", "business_review")
    r = build(out)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"[review] 已生成：{r['path']}")
        print("  条数：", r["counts"])
        print("  相关性命中模式分组：", r["groups"])
        print("  内部制度主题分组：", r["topics"])
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
