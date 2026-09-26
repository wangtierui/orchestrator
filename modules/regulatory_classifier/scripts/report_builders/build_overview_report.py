# -*- coding: utf-8 -*-
"""
scripts/report_builders/build_overview_report.py — 全景 + 主题分类报告生成器（R9 补全）

补齐 docs/reports 两类总览报告（对齐旧仓 docs 产物名，按现行 data 口径可复现）：
  人身保险公司-全景分析报告.md    —— 全库：总量/主题分布/时效分布/来源分布/年份分布/主题×时效
  人身保险公司-主题分类报告.md    —— 主题归属语义：T0–T10 主题→文件数/时效/代表性清单
数据源：归属表 8 列 + 主题归属表 3 列 + _t{n}_final.json（单一事实源 THEME_MAP 驱动）。
用法：python build_overview_report.py
"""
from __future__ import annotations

import collections
import csv
import json
import os
import re
import sys
from datetime import datetime

_THIS = os.path.dirname(os.path.abspath(__file__))          # .../report_builders
_SCRIPTS = os.path.dirname(_THIS)                             # .../scripts
_MOD_CLASS = os.path.dirname(_SCRIPTS)                        # modules/regulatory_classifier
for _p in (_MOD_CLASS, os.path.dirname(os.path.dirname(_MOD_CLASS))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from rfn import THEME_MAP  # noqa: E402

_DATA = os.path.join(_MOD_CLASS, "data")
_DOCS = os.path.join(_MOD_CLASS, "docs", "reports")

_CODE_RE = re.compile(r"^(T\d+)")


def _load_csv(fn: str) -> list[dict]:
    with open(os.path.join(_DATA, fn), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _theme_code(tname: str) -> str:
    m = _CODE_RE.match(tname or "")
    if m:
        c = m.group(1)
        return c if c in THEME_MAP else f"T{int(c[1:])}"
    return ""


def build() -> dict:
    attr = _load_csv("人身保险公司-文件归属表.csv")
    themerows = _load_csv("人身保险公司-主题归属表.csv")
    code_by_rfn = {}
    for tr in themerows:
        c = _theme_code(tr.get("主题", ""))
        if c:
            code_by_rfn[tr["监管文件编号"]] = c
    # 各主题 final 数（data 底座）
    final_cnt = {}
    for c in THEME_MAP:
        p = os.path.join(_DATA, f"_t{int(c[1:])}_final.json")
        if os.path.exists(p):
            try:
                final_cnt[c] = len(json.load(open(p, encoding="utf-8")))
            except Exception:
                final_cnt[c] = 0

    rows = []
    for r in attr:
        rfn = r["监管文件编号"]
        tc = code_by_rfn.get(rfn, "T0")  # 主题归属表无→T0锚点桶
        rows.append((rfn, r.get("文件名称", ""), r.get("发文字号", ""),
                     r.get("发布日期", ""), r.get("文件来源", ""),
                     r.get("时效状态", ""), tc))
    total = len(rows)
    eff_all = collections.Counter(r[5] for r in rows)
    src_all = collections.Counter(r[4] for r in rows)
    theme_cnt = collections.Counter(r[6] for r in rows)
    year_cnt = collections.Counter((r[3] or "")[:4] for r in rows if (r[3] or "").strip())
    theme_eff = collections.defaultdict(collections.Counter)
    for r in rows:
        theme_eff[r[6]][r[5]] += 1
    eff_ord = ["valid", "amended", "repealed", "partially_repealed", "expired", "pending", "uncertain"]

    def _eff_str(cnt: collections.Counter) -> str:
        return "；".join(f"{k}={cnt.get(k, 0)}" for k in eff_ord if cnt.get(k))

    # ---- 全景 ----
    L = ["# 人身保险公司-全景分析报告", "",
         "> 数据基准：`人身保险公司-文件归属表.csv`（%d 条）+ `人身保险公司-主题归属表.csv` 合并；"
         "生成 date=%s（现行口径，可复现于 report_builders/build_overview_report.py）"
         % (total, datetime.now().strftime("%Y-%m-%d")), "",
         "## 一、总量与主题分布", "",
         "| 主题码 | 主题名 | 归属表条数 | 底座 final 数 |", "|---|---|---:|---:|"]
    for c in sorted(THEME_MAP, key=lambda x: (len(x), x)):
        L.append(f"| {c} | {THEME_MAP[c]} | {theme_cnt.get(c, 0)} | {final_cnt.get(c, 0)} |")
    L += ["", "## 二、全局时效分布", "",
          "| 时效状态 | 文件数 |", "|---|---:|"]
    for k in eff_ord:
        if eff_all.get(k):
            L.append(f"| {k} | {eff_all[k]} |")
    L += ["", "## 三、来源分布", "", "| 来源 | 文件数 |", "|---|---:|"]
    for k, v in src_all.most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "## 四、发布年份分布（近 15 年）", "", "| 年份 | 文件数 |", "|---|---:|"]
    for k in sorted([y for y in year_cnt if y.isdigit() and len(y) == 4])[-15:]:
        L.append(f"| {k} | {year_cnt[k]} |")
    L += ["", "## 五、主题 × 时效矩阵", "",
          "| 主题码 | 主题名 | 时效分布 |", "|---|---|---|"]
    for c in sorted(THEME_MAP, key=lambda x: (len(x), x)):
        L.append(f"| {c} | {THEME_MAP[c]} | {_eff_str(theme_eff[c])} |")
    md_all = "\n".join(L)
    _write("人身保险公司-全景分析报告.md", md_all)

    # ---- 主题分类 ----
    L2 = ["# 人身保险公司-主题分类报告", "",
          "> 主题分类语义：每条 RFN 归入唯一主题（`人身保险公司-主题归属表.csv` 判定依据列留痕），"
          "分类命名空间 = rfn.THEME_MAP（T0 上位法锚点 + T1–T10）。生成可复现。", ""]
    for c in sorted(THEME_MAP, key=lambda x: (len(x), x)):
        subs = [r for r in rows if r[6] == c]
        L2 += [f"## {c} {THEME_MAP[c]}", "",
               f"- 文件数：{len(subs)}（时效：{_eff_str(collections.Counter(r[5] for r in subs))}）", ""]
        if not subs:
            L2.append("_（无文件归入）_\n")
            continue
        L2 += ["| RFN | 文件名称 | 发文字号 | 时效 |", "|---|---|---|---|"]
        for rfn, title, docno, _, _, eff, _ in sorted(subs, key=lambda x: x[0])[:80]:
            L2.append(f"| {rfn} | {title} | {docno} | {eff} |")
        L2.append("")
    md_theme = "\n".join(L2)
    _write("人身保险公司-主题分类报告.md", md_theme)
    return {"attr_total": total, "themes": sorted(THEME_MAP, key=lambda x: (len(x), x)),
            "files": ["人身保险公司-全景分析报告.md", "人身保险公司-主题分类报告.md"],
            "dir": _DOCS}


def _write(fname: str, md: str) -> None:
    os.makedirs(_DOCS, exist_ok=True)
    p = os.path.join(_DOCS, fname)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(md)
    os.replace(tmp, p)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  分类/检索容错
        pass
    print(json.dumps(build(), ensure_ascii=False))
