# -*- coding: utf-8 -*-
"""
run_production_refresh.py —— 生产五源全量数据刷新编排器（2026-09-09）

阶段（严格顺序，任一阶段非零 rc 记录且按策略继续/中止）：
  0 抓取   [可选 --scrape/--collect] gov(--full 全量)/mof/pbc/nfra(全量离线重建)/supp(本地无网络→跳过)
  1 clean   run_clean_pipeline --project 每源（尾部自动增量 clause_index 条文节点）
  2 时效回写 consolidate_timeliness --use-state → apply_timeliness_to_cleaned 五源
  3 reconcile_clean_drift（RFN↔clean 漂移核验，写 drift state/ledger）
  4 recall   run_retrieval_after_checks.py（clean/validity/contract/schema 四门禁）
  5 gates    cli.py gates（13 道交付门禁）
输出：控制台分步执行表 + reports/_tmp/ 或 stdout JSON 汇总（每步 rc/耗时/数据量）。

用法：
  python tools/run_production_refresh.py --no-scrape      # 仅重清洗+全链（raw 已抓取后）
  python tools/run_production_refresh.py --collect all    # 含全量抓取（长时；建议后台/分段）
  python tools/run_production_refresh.py --collect gov,mof
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

_THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_THIS)
PY = sys.executable
SCRAPERS = os.path.join(ROOT, "modules", "regulatory_scrapers")
CLASSIFIER = os.path.join(ROOT, "modules", "regulatory_classifier")
REVIEW = os.path.join(SCRAPERS, "timeliness_review")
COLLECTORS = os.path.join(SCRAPERS, "collectors")

SOURCES = ["gov", "mof", "nfra", "pbc", "supp"]
# 各源采集命令（全量语义）。supp 无网站增量 → 由 clean 刷新即可。
COLLECT_CMD = {
    "gov": [PY, os.path.join(COLLECTORS, "gov_collector.py"), "--full"],
    # mof 附件主机 10.1.60.36:8888 曾实测 100% HTTP 502：默认全量会空转数十小时，
    # 可用环境 MOF_COLLECT_ARGS="--no-attachments" 追加逃生参数（仍会抓详情正文）。
    "mof": [PY, os.path.join(COLLECTORS, "mof_collector.py")],
    # nfra 全量：原文（doc/pdf）已默认下载（2026-09-09 五源统一完整文档内容）。
    "nfra": [PY, os.path.join(COLLECTORS, "nfra_collector.py")],
    "pbc": [PY, os.path.join(COLLECTORS, "pbc_collector.py")],
}
# 各源 collector 输出目录参数名不一致（历史遗留），必须按源传参（2026-09-09 接线修复）。
# gov/nfra: --out-dir | mof: --outdir | pbc: --out
OUT_FLAG = {"gov": "--out-dir", "mof": "--outdir", "nfra": "--out-dir", "pbc": "--out"}
RAW_JSON = {
    "gov": "gov_laws.json", "mof": "mof_laws.json",
    "nfra": "nfra_regulations.json", "pbc": "pbc_laws.json",
    "supp": "supplementary_regulations.json",
}
RAW_DIR = os.path.join(SCRAPERS, "data", "raw")


def _run(step: str, argv, cwd=ROOT, timeout: int | None = None) -> dict:
    t0 = time.time()
    rec = {"step": step, "cmd": " ".join(os.path.basename(a) if os.sep in a else a
                                         for a in argv), "rc": -1, "elapsed_s": 0, "tail": ""}
    print(f"\n[step:{step}] {' '.join(rec['cmd'])}", flush=True)
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        rec["rc"] = r.returncode
        rec["tail"] = "\n".join((r.stdout or "").strip().splitlines()[-3:])
        rec["stderr_tail"] = "\n".join((r.stderr or "").strip().splitlines()[-2:])
    except subprocess.TimeoutExpired:
        rec["tail"] = "timeout"
        rec["stderr_tail"] = f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        rec["stderr_tail"] = f"{type(e).__name__}: {e}"
    rec["elapsed_s"] = round(time.time() - t0, 1)
    if rec["tail"]:
        print("    " + "\n    ".join(rec["tail"].splitlines()), flush=True)
    print(f"    → rc={rec['rc']} elapsed={rec['elapsed_s']}s", flush=True)
    return rec


def _raw_records(data) -> int | None:
    """兼容 raw 主库形态：list / {records|items: [...]} / {meta, records}。"""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in ("records", "items"):
            if isinstance(data.get(k), list):
                return len(data[k])
    return None


def _raw_size() -> dict:
    out = {}
    for src, fn in RAW_JSON.items():
        p = os.path.join(RAW_DIR, fn)
        try:
            data = json.load(open(p, encoding="utf-8"))
            out[src] = {"bytes": os.path.getsize(p), "records": _raw_records(data)}
        except Exception:  # noqa: BLE001
            out[src] = {"bytes": os.path.getsize(p) if os.path.exists(p) else 0,
                        "records": None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生产五源全量刷新（抓取→clean→时效→reconcile→recall→gates）")
    ap.add_argument("--no-scrape", action="store_true", help="跳过网络抓取（仅清洗+全链）")
    ap.add_argument("--collect", default="all", help="抓取源: gov/mof/nfra/pbc/supp 逗号分隔或 all")
    ap.add_argument("--stop-on-error", action="store_true", help="任一步 rc!=0 即中止（默认继续并汇总）")
    args = ap.parse_args()

    report: list[dict] = []
    t_start = time.time()
    # ---- 阶段 0：抓取 ----
    if not args.no_scrape:
        want = [s for s in SOURCES if s in (args.collect == "all" and SOURCES
                                            or args.collect.replace("，", ",").split(","))]
        for src in want:
            cmd = COLLECT_CMD.get(src)
            if not cmd:
                print(f"[collect] {src} 无网络采集（本地摄取/清洗刷新）→ 跳过")
                continue
            if src == "mof":
                cmd = cmd + (os.environ.get("MOF_COLLECT_ARGS", "").split() or [])
            argv = cmd + [OUT_FLAG.get(src, "--out-dir"), RAW_DIR]
            print(f"[collect] {src} argv={argv}", flush=True)
            report.append(_run(f"collect:{src}", argv, timeout=7200))
            if args.stop_on_error and report[-1]["rc"]:
                break
    report.append(_run("stage:raw_snapshot", [PY, "-c", "pass"]))

    # ---- 阶段 1：clean（尾部自动 clause）----
    for src in SOURCES:
        report.append(_run(
            f"clean:{src}",
            [PY, os.path.join(SCRAPERS, "clean", "run_clean_pipeline.py"),
             "--project", src, "--raw", os.path.join(RAW_DIR, RAW_JSON[src])],
            timeout=1800))
        if args.stop_on_error and report[-1]["rc"]:
            break

    # ---- 阶段 2：时效回写（consolidate → apply 五源）----
    led = None
    r1 = _run("timeliness:consolidate",
              [PY, os.path.join(REVIEW, "consolidate_timeliness.py"), "--use-state"], timeout=600)
    report.append(r1)
    # 找 consolidate 最新全量清单
    import glob  # noqa: PLC0415
    cands = sorted(glob.glob(os.path.join(REVIEW, "时效性标注结果清单_全量_*.jsonl")))
    if cands:
        led = cands[-1]
        print(f"[timeliness] 使用清单 {os.path.basename(led)}")
    for src in SOURCES:
        report.append(_run(
            f"apply:{src}",
            [PY, os.path.join(REVIEW, "apply_timeliness_to_cleaned.py"),
             "--source", src, "--ledger", led] if led else
            [PY, os.path.join(REVIEW, "apply_timeliness_to_cleaned.py"), "--source", src],
            timeout=600))

    # ---- 阶段 3：reconcile ----
    report.append(_run("reconcile", [PY, os.path.join(CLASSIFIER, "scripts",
                                                      "reconcile_clean_drift.py")], timeout=1200))

    # ---- 阶段 4：recall ----
    report.append(_run("recall", [PY, os.path.join(CLASSIFIER, "recall_audit",
                                                   "run_retrieval_after_checks.py")], timeout=3600))

    # ---- 阶段 5：gates ----
    report.append(_run("gates", [PY, os.path.join(ROOT, "cli.py"), "gates"], timeout=1800))

    summary = {
        "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_s": round(time.time() - t_start, 1),
        "raw": _raw_size(),
        "steps": report,
        "failed": [r for r in report if r["rc"] != 0],
    }
    print("\n" + "=" * 72)
    print("生产全量刷新汇总")
    print("=" * 72)
    print(f"{'step':<22}{'rc':>4}{'elapsed_s':>12}")
    for r in report:
        print(f"{r['step']:<22}{r['rc']:>4}{r['elapsed_s']:>12}")
    print("raw: " + " | ".join(f"{k}={v.get('records')}条/{round(v['bytes']/1048576,1)}MB"
                                for k, v in summary["raw"].items()))
    print(f"总耗时 {summary['total_elapsed_s']}s | 失败步骤 {len(summary['failed'])}")
    for f in summary["failed"]:
        print(f"  ✗ {f['step']} rc={f['rc']} {f.get('tail', '')} {f.get('stderr_tail', '')}")
    out_p = os.path.join(ROOT, "reports", "_tmp",
                         f"生产刷新汇总_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(f"汇总已落盘: {out_p}")
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
