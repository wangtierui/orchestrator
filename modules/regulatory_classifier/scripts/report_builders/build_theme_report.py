# -*- coding: utf-8 -*-
"""
scripts/report_builders/build_theme_report.py — 主题监管文件视图报告生成器（R9 落地）

按 rfn.THEME_MAP 遍历 T0–T10，读新仓 data 底座（_t{n}_final.json）+ 归属表 + 主题归属表，
输出每主题 Markdown 摘要报告至 docs/reports/：

  产出去向（R9：输出路径全 paths 派生，落 modules/regulatory_classifier/docs/reports/）：
    docs/reports/T{n}_主题报告.md     —— 每主题：统计 + 文件清单（RFN/标题/文号/时效/来源）

背景：2026-09-02-T1纵向深化分析 曾产一次性 T1–T10 报告（旧仓 docs）。本生成器是
「按主题遍历 data 底座」的现行可复现框架：主题集 = THEME_MAP（R16，新增主题只需改 THEME_MAP），
数据源 = 现行 40 底座/11 明细对应 final.json，不依赖一次性分析脚本。

用法：
  python build_theme_report.py            # 全部主题
  python build_theme_report.py --theme T1 # 单主题
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys

# ---- 同仓引导（R4：无盘符） ----
_THIS = os.path.dirname(os.path.abspath(__file__))     # .../scripts/report_builders
_SCRIPTS = os.path.dirname(_THIS)                       # .../scripts
_MOD_CLASS = os.path.dirname(_SCRIPTS)                  # modules/regulatory_classifier
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
for _p in (_MOD_CLASS, _ORCH_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rfn import THEME_MAP  # noqa: E402  主题单一事实源（R16）

_DATA = os.path.join(_MOD_CLASS, "data")
_DOCS_REPORTS = os.path.join(_MOD_CLASS, "docs", "reports")


def _load_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ② 条款共享维度：五源 clause_index（clean 固定节点产物）→ {归一化文号: 条款数}
# 供主题报告/全景报告统计"条款化文件数/条款总数"（clause 产物缺失则静默为无，不阻断报告）。
_clause_map: dict[str, int] | None = None


def _load_clause_map() -> dict[str, int]:
    from std_lib.common_lib.norm import norm_docno  # noqa: PLC0415
    # 阶段 3（2026-09-18）：条文产物经 interfaces 唯一入口（原跨模块引导已移除）
    from interfaces import clause_index_api as _ci  # noqa: PLC0415
    m: dict[str, int] = {}
    for src in ("gov", "mof", "nfra", "pbc", "supp"):
        try:
            for cl in _ci.iter_file_clauses(src):
                if cl.get("article_count", 0) <= 0:
                    continue
                dno = cl.get("document_number", "") or ""
                nd = norm_docno(dno)
                if nd and len(nd) >= 5:
                    m.setdefault(nd, 0)
                    m[nd] += cl["article_count"]
                sig = _docno_sig(dno)
                if sig:
                    m.setdefault(sig, 0)
                    m[sig] += cl["article_count"]
        except Exception:
            continue
    return m


# 文号尾签名「〔年〕序」：容忍发文机关前缀差异（归属表简写 vs clean 全称）
_DOCNO_SIG_RE = re.compile(r"[〔\[](\d{4})[〕\]][^0-9]{0,6}(\d{1,4})\s*号?")


def _docno_sig(docno: str) -> str:
    mm = _DOCNO_SIG_RE.search(docno or "")
    return f"{mm.group(1)}-{mm.group(2)}" if mm else ""


def _theme_report(theme: str) -> tuple[str, str]:
    """生成单主题报告 → (文件名, markdown)。"""
    tname = THEME_MAP.get(theme, theme)
    final_p = os.path.join(_DATA, f"_t{theme[1:]}_final.json")
    final = []
    if os.path.exists(final_p):
        final = json.load(open(final_p, encoding="utf-8"))
    attr = {r["监管文件编号"]: r for r in _load_rows(os.path.join(_DATA, "人身保险公司-文件归属表.csv"))}
    theme_rows = _load_rows(os.path.join(_DATA, "人身保险公司-主题归属表.csv"))
    theme_map = {}
    full = THEME_MAP.get(theme, theme)
    for tr in theme_rows:
        tval = tr.get("主题", "")
        # 主题归属表值为完整名（T1销售行为与消费者保护）；精确匹配防 T1 误吞 T10
        if tval == full or re.match(rf"^{theme}(?!\d)", tval):
            theme_map[tr.get("监管文件编号", "")] = tval

    lines = [f"# {tname} 主题监管文件视图", ""]
    lines.append(
        f"> 生成: data/_t{theme[1:]}_final.json（{len(final)} 条）| "
        f"主题归属表关联（{len(theme_map)} 条）| 归属表时效权威"
    )
    lines.append("")
    # 时效统计
    from collections import Counter  # noqa: PLC0415

    from std_lib.common_lib.norm import norm_docno  # noqa: PLC0415
    st_cnt: Counter = Counter()
    src_cnt: Counter = Counter()
    rows = []
    clause_files = clause_arts = 0
    for rec in final:
        rfn = rec.get("监管文件编号", "")
        ar = attr.get(rfn, {})
        eff = (rec.get("eff_status") or ar.get("时效状态") or "").strip() or "未标注"
        src = (rec.get("file_src") or ar.get("文件来源") or "").strip() or "—"
        st_cnt[eff] += 1
        src_cnt[src] += 1
        rows.append((rfn, rec.get("title") or ar.get("文件名称") or "",
                     rec.get("doc_no") or ar.get("发文字号") or "", eff, src))
        if _clause_map is not None:   # 条款共享维度（②）：doc_no 归一经 clause_index
            dno = rec.get("doc_no") or ""
            n = _clause_map.get(norm_docno(dno), 0) or _clause_map.get(_docno_sig(dno), 0)
            if n > 0:
                clause_files += 1
                clause_arts += n
    lines.append("## 统计")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 文件数 | {len(rows)} |")
    lines.append(f"| 时效分布 | {'；'.join(f'{k}={v}' for k, v in st_cnt.most_common())} |")
    lines.append(f"| 来源分布 | {'；'.join(f'{k}={v}' for k, v in src_cnt.most_common())} |")
    if _clause_map is not None:
        lines.append(f"| 条款化文件/条款总数（clause_index ②） | {clause_files} / {clause_arts} |")
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| RFN | 文件名称 | 发文字号 | 时效状态 | 来源 |")
    lines.append("|---|---|---|---|---|")
    for rfn, title, docno, eff, src in sorted(rows, key=lambda x: (x[3], x[0])):
        lines.append(f"| {rfn} | {title} | {docno} | {eff} | {src} |")
    return f"T{theme[1:]}主题报告.md", "\n".join(lines)


def build_all(themes: list[str] | None = None) -> dict:
    global _clause_map
    os.makedirs(_DOCS_REPORTS, exist_ok=True)
    try:
        _clause_map = _load_clause_map()
        print(f"[clause] 条款维度就绪: {len(_clause_map)} 个文号含条款")
    except Exception:  # noqa: BLE001  clause 产物缺失不阻断报告
        _clause_map = {}
    themes = themes or sorted(THEME_MAP)
    out = {}
    for th in themes:
        fname, md = _theme_report(th)
        p = os.path.join(_DOCS_REPORTS, fname)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(md)
        os.replace(tmp, p)
        out[th] = os.path.join(_DOCS_REPORTS, fname)
    return {"themes": themes, "files": out, "dir": _DOCS_REPORTS}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="主题监管文件视图报告生成")
    ap.add_argument("--theme", default=None, help="单主题（默认全部 T0-T10）")
    args = ap.parse_args()
    themes = [args.theme] if args.theme else None
    res = build_all(themes)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
