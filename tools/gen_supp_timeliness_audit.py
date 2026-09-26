# -*- coding: utf-8 -*-
"""
tools/gen_supp_timeliness_audit.py — supp 时效标注口径审计（批次3，2026-09-08）

问题：supp cleaned 存量 timeliness_status 多由「数据源标注」给出（非北大法宝核验），
属展示口径而非 SSOT 权威核验。map_supp 已修复（无显式值不再默认 valid，保空待核）。
本工具只读审计 supp 最新快照的状态/来源标注分布，输出 reports/supp时效口径审计_<date>.md，
明确消费口径（supp valid=数据源标注，权威核验走 timeliness verify --source supp），不改数据。

用法：python tools/gen_supp_timeliness_audit.py
"""

from __future__ import annotations

import csv
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import paths  # noqa: E402
from config.exitcodes import ExitCode  # noqa: E402

csv.field_size_limit(sys.maxsize)  # body_text 超默认字段上限

_CLEAN_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned")
_REPORTS = paths.REPORTS_DIR


def latest_supp_csv() -> str:
    cands = sorted(
        f for f in os.listdir(_CLEAN_DIR) if f.startswith("supp_cleaned_") and f.endswith(".csv")
    )
    return os.path.join(_CLEAN_DIR, cands[-1]) if cands else ""


def main() -> int:
    p = latest_supp_csv()
    if not os.path.exists(p):
        print("supp cleaned 缺失")
        return ExitCode.FAIL
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig", newline="")))
    from collections import Counter

    ts = Counter((r.get("timeliness_status") or "").strip() or "(空)" for r in rows)
    vs_kind = Counter()
    vs_detail = Counter()
    for r in rows:
        vs = (r.get("verification_source") or "").strip()
        if not vs:
            vs_kind["无来源标注"] += 1
        elif "北大法宝" in vs:
            vs_kind["北大法宝核验"] += 1
            vs_detail[vs[:40]] += 1
        elif "官网" in vs or "数据源" in vs or "原文" in vs:
            vs_kind["数据源/官网标注"] += 1
            vs_detail[vs[:40]] += 1
        else:
            vs_kind["其他"] += 1
            vs_detail[vs[:40]] += 1
    today = datetime.date.today().isoformat()
    L = [
        f"# supp 时效标注口径审计（{today}）",
        "",
        f"> 快照：`{os.path.basename(p)}`（{len(rows)} 行）| 工具：tools/gen_supp_timeliness_audit.py（只读）",
        "",
        "## 1. 背景与修复",
        "",
        "- 评估 P2：supp 清洗 `map_supp` 曾对无显式时效值默认 `valid` → 已修复（2026-09-08）：无显式值即保空（待核验位，不臆造现行有效）。",
        "- 本仓 supp 存量各行均带 `verification_source`（数据源标注），属合法标注而非默认污染；口径：**supp 状态=数据源/官网标注的展示口径**，权威核验统一走 `verification_state`（北大法宝，`cli.py timeliness verify --source supp`）。",
        "",
        "## 2. 状态分布",
        "",
        "| timeliness_status | 行数 |",
        "|---|---|",
    ]
    for k, v in ts.most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "## 3. 核验来源标注分布", "", "| 来源类别 | 行数 |", "|---|---|"]
    for k, v in vs_kind.most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "### 明细（前 12 类标注文本）", "", "| verification_source | 行数 |", "|---|---|"]
    for k, v in vs_detail.most_common(12):
        L.append(f"| {k} | {v} |")
    L += [
        "",
        "## 4. 消费口径建议",
        "",
        "1. classifier 底座 `eff_status` 以归属表时效列为权威（supp 行归入其中，不直接消费 supp cleaned 状态）。",
        "2. 展示/检索类消费 supp cleaned 时明确 `verification_source` 口径（数据源标注），不作北大法宝核验结论。",
        "3. 需要对 supp 补充权威核验 → `cli.py timeliness verify --source supp`（R13 三态，候选=cleaned 空时效行）。",
    ]
    os.makedirs(_REPORTS, exist_ok=True)
    out = os.path.join(_REPORTS, f"supp时效口径审计_{today.replace('-', '')}.md")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"supp 审计报告 → {out}")
    print("  状态:", dict(ts))
    print("  来源类别:", dict(vs_kind))
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
