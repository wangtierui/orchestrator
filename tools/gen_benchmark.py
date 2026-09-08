# -*- coding: utf-8 -*-
"""
tools/gen_benchmark.py — 交付基准登记生成器（二期 2026-09-08）

读取当前数据/代码状态聚合为 BENCHMARK.md（回归对照基线）：门禁清单、测试计数、
各层数据统计（cleaned 快照/归属/底座/明细/桥/时效 state/internal）。任何数据重建或
门禁/测试变化后重跑本脚本刷新登记：
  python tools/gen_benchmark.py            # 覆盖写入根 BENCHMARK.md
"""
from __future__ import annotations

import csv
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import paths  # noqa: E402

_TODAY = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

CLS_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
INT_DATA = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data")
STATE_JSON = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "timeliness_review",
                          "verification_state.json")
CLS_MAP = {"T0": "上位法锚点", "T1": "销售行为与消费者保护", "T2": "偿付能力",
           "T3": "产品条款费率", "T4": "资金运用", "T5": "公司治理",
           "T6": "数据与信息管理", "T7": "风险处置", "T8": "机构管理",
           "T9": "机构设置与撤销/再保险", "T10": "市场行为"}  # 近似名；权威=THEME_MAP


def _csv_len(p: str) -> int:
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def gather() -> dict:
    out: dict = {"generated_at": _TODAY, "python": sys.version.split()[0]}

    # 1) gates
    gates_dir = os.path.join(ROOT, "gates")
    g_files = sorted(f for f in os.listdir(gates_dir) if f.startswith("gate_") and f.endswith(".py"))
    out["gates"] = g_files

    # 2) tests
    tests = sorted(os.path.basename(f) for f in glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    out["test_files"] = tests

    # 3) classifier data
    cd = CLS_DATA
    attr_len = _csv_len(os.path.join(cd, "人身保险公司-文件归属表.csv"))
    theme_len = _csv_len(os.path.join(cd, "人身保险公司-主题归属表.csv"))
    base_tot = final_tot = 0
    theme_rows = {}
    for i in range(1, 11):
        bp = os.path.join(cd, f"_t{i}_base.json")
        fp = os.path.join(cd, f"_t{i}_final.json")
        if os.path.exists(bp):
            base_tot += len(json.load(open(bp, encoding="utf-8")))
        if os.path.exists(fp):
            n = len(json.load(open(fp, encoding="utf-8")))
            final_tot += n
            theme_rows[f"T{i}"] = n
    dets = sorted(f for f in os.listdir(cd) if re.match(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$", f))
    det_tot = sum(_csv_len(os.path.join(cd, f)) for f in dets)
    brid = os.path.join(cd, "rfn_clean_bridge.csv")
    out["classifier"] = {
        "attr_rows": attr_len, "theme_rows": theme_len, "base_total": base_tot,
        "final_total": final_tot, "per_theme": theme_rows, "detail_files": len(dets),
        "detail_rows_total": det_tot,
        "bridge_rows": _csv_len(brid) if os.path.exists(brid) else 0,
        "base_final_matched_citerefs_files": len(glob.glob(os.path.join(cd, "_t*_*.json"))),
    }
    out["state_records"] = len(json.load(open(STATE_JSON, encoding="utf-8")))

    # 4) internal
    proc = os.path.join(INT_DATA, "processed")
    oreg = os.path.join(INT_DATA, "originals")
    out["internal"] = {
        "originals": len([f for f in os.listdir(oreg) if os.path.isfile(os.path.join(oreg, f))])
        if os.path.isdir(oreg) else 0,
        "processed_files": len([f for f in os.listdir(proc) if os.path.isfile(os.path.join(proc, f))])
        if os.path.isdir(proc) else 0,
        "clauses_json": len(glob.glob(os.path.join(proc, "*_clauses.json"))),
        "clauses_md": len(glob.glob(os.path.join(proc, "*_clauses.md"))),
        "merged_records": _read_count(os.path.join(INT_DATA, "merged_view.json"), "count"),
    }

    # 5) clean snapshot dates
    snaps = {}
    idx_p = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "clean_index", "index.json")
    idx = json.load(open(idx_p, encoding="utf-8"))
    srcs = idx.get("sources") or {}
    for sid, sc in srcs.items() if isinstance(srcs, dict) else []:
        blob = json.dumps(sc, ensure_ascii=False)
        m = re.search(r"(20\d{6})", blob)
        snaps[sid] = m.group(1) if m else "?"
    out["clean_snapshots"] = snaps
    out["clean_total_records"] = (idx.get("summary") or {}).get("total_record_count", "?")
    return out


def _read_count(p: str, key: str) -> int:
    if not os.path.exists(p):
        return 0
    try:
        x = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(x, dict):
        if isinstance(x.get(key), int):
            return x[key]
        recs = x.get("records")
        return len(recs) if isinstance(recs, list) else 0
    return len(x) if isinstance(x, list) else 0


def render(g: dict) -> str:
    L = []
    L.append("# BENCHMARK —— 交付基准登记（回归对照基线）\n")
    L.append(f"> 自动生成：tools/gen_benchmark.py @ {g['generated_at']} | python {g['python']}")
    L.append("> 用途：数据重建/重构后重跑 `python tools/gen_benchmark.py` 刷新；数值漂移即回归信号。\n")

    L.append("## 1 门禁（gates/ALL_GATES）\n")
    L.append(f"实装 {len(g['gates'])} 道（gates/gate_*.py）：")
    L.append("```")
    for f in g["gates"]:
        L.append(f"  {f}")
    L.append("```\n")
    L.append("运行：`python cli.py gates`（GatesRunner 汇总，exit code 非 0 即阻断）\n")

    L.append("## 2 自动化验收测试（pytest）\n")
    L.append(f"用例文件 {len(g['test_files'])}：`" + "`、`".join(g["test_files"]) + "`")
    L.append("运行：`python -m pytest tests -q`\n")

    c = g["classifier"]
    L.append("## 3 数据基线\n")
    L.append("### 3.1 外部五源 cleaned（clean_index 快照，SSOT 索引）")
    L.append("")
    L.append("| 源 | 快照日期 | 备注 |")
    L.append("|---|---|---|")
    for sid, d in g["clean_snapshots"].items():
        L.append(f"| {sid} | {d} | 最新 cleaned |")
    L.append(f"| 合计 | — | 索引记录 {g['clean_total_records']} |")
    L.append("")
    L.append("### 3.2 classifier 底座/明细/桥（数据血缘 R10 已注入 generated_*）")
    L.append("")
    L.append("| 产物 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 文件归属表行数 | {c['attr_rows']} |")
    L.append(f"| 主题归属表行数 | {c['theme_rows']} |")
    L.append(f"| base 底座合计（T1–T10） | {c['base_total']} |")
    L.append(f"| final 底座合计（含 cluster/finalized 血缘） | {c['final_total']} |")
    L.append("| 明细表份数 / 行数合计 | " + f"{c['detail_files']} / {c['detail_rows_total']} |")
    L.append(f"| RFN↔clean 溯源桥行数 | {c['bridge_rows']} |")
    L.append(f"| 时效核验 verification_state 记录 | {g['state_records']} |")
    per = "、".join(f"{k}={v}" for k, v in c["per_theme"].items())
    L.append(f"| 各主题 final 记录数 | {per} |")
    L.append("")
    L.append("### 3.3 internal 制度库")
    L.append("")
    L.append("| 产物 | 数值 |")
    L.append("|---|---|")
    L.append(f"| originals 原始制度 | {g['internal']['originals']} |")
    L.append(f"| processed 处理文件（fulltext/main/json/md） | {g['internal']['processed_files']} |")
    L.append(f"| 条文结构 _clauses.json | {g['internal']['clauses_json']} |")
    L.append(f"| 条文视图 _clauses.md | {g['internal']['clauses_md']} |")
    L.append(f"| merged_view 记录 | {g['internal']['merged_records']} |")
    L.append("")
    L.append("## 4 运行入口速查\n")
    L.append("| 命令 | 职责 |")
    L.append("|---|---|")
    L.append("| `python cli.py gates` | 12 道交付门禁 |")
    L.append("| `python cli.py classify --all --steps base,cluster,match,detail,upper,clause_graph` | 底座强序重建（R8 幂等断点） |")
    L.append("| `python cli.py source list / add --id` | 源目录路由（R15） |")
    L.append("| `python cli.py internal index/align/merged` | 内部制度链路 |")
    L.append("| `python cli.py timeliness verify --source all` | 时效核验三态（R13，需北大法宝 token） |")
    L.append("| `clean\\run_clean_pipeline.py --project <源>` | 单源清洗 |")
    L.append("| `recall_audit\\run_retrieval_after_checks.py` | retrieval 四门禁编排（幂等） |")
    L.append("| `python tools/gen_benchmark.py` | 刷新本基准 |")
    L.append("")
    L.append("## 5 回归说明\n")
    L.append("1. 归属表/时效数据变更后：重跑 `classify` 底座链 → `reconcile_clean_drift`（桥）→ gates → 刷新本表。")
    L.append("2. internal 源变后：`internal index`（backfill_clauses 幂等）→ `internal align` → merged 视图。")
    L.append("3. 任一基线与上表不符且非预期升级 → 先查对应门禁 FAIL 输出，勿静默覆盖。")
    L.append("4. 本表只登记当前仓产物；历史一次性脚本（旧仓）不在此列。")
    return "\n".join(L) + "\n"


def main() -> int:
    g = gather()
    text = render(g)
    out_p = os.path.join(ROOT, "BENCHMARK.md")
    with open(out_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"BENCHMARK.md 已写入 {out_p}")
    print(f"  gates={len(g['gates'])} tests={len(g['test_files'])} "
          f"attr={g['classifier']['attr_rows']} base={g['classifier']['base_total']} "
          f"final={g['classifier']['final_total']} detail_rows={g['classifier']['detail_rows_total']} "
          f"bridge={g['classifier']['bridge_rows']} state={g['state_records']} "
          f"merged={g['internal']['merged_records']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
