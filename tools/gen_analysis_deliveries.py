# -*- coding: utf-8 -*-
"""F-L01：规划 2.1 五级分析体系交付库生成器（2026-09-12；关系类纳管 2026-09-14）。

背景：规划 §2.1 定义"主题分类 → 纵向深化 → 横向整合 → 召回复核 → 全景分析"五级分析
交付库（2.1.1.x / 2.1.2.x / 2.1.3.x），此前 0 项按结构产出（交付库不存在）。
本工具把 classifier/published 的既有产物**按结构**生成 MD 交付物到 docs/reports/。

交付项 **17 项** = 2.1.1（5）+ 2.1.2（5）= 2.1.2.1~3 原有 + **2.1.2.4/2.1.2.5 关系类追加**
（2026-09-14 纳管）+ 2.1.3（7）。关系类 2 项**复用各自工具的单源渲染器**、从已落盘关系产物
重建（零重抽取），并在关系事实源缺失时**跳过并告警**（不写占位、不登记）。详见 §2.1.2 追加段。

用法：
  python tools/gen_analysis_deliveries.py                 # 全量生成到 docs/reports/
  python tools/gen_analysis_deliveries.py --out <dir>     # 自定义输出目录
  python tools/gen_analysis_deliveries.py --dry           # 仅统计不写盘

原则：全部数据驱动（每节统计来自实际 JSON/CSV）；无数据时显式标注"数据源缺失"，
不臆造；重跑幂等覆盖；frontmatter 记录数据源与生成时间。
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDATA = os.path.join(ROOT, "modules", "regulatory_classifier", "data")
PUBLISH = os.path.join(ROOT, "modules", "regulatory_scrapers", "published")
NOW = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

THEME_ORDER = [f"T{i}" for i in range(0, 11)]


# --------------------------------------------------------------------------- #
# 基础读取
# --------------------------------------------------------------------------- #
def _sha16(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default if default is not None else {}


def _load_theme_names() -> dict:
    """主题完整名：归属表『主题』列（含 T{N} 前缀的完整名）。"""
    out = {}
    for p in (os.path.join(CDATA, "人身保险公司-文件归属表.csv"),
              os.path.join(CDATA, "人身保险公司-主题归属表.csv")):
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                th = (row.get("主题") or "").strip()
                if th.startswith("T") and th[:2].rstrip("0123456789") == "T":
                    tid = th.split()[0].split("_")[0].strip()
                    if tid[:1] == "T" and tid[1:].isdigit():
                        out.setdefault(tid, th)
    return out


def _load_finals() -> dict:
    out = {}
    for tid in THEME_ORDER:
        p = os.path.join(CDATA, f"_{tid.lower()}_final.json")
        d = _load_json(p, default=[])
        if isinstance(d, list) and d:
            out[tid] = d
    return out


def _load_graphs() -> dict:
    out = {}
    for tid in THEME_ORDER:
        p = os.path.join(CDATA, f"_{tid.lower()}_clause_graph.json")
        d = _load_json(p, default={})
        if isinstance(d, dict) and d.get("edges") is not None:
            out[tid] = d
    return out


def _load_details() -> dict:
    out = {}
    for p in sorted(glob.glob(os.path.join(CDATA, "T*逐份条款引用与上位法依据明细表.csv"))):
        base = os.path.basename(p)
        tid = base.split("_")[0]
        rows = []
        try:
            with open(p, encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        except OSError:
            continue
        out[tid] = {"path": p, "rows": rows}
    return out


def _load_upper_laws() -> dict:
    return _load_json(os.path.join(CDATA, "_upper_laws.json"), default={})


def _load_published_counts() -> dict:
    m = _load_json(os.path.join(PUBLISH, "publish_manifest.json"), default={})
    return (m.get("counts") or {}) if isinstance(m, dict) else {}


def _norm_year(v) -> int:
    try:
        y = int(str(v)[:4])
        return y if 1900 < y < 2100 else 0
    except (ValueError, TypeError):
        return 0


def _n(x) -> str:
    """主题标识归一：clause_graph.dst_theme='1' vs 图 theme='T1'（前缀不一致，2026-09-12 实测）。"""
    return str(x or "").strip().lstrip("Tt")


# --------------------------------------------------------------------------- #
# 通用渲染
# --------------------------------------------------------------------------- #
def _front(stage: str, item: str, sources: list) -> str:
    return "\n".join([
        "---",
        f"delivery: {item}",
        f"stage: {stage}",
        "generated_by: gen_analysis_deliveries",
        f"generated_at: {NOW}",
        "sources:",
        *[f"  - {s}" for s in sources],
        "---",
        "",
    ])


def _table(headers: list, rows: list, empty="（无数据）") -> str:
    if not rows:
        return empty
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "／").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def _mermaid_edges(edges: list) -> str:
    """边清单 → Mermaid flowchart（节点用 RFN 短尾；标注边 kind/count）。"""
    if not edges:
        return "（无关系边）"
    lines = ["```mermaid", "flowchart LR"]
    seen = set()
    for e in edges[:60]:
        s = (e.get("src_rfn") or "")[-6:]
        d = (e.get("dst_rfn") or "")[-6:]
        k = (s, d, e.get("kind") or "")
        if not s or not d or k in seen:
            continue
        seen.add(k)
        lines.append(f'  {s}["{s}"] -->|{e.get("kind") or ""}×{e.get("count") or 1}| {d}["{d}"]')
    lines.append("```")
    return "\n".join(lines)


def _write(outdir: str, name: str, content: str, manifest: list, item: str,
           title: str, sources: list, dry: bool) -> None:
    p = os.path.join(outdir, name)
    if not dry:
        os.makedirs(outdir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    manifest.append({
        "item": item, "title": title, "file": name,
        "lines": content.count("\n") + 1, "chars": len(content),
        # 口径（2026-09-14 统一）：登记**磁盘字节** sha256 前 16 位（`_sha16(落盘文件)`）。
        # 原为「正文串（LF 归一）」哈希 —— Windows 下 `open(...,"w")` 会把 \n 写成 \r\n，
        # 与文件字节不符，不能作一致性判据；现改为读回文件字节，平台无关、可直接比对。
        # dry 模式不落盘 → 置空（与原行为一致）。
        "sha256_16": (_sha16(p) if not dry else ""),
        "sources": [os.path.relpath(s, ROOT).replace("\\", "/") for s in sources if s],
    })


# --------------------------------------------------------------------------- #
# 2.1.1 纵向深化（T1-Tn）
# --------------------------------------------------------------------------- #
def d_211_1(names, finals, details, dry, manifest, outdir) -> None:
    """2.1.1.1 子主题层级结构总览（含层级表）"""
    rows = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        clusters = {}
        for r in recs:
            c = (r.get("cluster") or "（未聚类）").strip() or "（未聚类）"
            clusters.setdefault(c, []).append(r)
        for c in sorted(clusters):
            rs = clusters[c]
            sample = (rs[0].get("title") or "")[:34]
            rows.append((tid, names.get(tid, tid), c, len(rs), sample))
    body = [
        "# 2.1.1.1 子主题层级结构总览",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：classifier `_t*_final.json` cluster 字段）",
        "",
        "## 层级表（主题 → 子主题 → 文件）", "",
        _table(["主题", "主题名", "子主题", "文件数", "示例文件"], rows),
        "",
        f"合计：**{sum(r[3] for r in rows)} 份文件 / {len(rows)} 个子主题**（跨 {len(set(r[0] for r in rows))} 个主题）",
    ]
    _write(outdir, "2.1.1.1_子主题层级结构总览.md", _front("2.1.1", "2.1.1.1", []) + "\n".join(body),
           manifest, "2.1.1.1", "子主题层级结构总览",
           [os.path.join(CDATA, "_t1_final.json")], dry)


def d_211_2(names, finals, dry, manifest, outdir) -> None:
    """2.1.1.2 监管演变阶段与逻辑迁移（含时间线）"""
    # 年度分布 + 状态构成（按主题）；阶段=按年代分组
    year_rows = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        by_year = {}
        for r in recs:
            y = _norm_year(r.get("real_year") or r.get("year_reported"))
            if y:
                by_year[y] = by_year.get(y, 0) + 1
        for y in sorted(by_year):
            year_rows.append((tid, names.get(tid, tid), y, by_year[y]))
    stage_rows = []
    for lo, hi, label in ((0, 2000, "Ⅰ 起步（≤2000）"), (2001, 2009, "Ⅱ 规范（2001-2009）"),
                          (2010, 2017, "Ⅲ 强化（2010-2017）"), (2018, 2026, "Ⅳ 严监管（2018-）")):
        n = sum(c for (_t, _n, y, c) in year_rows if lo <= y <= hi)
        stage_rows.append((label, f"{lo}-{hi}", n))
    total = sum(r[3] for r in year_rows)
    body = [
        "# 2.1.1.2 监管演变阶段与逻辑迁移",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_t*_final.json` real_year/eff_status）",
        "",
        "## 阶段划分（按年代）", "",
        _table(["阶段", "年代区间", "文件数"], stage_rows),
        "",
        "## 年度时间线（主题 × 年份 发文量）", "",
        _table(["主题", "主题名", "年份", "发文数"], year_rows),
        "",
        f"合计：**{total} 份（有年份）**；时间跨度 {min((r[2] for r in year_rows), default='—')}"
        f"–{max((r[2] for r in year_rows), default='—')}",
    ]
    _write(outdir, "2.1.1.2_监管演变阶段与逻辑迁移.md",
           _front("2.1.1", "2.1.1.2", []) + "\n".join(body), manifest, "2.1.1.2",
           "监管演变阶段与逻辑迁移", [os.path.join(CDATA, "_t1_final.json")], dry)


def d_211_3(names, graphs, dry, manifest, outdir) -> None:
    """2.1.1.3 内部关联关系图（含关系性质说明与实证文件）"""
    all_rows = []
    kind_cnt = {}
    for tid in THEME_ORDER:
        g = graphs.get(tid) or {}
        for e in g.get("edges") or []:
            if _n(e.get("dst_theme")) != _n(tid):
                continue                       # 内部=同主题（归一后比较）
            all_rows.append((tid, names.get(tid, tid), e.get("src_title", "")[:28],
                             e.get("dst_title", "")[:28], e.get("kind", ""),
                             e.get("count") or 1))
            kind_cnt[e.get("kind", "")] = kind_cnt.get(e.get("kind", ""), 0) + (e.get("count") or 1)
    kind_desc = {
        "book_title": "书名号引用（《X》显式引用）",
        "docno": "发文字号引用（〔YYYY〕N号 显式引用）",
        "docno_sig": "文号特征引用（核心数字比对）",
        "title_contains": "标题包含（双向包含匹配）",
    }
    body = [
        "# 2.1.1.3 内部关联关系图",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_t*_clause_graph.json` edges，同主题内部边）",
        "",
        "## 关系性质说明", "",
        _table(["关系类型", "含义", "累计次数"],
               [(k, kind_desc.get(k, "（见 clause_graph 规则）"), v) for k, v in sorted(kind_cnt.items())]),
        "",
        "## 关系图（前 60 边，节点=RFN 短尾）", "",
        _mermaid_edges([x for tid in THEME_ORDER
                        for x in ((graphs.get(tid) or {}).get("edges") or [])
                        if _n(x.get("dst_theme")) == _n(tid)]),
        "",
        "## 关系明细（内部边）", "",
        _table(["主题", "主题名", "源文件", "目标文件", "关系", "次数"], all_rows),
        "",
        f"合计：**{len(all_rows)} 条内部关系边**",
    ]
    _write(outdir, "2.1.1.3_内部关联关系图.md",
           _front("2.1.1", "2.1.1.3", []) + "\n".join(body), manifest, "2.1.1.3",
           "内部关联关系图", [os.path.join(CDATA, "_t1_clause_graph.json")], dry)


def d_211_4(names, details, dry, manifest, outdir) -> None:
    """2.1.1.4 核心文件引用-细化链条表（含法宝核验标记）"""
    rows = []
    n_tl = 0
    for tid in sorted(details):
        for r in details[tid]["rows"]:
            tl = (r.get("时效状态") or "").strip()
            if tl:
                n_tl += 1
            rows.append((tid, names.get(tid, tid), (r.get("标题") or "")[:30],
                         r.get("发文字号") or "", (r.get("立法依据") or "")[:30],
                         (r.get("条款引用") or "")[:26], tl or "—", r.get("核验来源") or "—"))
    body = [
        "# 2.1.1.4 核心文件引用-细化链条表",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：T*明细表 14 列——含 F-L03 法宝核验标记）",
        "",
        "## 引用链条表（文件 → 立法依据 → 条款引用 → 核验）", "",
        _table(["主题", "主题名", "标题", "发文字号", "立法依据", "条款引用", "时效状态", "核验来源"], rows),
        "",
        f"合计：**{len(rows)} 行**；含时效核验标记 **{n_tl} 行**（覆盖 {round(n_tl / len(rows) * 100, 1) if rows else 0}%）",
    ]
    _write(outdir, "2.1.1.4_核心文件引用细化链条表.md",
           _front("2.1.1", "2.1.1.4", []) + "\n".join(body), manifest, "2.1.1.4",
           "核心文件引用-细化链条表", sorted(p for p in
                                          (details[t]["path"] for t in details)), dry)


def d_211_5(names, finals, details, graphs, dry, manifest, outdir) -> None:
    """2.1.1.5 监管体系完整性评估（含缺口清单与完善建议）"""
    gaps = []
    # 缺口A：子主题文件数过少（薄弱）
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        clusters = {}
        for r in recs:
            c = (r.get("cluster") or "（未聚类）").strip() or "（未聚类）"
            clusters[c] = clusters.get(c, 0) + 1
        for c, n in sorted(clusters.items()):
            if n <= 2:
                gaps.append((tid, names.get(tid, tid), "子主题覆盖薄弱", f"{c}（{n} 份）",
                             "补充该子主题监管文件或合并归类"))
    # 缺口B：明细无条款引用
    for tid in sorted(details):
        for r in details[tid]["rows"]:
            if not (r.get("条款引用") or "").strip():
                gaps.append((tid, names.get(tid, tid), "无条款引用", (r.get("标题") or "")[:30],
                             "人工补充条款级引用或标注无需引用"))
    # 缺口C：主题无关联边
    for tid in THEME_ORDER:
        g = graphs.get(tid) or {}
        inner = [e for e in (g.get("edges") or []) if _n(e.get("dst_theme")) == _n(tid)]
        if (finals.get(tid)) and not inner:
            gaps.append((tid, names.get(tid, tid), "主题内无引用关系", f"{len(finals[tid])} 份文件",
                         "核查引用抽取规则是否漏配"))
    body = [
        "# 2.1.1.5 监管体系完整性评估",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：final × 明细 × clause_graph 交叉核算）",
        "",
        "## 缺口清单", "",
        _table(["主题", "主题名", "缺口类型", "缺口实体", "完善建议"], gaps[:200]),
        "",
        f"合计：**{len(gaps)} 项缺口**（明细全量见上表前 200 项；按类型："
        + "、".join(f"{k}:{sum(1 for g in gaps if g[2] == k)}" for k in sorted({g[2] for g in gaps})) + "）",
    ]
    _write(outdir, "2.1.1.5_监管体系完整性评估.md",
           _front("2.1.1", "2.1.1.5", []) + "\n".join(body), manifest, "2.1.1.5",
           "监管体系完整性评估",
           [os.path.join(CDATA, "_t1_final.json"), os.path.join(CDATA, "_t1_clause_graph.json")], dry)


# --------------------------------------------------------------------------- #
# 2.1.2 横向整合
# --------------------------------------------------------------------------- #
def d_212_1(names, finals, dry, manifest, outdir) -> None:
    """2.1.2.1 核心监管主题总览（主题-文件归属表）"""
    rows = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        years = [_norm_year(r.get("real_year") or r.get("year_reported")) for r in recs]
        years = [y for y in years if y]
        st = {}
        for r in recs:
            s = (r.get("eff_status") or "unknown").strip() or "unknown"
            st[s] = st.get(s, 0) + 1
        srcs = {}
        for r in recs:
            s = (r.get("file_src") or "?").strip() or "?"
            srcs[s] = srcs.get(s, 0) + 1
        rows.append((tid, names.get(tid, tid), len(recs),
                     f"{min(years) if years else '—'}–{max(years) if years else '—'}",
                     "、".join(f"{k}:{v}" for k, v in sorted(st.items())),
                     "、".join(f"{k}:{v}" for k, v in sorted(srcs.items()))))
    body = [
        "# 2.1.2.1 核心监管主题总览",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_t*_final.json` × 归属表）",
        "",
        "## 主题-文件归属表", "",
        _table(["主题", "主题名", "文件数", "时间跨度", "效力构成", "来源构成"], rows),
        "",
        f"合计：**{sum(r[2] for r in rows)} 份文件 / {len(rows)} 个主题**",
    ]
    _write(outdir, "2.1.2.1_核心监管主题总览.md",
           _front("2.1.2", "2.1.2.1", []) + "\n".join(body), manifest, "2.1.2.1",
           "核心监管主题总览", [os.path.join(CDATA, "_t1_final.json")], dry)


def d_212_2(names, graphs, dry, manifest, outdir) -> None:
    """2.1.2.2 主题间关联关系图（含关系性质说明）"""
    rows, mat = [], {}
    for tid in THEME_ORDER:
        g = graphs.get(tid) or {}
        for e in g.get("edges") or []:
            dt = _n(e.get("dst_theme"))
            if dt and dt != _n(tid):
                rows.append((tid, names.get(tid, tid), dt, names.get(dt, dt),
                             e.get("src_title", "")[:26], e.get("dst_title", "")[:26],
                             e.get("kind", ""), e.get("count") or 1))
                mat.setdefault(tid, {})[dt] = mat.setdefault(tid, {}).get(dt, 0) + (e.get("count") or 1)
    tids = [t for t in THEME_ORDER if t in graphs]
    mrows = [(t, *[ (mat.get(t, {}).get(d2, 0) or "·") for d2 in tids ]) for t in tids]
    body = [
        "# 2.1.2.2 主题间关联关系图",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_t*_clause_graph.json` 跨主题边）",
        "",
        "## 关系性质说明", "",
        "- 跨主题边 = 某主题文件正文引用其他主题监管文件（书名号/发文字号/标题包含）；",
        "- 矩阵为『行主题 → 列主题』引用累计次数（· 表示无引用）；",
        "",
        "## 主题 × 主题 关联矩阵", "",
        _table(["源\\目标", *tids], mrows),
        "",
        "## 关联明细（含实证文件）", "",
        _table(["源主题", "源主题名", "目标主题", "目标主题名", "证据文件", "被引用文件", "关系", "次数"], rows),
        "",
        f"合计：**{len(rows)} 条跨主题关系边**",
    ]
    _write(outdir, "2.1.2.2_主题间关联关系图.md",
           _front("2.1.2", "2.1.2.2", []) + "\n".join(body), manifest, "2.1.2.2",
           "主题间关联关系图", [os.path.join(CDATA, "_t1_clause_graph.json")], dry)


def d_212_3(names, finals, dry, manifest, outdir) -> None:
    """2.1.2.3 各主题监管演进阐述（附关键时间线）"""
    parts = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        years = {}
        for r in recs:
            y = _norm_year(r.get("real_year") or r.get("year_reported"))
            if y:
                years.setdefault(y, []).append(r)
        tl = [(y, len(years[y]), "；".join((x.get("title") or "")[:22] for x in years[y][:2]))
              for y in sorted(years)]
        first = min(years) if years else None
        last = max(years) if years else None
        cur = sum(1 for r in recs if (r.get("eff_status") or "") in ("", "valid", "amended"))
        parts += [
            f"### {tid} {names.get(tid, tid)}", "",
            f"- 文件数：**{len(recs)}**；时间跨度：{first or '—'}–{last or '—'}；现行/修订：**{cur}**；",
            "- 关键时间线（年份 → 发文数 → 代表文件）：", "",
            _table(["年份", "发文数", "代表文件"], tl),
            "",
        ]
    body = [
        "# 2.1.2.3 各主题监管演进阐述",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_t*_final.json`）",
        "",
        *parts,
    ]
    _write(outdir, "2.1.2.3_各主题监管演进阐述.md",
           _front("2.1.2", "2.1.2.3", []) + "\n".join(body), manifest, "2.1.2.3",
           "各主题监管演进阐述", [os.path.join(CDATA, "_t1_final.json")], dry)


# --------------------------------------------------------------------------- #
# 2.1.2 追加（F-L01 纳管，2026-09-14）：关系类报告纳入交付库
# --------------------------------------------------------------------------- #
# 背景：`docs/reports/监管与制度依据废止关系图谱.md` 与 `RFN补登候选清单.md` 由**各自工具**产出
# （`tools/extract_relations.py --report` / `tools/rfn_backlog.py`），此前**游离于交付库之外**
# ——不随 `analysis gen` 刷新、不在 `_manifest.json` 口径内、异机交付不完整。
#
# 纳管方式（**追加式，零重构**）：
#   1. **复用单源渲染**：调用两工具的纯渲染函数（`render_report` / `render_md`），
#      不复制其渲染逻辑（禁止分叉）；两工具的 CLI 路径亦已改走同一函数（行为等价，逐字节验证）；
#   2. **零重抽取**：从**已落盘产物**重建（`relations_index.jsonl` + `relations_stat.json`），
#      不做关系抽取（避免把 ~32s 抽取成本引入交付库刷新）；
#   3. **沿用既有文件名**：不改两工具的 `REPORT_PATH` / `OUT_MD`，避免"同一报告两个名字"、
#      或另一写入方写回旧名导致分叉；
#   4. **数据源缺失即跳过并告警**：不写占位、不登记——避免用占位内容覆盖既有好报告。
REL_DIR = os.path.join(CDATA, "relations")
REL_INDEX = os.path.join(REL_DIR, "relations_index.jsonl")
REL_STAT = os.path.join(REL_DIR, "relations_stat.json")


def _load_relations():
    """读关系产物 → (rows, stat)；任一缺失/不可解析返回 (None, None)。"""
    if not (os.path.exists(REL_INDEX) and os.path.exists(REL_STAT)):
        return None, None
    rows = []
    try:
        with open(REL_INDEX, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, ValueError):
        return None, None
    stat = _load_json(REL_STAT, default=None)
    if not isinstance(stat, dict) or not stat.get("files"):
        return None, None
    return rows, stat


def _relation_tool(module_name: str):
    """导入 tools/ 下的单源渲染器模块（与命令行共用唯一实现）。"""
    tdir = os.path.join(ROOT, "tools")
    if tdir not in sys.path:
        sys.path.insert(0, tdir)
    return __import__(module_name)


def d_212_4(rows, stat, dry, manifest, outdir) -> None:
    """2.1.2.4 监管与制度依据·废止关系图谱（单源渲染 = extract_relations.render_report）"""
    if rows is None:
        print("  [跳过] 2.1.2.4 关系图谱：关系事实源缺失（先跑 `cli.py relations gen`），不写占位不登记")
        return
    er = _relation_tool("extract_relations")
    _write(outdir, "监管与制度依据废止关系图谱.md", er.render_report(rows, stat), manifest,
           "2.1.2.4", "监管与制度依据废止关系图谱", [REL_INDEX, REL_STAT], dry)


def d_212_5(dry, manifest, outdir) -> None:
    """2.1.2.5 RFN 补登候选清单（单源渲染 = rfn_backlog.render_md，时间戳与交付库同源）"""
    if not os.path.exists(REL_INDEX):
        print("  [跳过] 2.1.2.5 补登候选清单：关系事实源缺失（先跑 `cli.py relations gen`）")
        return
    rb = _relation_tool("rfn_backlog")
    try:
        bl = rb.build_backlog()
    except (OSError, ValueError, KeyError) as e:  # 数据不可解析 → 跳过，不写占位
        print(f"  [跳过] 2.1.2.5 补登候选清单：{e!r}")
        return
    _write(outdir, "RFN补登候选清单.md", rb.render_md(bl, generated_at=NOW), manifest,
           "2.1.2.5", "RFN 补登候选清单", [REL_INDEX], dry)


# --------------------------------------------------------------------------- #
# 2.1.3 全景分析
# --------------------------------------------------------------------------- #
def _global_stats(names, finals, details, graphs) -> dict:
    n_files = sum(len(v) for v in finals.values())
    years = [_norm_year(r.get("real_year") or r.get("year_reported"))
             for v in finals.values() for r in v]
    years = [y for y in years if y]
    n_rows = sum(len(v["rows"]) for v in details.values())
    n_tl = sum(1 for v in details.values() for r in v["rows"] if (r.get("时效状态") or "").strip())
    n_inner = sum(1 for tid in graphs for e in (graphs[tid].get("edges") or [])
                  if _n(e.get("dst_theme")) == _n(tid))
    n_cross = sum(1 for tid in graphs for e in (graphs[tid].get("edges") or [])
                  if _n(e.get("dst_theme")) and _n(e.get("dst_theme")) != _n(tid))
    return {"themes": len(finals), "files": n_files, "y0": min(years) if years else 0,
            "y1": max(years) if years else 0, "detail_rows": n_rows, "tl_cov": n_tl,
            "inner_edges": n_inner, "cross_edges": n_cross}


def d_213_1(names, finals, details, graphs, dry, manifest, outdir) -> None:
    """2.1.3.1 执行摘要（核心发现与建议）"""
    s = _global_stats(names, finals, details, graphs)
    top = sorted(((tid, len(finals[tid])) for tid in finals), key=lambda x: -x[1])[:5]
    body = [
        "# 2.1.3.1 执行摘要",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（全交付库汇总）",
        "",
        "## 体系规模", "",
        _table(["指标", "数值"], [
            ("主题数", s["themes"]), ("监管文件总数", s["files"]),
            ("时间跨度", f"{s['y0']}–{s['y1']}"), ("引用链条表行数（明细）", s["detail_rows"]),
            ("含法宝时效核验行", f"{s['tl_cov']}（{round(s['tl_cov'] / s['detail_rows'] * 100, 1) if s['detail_rows'] else 0}%）"),
            ("主题内关系边", s["inner_edges"]), ("跨主题关系边", s["cross_edges"]),
        ]),
        "",
        "## 核心发现", "",
        *[f"- **{tid} {names.get(tid, tid)}**：{n} 份文件（Top {i + 1}）" for i, (tid, n) in enumerate(top)],
        f"- 跨主题引用 {s['cross_edges']} 条：主题间存在实质交叉监管（详见 2.1.3.2）；",
        f"- 时效核验覆盖 {round(s['tl_cov'] / s['detail_rows'] * 100, 1) if s['detail_rows'] else 0}%："
        f"未覆盖行需补核（详见 2.1.3.3）。",
        "",
        "## 建议（详见 2.1.3.7）", "",
        "- 优先补齐空白子主题与无条款引用文件（2.1.1.5 / 2.1.3.3）；",
        "- 关注跨主题交叉领域（2.1.3.2）；核对上位法覆盖缺口（2.1.3.4）。",
    ]
    _write(outdir, "2.1.3.1_执行摘要.md",
           _front("2.1.3", "2.1.3.1", []) + "\n".join(body), manifest, "2.1.3.1",
           "执行摘要", [os.path.join(CDATA, "_t1_final.json")], dry)


def d_213_2(names, graphs, dry, manifest, outdir) -> None:
    """2.1.3.2 主题交叉影响全景图（含交叉领域清单与评估）"""
    pairs = {}
    for tid in THEME_ORDER:
        g = graphs.get(tid) or {}
        for e in g.get("edges") or []:
            dt = _n(e.get("dst_theme"))
            if dt and dt != _n(tid):
                k = tuple(sorted((_n(tid), dt)))
                pairs[k] = pairs.get(k, 0) + (e.get("count") or 1)
    rank = sorted(pairs.items(), key=lambda x: -x[1])
    rows = [(f"{a} ↔ {b}", names.get(a, a), names.get(b, b), n,
             "强" if n >= 10 else ("中" if n >= 3 else "弱")) for (a, b), n in rank]
    body = [
        "# 2.1.3.2 主题交叉影响全景图",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：clause_graph 双向合并跨主题边）",
        "",
        "## 交叉领域清单（强度分级：强 ≥10 / 中 ≥3 / 弱 <3）", "",
        _table(["交叉对", "主题A", "主题B", "引用合计", "强度"], rows),
        "",
        f"合计：**{len(rows)} 对交叉领域**；最高强度：{rows[0][0] + '（' + str(rows[0][3]) + '）' if rows else '—'}",
    ]
    _write(outdir, "2.1.3.2_主题交叉影响全景图.md",
           _front("2.1.3", "2.1.3.2", []) + "\n".join(body), manifest, "2.1.3.2",
           "主题交叉影响全景图", [os.path.join(CDATA, "_t1_clause_graph.json")], dry)


def d_213_3(names, finals, details, graphs, dry, manifest, outdir) -> None:
    """2.1.3.3 监管空白与薄弱环节识别（含空白清单与建议关注度）"""
    gaps = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        clusters = {}
        for r in recs:
            c = (r.get("cluster") or "（未聚类）").strip() or "（未聚类）"
            clusters[c] = clusters.get(c, 0) + 1
        # 空白：子主题无现行（全废止）
        for c in sorted(clusters):
            rs = [r for r in recs if ((r.get("cluster") or "（未聚类）").strip() or "（未聚类）") == c]
            cur = sum(1 for r in rs if (r.get("eff_status") or "") in ("", "valid", "amended"))
            if cur == 0:
                gaps.append((tid, names.get(tid, tid), "子主题无现行文件", f"{c}（{len(rs)} 份全非现行）", "高"))
            elif len(rs) <= 2:
                gaps.append((tid, names.get(tid, tid), "子主题覆盖薄弱", f"{c}（{len(rs)} 份）", "中"))
    # 薄弱：无条款引用占比高的主题
    for tid in sorted(details):
        rs = details[tid]["rows"]
        if not rs:
            continue
        miss = sum(1 for r in rs if not (r.get("条款引用") or "").strip())
        if miss:
            gaps.append((tid, names.get(tid, tid), "无条款引用文件",
                         f"{miss}/{len(rs)}（{round(miss / len(rs) * 100)}%）",
                         "高" if miss / len(rs) > 0.5 else "中"))
    body = [
        "# 2.1.3.3 监管空白与薄弱环节识别",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：final × 明细；关注度=空白严重度分级）",
        "",
        "## 空白清单", "",
        _table(["主题", "主题名", "空白类型", "空白实体", "建议关注度"], gaps),
        "",
        f"合计：**{len(gaps)} 项**（高关注 {sum(1 for g in gaps if g[4] == '高')} / 中 {sum(1 for g in gaps if g[4] == '中')}）",
    ]
    _write(outdir, "2.1.3.3_监管空白与薄弱环节识别.md",
           _front("2.1.3", "2.1.3.3", []) + "\n".join(body), manifest, "2.1.3.3",
           "监管空白与薄弱环节识别",
           [os.path.join(CDATA, "_t1_final.json")], dry)


def d_213_4(names, upper, details, dry, manifest, outdir) -> None:
    """2.1.3.4 上位法覆盖对照表（《保险法》条款与各主题的对应关系）"""
    rows = []
    for k, v in sorted((upper or {}).items()):
        if not isinstance(v, dict):
            continue
        cited = v.get("cited_by_themes") or v.get("themes") or ""
        if isinstance(cited, list):
            cited = "、".join(str(x) for x in cited)
        body_len = len(v.get("body_text") or "")
        rows.append((k, (v.get("title") or "")[:40], v.get("docno") or "—",
                     cited or "（未标注引用主题）", f"{body_len} 字" if body_len else "正文待补"))
    body = [
        "# 2.1.3.4 上位法覆盖对照表",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：`_upper_laws.json`）",
        "",
        "## 上位法 → 主题对应", "",
        _table(["键", "上位法", "发文字号", "引用主题（cited_by_themes）", "正文规模"], rows),
        "",
        f"合计：**{len(rows)} 部上位法**；说明：引用主题为 build_upper_laws 抽取结果，"
        "空值表示当前规则未检出显式引用（治理项，见 2.1.3.3）。",
    ]
    _write(outdir, "2.1.3.4_上位法覆盖对照表.md",
           _front("2.1.3", "2.1.3.4", []) + "\n".join(body), manifest, "2.1.3.4",
           "上位法覆盖对照表", [os.path.join(CDATA, "_upper_laws.json")], dry)


def d_213_5(names, finals, dry, manifest, outdir) -> None:
    """2.1.3.5 行业外监管接口矩阵（通用监管与专属监管的衔接）"""
    # 通用=gov/mof/pbc/supp（政府/财政/央行/补充）；专属=nfra（金融监管总局）
    univ = ("gov", "mof", "pbc", "supp")
    rows = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        src = {}
        for r in recs:
            s = (r.get("file_src") or "?").strip() or "?"
            src[s] = src.get(s, 0) + 1
        n_univ = sum(v for k, v in src.items() if k in univ)
        n_spec = sum(v for k, v in src.items() if k not in univ)
        rows.append((tid, names.get(tid, tid), n_spec, n_univ,
                     "、".join(f"{k}:{v}" for k, v in sorted(src.items())),
                     round(n_univ / (n_univ + n_spec) * 100, 1) if (n_univ + n_spec) else 0))
    body = [
        "# 2.1.3.5 行业外监管接口矩阵",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：final.file_src；"
        "专属=nfra（金融监管总局）；通用=gov/mof/pbc/supp（政府/财政/央行/补充））",
        "",
        "## 主题 × 监管域 接口矩阵", "",
        _table(["主题", "主题名", "专属监管(nfra)", "通用监管(gov/mof/pbc/supp)", "来源明细", "通用占比%"], rows),
        "",
        "**衔接解读**：通用占比高的主题属『多部门接口区』（如市场主体/财会/数据领域），"
        "其制度转化需同时对齐行业规章与上位通用法；专属占比高者以行业规则为主。",
    ]
    _write(outdir, "2.1.3.5_行业外监管接口矩阵.md",
           _front("2.1.3", "2.1.3.5", []) + "\n".join(body), manifest, "2.1.3.5",
           "行业外监管接口矩阵", [os.path.join(CDATA, "_t1_final.json")], dry)


def d_213_6(names, finals, dry, manifest, outdir) -> None:
    """2.1.3.6 监管趋势预判与前瞻性建议"""
    import datetime as _d2
    cur_y = _d2.datetime.now().year
    rows = []
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        years = [_norm_year(r.get("real_year") or r.get("year_reported")) for r in recs]
        years = [y for y in years if y]
        n3 = sum(1 for y in years if y >= cur_y - 3)
        n5 = sum(1 for y in years if cur_y - 5 <= y < cur_y - 3)
        prev5 = sum(1 for y in years if cur_y - 10 <= y < cur_y - 5)
        trend = "↑ 加强" if n3 + n5 > prev5 else ("↓ 放缓" if n3 + n5 < prev5 else "→ 平稳")
        rows.append((tid, names.get(tid, tid), n3, n5, prev5, trend))
    body = [
        "# 2.1.3.6 监管趋势预判与前瞻性建议",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（数据源：final.real_year；窗口=近3年/4-5年/6-10年）",
        "",
        "## 主题发文密度趋势（近 10 年）", "",
        _table(["主题", "主题名", f"近3年({cur_y - 2}-)", "4-5年前", "6-10年前", "趋势"], rows),
        "",
        "**前瞻性建议**（规则化输出，供人工研判）：",
        *[f"- {r[0]} {names.get(r[0], r[0])}：趋势{r[5]}——"
          + ("保持关注，预判后续规章密度上升，提前储备制度转化。" if "加强" in str(r[5])
             else ("关注存量规则整合，避免制度冗余。" if "平稳" in str(r[5])
                   else "领域趋稳，重点转为存量执行与实效评估。"))
          for r in rows],
    ]
    _write(outdir, "2.1.3.6_监管趋势预判与前瞻性建议.md",
           _front("2.1.3", "2.1.3.6", []) + "\n".join(body), manifest, "2.1.3.6",
           "监管趋势预判与前瞻性建议", [os.path.join(CDATA, "_t1_final.json")], dry)


def d_213_7(names, finals, details, graphs, dry, manifest, outdir) -> None:
    """2.1.3.7 后续行动计划建议（基于空白识别和趋势预判，建议优先强化的领域）"""
    acts = []
    # 来源1：无现行文件/薄弱子主题（空白）
    for tid in THEME_ORDER:
        recs = finals.get(tid) or []
        if not recs:
            continue
        clusters = {}
        for r in recs:
            c = (r.get("cluster") or "（未聚类）").strip() or "（未聚类）"
            clusters.setdefault(c, []).append(r)
        for c, rs in sorted(clusters.items()):
            cur = sum(1 for r in rs if (r.get("eff_status") or "") in ("", "valid", "amended"))
            if cur == 0:
                acts.append(("P1", tid, names.get(tid, tid), f"子主题『{c}』无现行文件", "新建/修订相关制度或补充监管跟踪"))
            elif len(rs) <= 2:
                acts.append(("P2", tid, names.get(tid, tid), f"子主题『{c}』覆盖薄弱（{len(rs)} 份）", "扩充文件覆盖并评估制度对应"))
    # 来源2：无条款引用文件
    for tid in sorted(details):
        rs = details[tid]["rows"]
        miss = [r for r in rs if not (r.get("条款引用") or "").strip()]
        if miss:
            acts.append(("P2", tid, names.get(tid, tid),
                         f"{len(miss)} 份文件无条款引用", "补条款级引用或标注无需引用（人工复核）"))
    # 来源3：无关联主题（关系孤岛）
    for tid in THEME_ORDER:
        g = graphs.get(tid) or {}
        if (finals.get(tid)) and not (g.get("edges") or []):
            acts.append(("P1", tid, names.get(tid, tid), "主题无任何引用关系边", "核查 clause_graph 抽取链"))
    body = [
        "# 2.1.3.7 后续行动计划建议",
        "",
        f"> 生成：gen_analysis_deliveries · {NOW}（来源：2.1.3.3 空白清单 + 2.1.3.6 趋势预判）",
        "",
        "## 行动计划表", "",
        _table(["优先级", "主题", "主题名", "触发问题", "建议行动"], sorted(acts)),
        "",
        f"合计：**{len(acts)} 项行动建议**（P1 {sum(1 for a in acts if a[0] == 'P1')} / P2 {sum(1 for a in acts if a[0] == 'P2')}）",
        "",
        "> 说明：本表由空白/趋势规则自动生成，优先级 P1=空白或断链、P2=薄弱或待复核；"
        "执行前需人工复核并登记到制度交付（drafter 3.2 六交付）。",
    ]
    _write(outdir, "2.1.3.7_后续行动计划建议.md",
           _front("2.1.3", "2.1.3.7", []) + "\n".join(body), manifest, "2.1.3.7",
           "后续行动计划建议", [os.path.join(CDATA, "_t1_final.json")], dry)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F-L01 规划 2.1 五级分析交付库生成器")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "reports"), help="输出目录")
    ap.add_argument("--dry", action="store_true", help="仅统计不写盘")
    args = ap.parse_args(argv)

    names = _load_theme_names()
    finals = _load_finals()
    graphs = _load_graphs()
    details = _load_details()
    upper = _load_upper_laws()
    print(f"[deliveries] 主题 {len(finals)} | 图 {len(graphs)} | 明细 {len(details)} | 上位法 {len(upper)}")

    manifest: list = []
    # 2.1.1（5）→ 2.1.2（5，含 2026-09-14 追加的关系类 2 项）→ 2.1.3（7）
    d_211_1(names, finals, details, args.dry, manifest, args.out)
    d_211_2(names, finals, args.dry, manifest, args.out)
    d_211_3(names, graphs, args.dry, manifest, args.out)
    d_211_4(names, details, args.dry, manifest, args.out)
    d_211_5(names, finals, details, graphs, args.dry, manifest, args.out)
    d_212_1(names, finals, args.dry, manifest, args.out)
    d_212_2(names, graphs, args.dry, manifest, args.out)
    d_212_3(names, finals, args.dry, manifest, args.out)
    _rel_rows, _rel_stat = _load_relations()          # F-L01 纳管：2.1.2.4 / 2.1.2.5（单源渲染）
    d_212_4(_rel_rows, _rel_stat, args.dry, manifest, args.out)
    d_212_5(args.dry, manifest, args.out)
    d_213_1(names, finals, details, graphs, args.dry, manifest, args.out)
    d_213_2(names, graphs, args.dry, manifest, args.out)
    d_213_3(names, finals, details, graphs, args.dry, manifest, args.out)
    d_213_4(names, upper, details, args.dry, manifest, args.out)
    d_213_5(names, finals, args.dry, manifest, args.out)
    d_213_6(names, finals, args.dry, manifest, args.out)
    d_213_7(names, finals, details, graphs, args.dry, manifest, args.out)

    if not args.dry:
        mpath = os.path.join(args.out, "_manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump({"generated_by": "gen_analysis_deliveries", "generated_at": NOW,
                       "count": len(manifest), "items": manifest}, fh,
                      ensure_ascii=False, indent=2)
    print(f"[deliveries] 交付 {len(manifest)} 项 → {args.out}")
    for m in manifest:
        print(f"  {m['item']:9s} {m['lines']:5d} 行  {m['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

