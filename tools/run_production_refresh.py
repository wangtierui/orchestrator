# -*- coding: utf-8 -*-
"""
run_production_refresh.py —— 生产五源全量数据刷新编排器（2026-09-09）

阶段（严格顺序，任一阶段非零 rc 记录且按策略继续/中止）：
  0 抓取   [可选 --scrape/--collect] gov(--full 全量)/mof/pbc/nfra(全量离线重建)/supp(本地无网络→跳过)
  1 clean   run_clean_pipeline --project 每源（尾部自动增量 clause_index 条文节点）
  2 时效回写 consolidate_timeliness --use-state → apply_timeliness_to_cleaned 五源
  2.5 classify cli.py classify --all（主题底座/明细强序重建，断点幂等；A-02 接线）
  3 reconcile_clean_drift（RFN↔clean 漂移核验，写 drift state/ledger）
  4 recall   run_retrieval_after_checks.py（clean/validity/contract/schema 四门禁）
  5 gates    cli.py gates（13 道交付门禁）
输出：控制台分步执行表 + reports/_tmp/ 或 stdout JSON 汇总（每步 rc/耗时/数据量）。

用法：
  python tools/run_production_refresh.py --no-scrape      # 仅重清洗+全链（raw 已抓取后）
  python tools/run_production_refresh.py --collect all    # 含全量抓取（长时；建议后台/分段）
  python tools/run_production_refresh.py --collect gov,mof
  python tools/run_production_refresh.py --collect nfra-weekly   # nfra 周度增量链（F-O04：
      # 等价周二 01:00 调度链——单实例锁/顶部窗口刷新/详情续跑/离线重建，替代全量）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
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
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"  # 子脚本 ✓/✗ 输出避免 Windows gbk 崩溃
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
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
    ap.add_argument("--supp-batch", default="",
                    help="supp 批量摄取 backlog JSON（可选；F-O05：本地补全文件显式入链）")
    ap.add_argument("--stop-on-error", action="store_true", help="任一步 rc!=0 即中止（默认继续并汇总）")
    args = ap.parse_args()

    report: list[dict] = []
    t_start = time.time()
    # ---- 阶段 0：抓取 ----
    if not args.no_scrape:
        want = [s for s in SOURCES if s in (args.collect == "all" and SOURCES
                                            or args.collect.replace("，", ",").split(","))]
        # F-O04（2026-09-12）：nfra 周度增量链（nfra_weekly.py，原零消费）——周刷链已含
        # 列表顶部窗口刷新 + 详情续跑 + 离线重建，替代全量 collector，避免重复抓取。
        if "nfra-weekly" in args.collect.replace("，", ",").split(","):
            report.append(_run("collect:nfra_weekly",
                               [PY, os.path.join(COLLECTORS, "nfra_weekly.py")], timeout=7200))
            want = [s for s in want if s != "nfra"]
        for src in want:
            cmd = COLLECT_CMD.get(src)
            if not cmd:
                print(f"[collect] {src} 无网络采集（本地摄取/清洗刷新）→ 跳过")
                continue
            if src == "mof":
                # A-14（2026-09-12）：改 shlex.split（原 .split() 遇引号/中文逗号即拆错）。
                cmd = cmd + shlex.split(os.environ.get("MOF_COLLECT_ARGS", ""))
            argv = cmd + [OUT_FLAG.get(src, "--out-dir"), RAW_DIR]
            print(f"[collect] {src} argv={argv}", flush=True)
            report.append(_run(f"collect:{src}", argv, timeout=7200))
            if args.stop_on_error and report[-1]["rc"]:
                break
    # A-12（2026-09-12）：删除假步骤（原 python -c pass 产生 rc=0 的"快照"行，汇总含假成功）；
    # raw 摘要已由汇总 raw 字段（_raw_size）输出。
    # F-O05（2026-09-12）：supp 本地摄取入编排（可选）——此前 supp 链完全在编排外，
    # supp 新文件永不过链。默认跳过（防重写 raw）；需要时经 --supp-batch 显式触发，
    # 或以 collectors/supp_ingest_local_dir.py 收录本地目录。
    if args.supp_batch:
        report.append(_run("supp:ingest_batch",
                           [PY, os.path.join(COLLECTORS, "supp_ingest_batch.py"),
                            "--backlog", args.supp_batch], timeout=1800))
    else:
        print("[collect] supp：未触发本地摄取（经 --supp-batch <backlog.json> 或 "
              "collectors/supp_ingest_local_dir.py）", flush=True)

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

    # ---- 阶段 2.5：classify（主题底座/明细/upper 强序重建）----
    # A-02/F-O01/F-C07（2026-09-12）：生产刷新历史上不接 classify → 归属表/clean 更新后
    # 底座/明细静默过时仍报"完成"。断点幂等（输入未变自动跳过），成本可控。
    report.append(_run("classify:all",
                       [PY, os.path.join(ROOT, "cli.py"), "classify", "--all"], timeout=5400))

    # ---- 阶段 3：reconcile ----
    report.append(_run("reconcile", [PY, os.path.join(CLASSIFIER, "scripts",
                                                      "reconcile_clean_drift.py")], timeout=1200))

    # ---- 阶段 4：recall ----
    report.append(_run("recall", [PY, os.path.join(CLASSIFIER, "recall_audit",
                                                   "run_retrieval_after_checks.py")], timeout=3600))

    # ---- 阶段 4.2：internal merged（内部制度 × RFN 引用视图；F-O07 编排唯一化）----
    report.append(_run("internal:merged", [PY, os.path.join(ROOT, "cli.py"), "internal", "merged"],
                       timeout=1800))

    # ---- 阶段 4.5：reports（全景/主题分类报告生成；F-O06 报告生成入编排）----
    report.append(_run("reports:build",
                       [PY, os.path.join(CLASSIFIER, "scripts", "report_builders",
                                         "build_overview_report.py")], timeout=1800))

    # ---- 阶段 5.5：base publish（双底座发布件 + SQLite/FTS5；Base Contract v1，F-K03）----
    # 发布层在 gates 前刷新：门禁校验的是底座产物，应用模块消费的是发布件（同一批快照）。
    report.append(_run("base:publish", [PY, os.path.join(ROOT, "cli.py"), "base", "publish"],
                       timeout=1800))

    # ---- 阶段 6：gates ----
    report.append(_run("gates", [PY, os.path.join(ROOT, "cli.py"), "gates"], timeout=1800))

    # ---- 阶段 6.8：分析交付库刷新（F-L01）----
    # 数据重建后刷新规划 §2.1 五级分析 15 项交付（docs/reports/）；classify 阶段已
    # 自动触发一次，此处显式再跑确保 merged/publish 后数据面一致（幂等，~2s）。
    report.append(_run("analysis:gen",
                       [PY, os.path.join(ROOT, "cli.py"), "analysis", "gen"], timeout=600))

    # ---- 阶段 6.5：变更监听基线记录（F-O02）----
    # 每次编排运行把各源"日期/记录数/内容 sha"追加到 data/watch_baseline.jsonl；
    # `cli.py source diff` 以此对比"上次运行 → 本次"（同日改写/条数变化可见）。
    report.append(_run("watch:baseline",
                       [PY, os.path.join(ROOT, "cli.py"), "source", "diff", "--record"],
                       timeout=300))

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
