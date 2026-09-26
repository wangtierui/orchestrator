# -*- coding: utf-8 -*-
"""
run_production_refresh.py —— 生产五源全量数据刷新编排器（2026-09-09）

阶段（严格顺序，任一阶段非零 rc 记录且按策略继续/中止）：
 0   抓取   [可选 --scrape/--collect] gov(--full 全量)/mof/pbc/nfra(全量离线重建)/supp(本地无网络→跳过)
 1   clean   run_clean_pipeline --project 每源（尾部自动增量 clause_index 条文节点）
 2   时效回写 consolidate_timeliness --use-state → apply_timeliness_to_cleaned 五源
 2.5 classify cli.py classify --all（主题底座/明细强序重建，断点幂等；A-02 接线）
 2.6 relations cli.py relations gen（依据/废止关系全量重抽取，R-F01；2026-09-14 接入）
     —— 必须在阶段 1/2 写 cleaned 之后、下游消费者（3/4.2/4.5/6.8）之前；否则
     merged 引用原语、drafter 关系素材与交付库 2.1.2.4/2.1.2.5 会**静默反映旧数据**
 3   reconcile clean_drift（RFN↔clean 漂移核验，写 drift state/ledger）
 3.5 governance sync（阶段 2，2026-09-18：元数据四表投影 + 比对断言；断言失败即计入失败步骤）
 4   recall   run_retrieval_after_checks.py（clean/validity/contract/schema 四门禁）
 4.2 internal merged（内部制度 × RFN 引用视图；F-O07 编排唯一化）
 4.5 reports 全量/单主题报告生成（F-O06）
 5.5 base publish（双底座发布件 + SQLite/FTS5；Base Contract v1，F-K03）
 6.8 analysis gen（规划 §2.1 交付库 17 项刷新，F-L01；含关系类 2 项）—— P3-5 前移到门禁之前
 6.5 watch baseline（变更监听基线记录，F-O02）
 6   gates    cli.py gates（**22 道**交付门禁，含 gate_watermark）—— **链尾**（P3-5：门禁须看到本轮全部产物）

阶段 0/1 接线（2026-09-18；`reports/数据流转与存储交互优化方案_20260917.md` §6）：
  - **单实例锁**：本编排自身加 `ProcessLock`（此前只有各 collector 有锁，编排可并发重入）；
  - **run_log**：本次运行登记 `RUN-<ts>-<pid>`，经 `REG_ORCH_RUN_ID` 下传子进程
    （`cli.py gates` 据此把 22 道门禁结果归档进 `gate_result`）；
  - **watermark**：每阶段 rc==0 后登记「产物水位」（产物版本 + 其所依赖的上游版本），
    把本文档阶段表里的**隐式时序约束**变成机器可读的依赖边；`gate_watermark` 据此
    判定「上游已推进、下游未重跑」（替代易受 touch/copy 干扰的 mtime 判据）。
    治理库是**旁路观测设施**：未建库时全部登记为 no-op，登记失败只告警，绝不影响主链 rc。

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
import contextlib
import datetime
import json
import os
import re
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
IPB = os.path.join(ROOT, "modules", "internal_policy_base")
DOCS_REPORTS = os.path.join(ROOT, "docs", "reports")

# 本模块以 `python tools/run_production_refresh.py` 直跑时 sys.path[0] 是 tools/，
# 需显式补仓根才能 import std_lib（阶段 0/1 治理库接线）。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config.exitcodes import ExitCode  # noqa: E402

SOURCES = ["gov", "mof", "nfra", "pbc", "supp"]
# 各源采集命令（全量语义）。supp 无网站增量 → 由 clean 刷新即可。
COLLECT_CMD = {
    # gov 含两个子源：xzfgk（行政法规库）+ zhengceku（国务院政策文件库·部门文件，2026-09-15 纳入）。
    # 去掉历史 `--full`：gov resume 默认开（基于 detail_url 跳过已抓、与主库合并），
    # 既可持续补全 zhengceku 的历史存量（约 629 页 / 1.7 万条，全量约需 30 小时，
    # 由每日增量反复调用逐步收敛），又能跟进两个子源的新增条目。
    # 需要一次性全量重抓时手工执行：gov_collector.py --source all --full
    "gov": [PY, os.path.join(COLLECTORS, "gov_collector.py"), "--source", "all"],
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
    "gov": "gov_laws.json",
    "mof": "mof_laws.json",
    "nfra": "nfra_regulations.json",
    "pbc": "pbc_laws.json",
    "supp": "supplementary_regulations.json",
}
RAW_DIR = os.path.join(SCRAPERS, "data", "raw")
CLS_DATA = os.path.join(CLASSIFIER, "data")
IPB_DATA = os.path.join(IPB, "data")
PUB_EXT = os.path.join(SCRAPERS, "published")
PUB_INT = os.path.join(IPB, "published")

# --------------------------------------------------------------------------- #
# 治理库：产物水位声明表（阶段 1，2026-09-18）
#   `inputs` 为**声明式依赖边**：登记时实时向治理库查该依赖的当前版本，写入 inputs_json；
#   门禁（gate_watermark）比对「声明版本 vs 当前版本」，不等即"上游已推进、下游未重跑"。
#
# 覆盖度取舍（阶段 1 刻意保守，避免假阻断）：
#   - 只声明**本次刷新链内自产自销**的边（cleaned / clauses / relations / merged / published）；
#   - `rfn_attr` / `rfn_theme` / `timeliness:state` 仅**观察登记、不声明为依赖**：
#     它们在链内被 stage 3 reconcile 二次改写，若在 stage 2.5/2.6 声明其版本，
#     同一次运行内必然自相矛盾（正是 gate_rfn_sync / gate_timeliness_ssot 的职责域）。
#     attr 边留待阶段 4「判据切换」与旧判据一并处理。
#   - `cleaned:<src>` 只在**阶段 2（时效回写）之后**登记一次：apply_timeliness_to_cleaned
#     会 jsonl+csv 双轨回写三字段，阶段 1 的 cleaned 版本随即作废。
#     v2 §3.3.1（2026-09-26）：`clauses:<src>` 现已声明 `cleaned:<src>` 上游——"版本随即作废"
#     不再致命，因为二者同属一轮（`round` 相同）→ `dependency_edges` 判 `ok_within_round`
#     而非 `stale`。判据语义从"结构性 FAIL"转为"同轮顺序特性披露"。
# --------------------------------------------------------------------------- #
GOV_ARTIFACTS: dict[str, dict] = {
    # `stage`（v2 §3.3.1，2026-09-26 新增）：产生该产物的阶段 id，仅审计/排障用、不参与判据
    # （判据用 `round` = run_id，由 `_wm` 自动取 `current_run_id()`）。
    # ---- 观察型（无声明依赖；供下游 `inputs` 引用）----
    "clean_index": {
        "produced_by": "regulatory_scrapers.clean_index.build_clean_index",
        "stage": "1",
    },
    "rfn_attr": {"produced_by": "regulatory_classifier.rfn.registry._save_rows", "stage": "3"},
    "rfn_theme": {
        "produced_by": "regulatory_classifier.rfn.registry._save_theme_rows",
        "stage": "3",
    },
    "timeliness:state": {
        "produced_by": "timeliness_review.verification_state.save_state",
        "stage": "2",
    },
    "internal_index": {"produced_by": "internal_policy_base.indexer.ingest", "stage": "3"},
    # ---- 声明依赖型（阶段 1 主链边）----
    # v2 §3.3.1（2026-09-26）：`clauses:{src}` **现声明** `cleaned:{src}` 上游。
    #   此前不声明的原因——"阶段 1 的 cleaned 版本会被阶段 2 时效回写作废，声明即恒 stale"
    #   ——已由 `dependency_edges` 的新增态 `ok_within_round` 解决：clauses（阶段 1）与
    #   cleaned（阶段 2）同在**同一轮**（round = 同一 run_id），版本差异不再判陈旧。
    #   残留语义差（clauses 基于阶段 1 的**瞬时** cleaned，而非阶段 2 回写后版本）由
    #   detail 披露；是否需在阶段 2 之后重算 clauses 属「判据切换」议题，另行评估。
    "clauses:{src}": {
        "produced_by": "regulatory_scrapers.clause_index.build_clause_index",
        "stage": "1",
        "inputs": ["cleaned:{src}"],
    },
    "cleaned:{src}": {
        "produced_by": "regulatory_scrapers.clean.run_clean_pipeline.main",
        "stage": "2",
    },
    "classify:products": {
        "produced_by": "regulatory_classifier.scripts.classify.run",
        "stage": "2.5",
        "inputs": ["cleaned:{src}"],
    },
    "relations_index": {
        "produced_by": "tools.extract_relations",
        "stage": "2.6",
        "inputs": ["cleaned:{src}", "internal_index"],
    },
    "reconcile:drift": {
        "produced_by": "regulatory_classifier.scripts.reconcile_clean_drift",
        "stage": "3",
        "inputs": ["cleaned:{src}"],
    },
    "merged_view": {
        "produced_by": "internal_policy_base.merged.build_merged_view",
        "stage": "4.2",
        "inputs": ["internal_index"],
    },
    "published:external": {
        "produced_by": "base_publish.build_external+build_fts",
        "stage": "5.5",
        "inputs": ["cleaned:{src}", "clauses:{src}", "relations_index"],
    },
    "published:internal": {
        "produced_by": "base_publish.build_internal+build_fts",
        "stage": "5.5",
        "inputs": ["internal_index", "merged_view"],
    },
    # `analysis:manifest` —— **P3-5 已解耦（2026-09-26）**：原登记（N-13）为"只观察、不声明依赖"，
    #   因其在 6.8 生成而门禁在阶段 6 运行 → 门禁看到**上一轮**产物、依赖已在本轮推进
    #   → artifact.round ≠ dep.round → 判 `stale` → 声明即结构性恒 FAIL。
    #   解法（原 N-13 方案 (a)）：**把 gates 移为链尾**（现 `STEP_ORDER` 末位），
    #   门禁因此看到**本轮**的 `analysis:manifest`，依赖可按同轮判定。
    #   影响面已在第五批报告中评估登记：`gate_result` 仍记同一 run_id（无归档变化）、
    #   BENCHMARK 基线不受影响（gen_benchmark 独立跑门禁）、人工心智更顺（"最后一道门"）。
    #   输入取 `gen_analysis_deliveries` 的真实读取面（classifier 数据 + 双底座发布件 +
    #   关系产物 + 内部制度 merged 视图）。
    "analysis:manifest": {
        "produced_by": "tools.gen_analysis_deliveries",
        "stage": "6.8",
        "inputs": [
            "clean_index",
            "rfn_attr",
            "rfn_theme",
            "relations_index",
            "merged_view",
            "published:external",
            "published:internal",
        ],
    },
}


def _expand(keys) -> list[str]:
    """把 `{src}` 占位展开为五源（声明表用单行表达 5 条同类边）。"""
    out: list[str] = []
    for k in keys or ():
        if "{src}" in k:
            out.extend(k.replace("{src}", s) for s in SOURCES)
        else:
            out.append(k)
    return out


def _clean_idx():
    """clean_index 单例（唯一入口）；不可用返回 None（调用方降级）。"""
    try:
        if SCRAPERS not in sys.path:
            sys.path.insert(0, SCRAPERS)
        from clean_index import get_clean_index  # noqa: PLC0415

        return get_clean_index()
    except Exception as e:  # noqa: BLE001
        print(f"[watermark] WARN clean_index 不可用: {type(e).__name__}: {e}")
        return None


def _cleaned_jsonl(src: str) -> str:
    """该源最新 cleaned **jsonl**（权威轨）绝对路径。"""
    idx = _clean_idx()
    return (idx.latest_jsonl_path(src) or "") if idx else ""


def _cleaned_count(src: str):
    """该源最新快照记录数（供水位披露；取不到返回 None）。"""
    idx = _clean_idx()
    return idx.record_count(src) if idx else None


def _clauses_jsonl(src: str) -> str:
    """该源 clauses jsonl（data/clauses/，排除 history 轮转目录）。"""
    d = os.path.join(SCRAPERS, "data", "clauses")
    if not os.path.isdir(d):
        return ""
    pat = re.compile(rf"^{src}_clauses_(\d{{8}})\.jsonl$")
    cands = sorted(f for f in os.listdir(d) if pat.match(f))
    return os.path.join(d, cands[-1]) if cands else ""


def _pub_count(path: str):
    """发布件清单里的代表性条数（records/policies/clauses）；取不到返回 None。"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
    for k in ("records", "policies", "clauses"):
        for holder in (d, counts):
            v = holder.get(k)
            if isinstance(v, int):
                return v
    return None


def _detail_tables() -> list[str]:
    """classify 产物族的代表集：11 份 T*_*逐份条款引用与上位法依据明细表.csv。"""
    if not os.path.isdir(CLS_DATA):
        return []
    return [
        os.path.join(CLS_DATA, f)
        for f in sorted(os.listdir(CLS_DATA))
        if f.startswith("T") and f.endswith(".csv")
    ]


def _spec_of(artifact_key: str) -> dict:
    """取产物声明：先精确键，再回退 `{src}` 模板键（`cleaned:gov` → `cleaned:{src}`）。"""
    if artifact_key in GOV_ARTIFACTS:
        return GOV_ARTIFACTS[artifact_key]
    for s in SOURCES:
        tpl = artifact_key.replace(f":{s}", ":{src}")
        if tpl in GOV_ARTIFACTS:
            return GOV_ARTIFACTS[tpl]
    return {}


def _wm(
    artifact_key: str, *, rc: int = 0, version: str = "", paths=(), record_count: int | None = None
) -> None:
    """登记产物水位（尽力而为；不影响主链 rc）。

    version 缺省时由 paths 求文件哈希（多文件取联合哈希）；
    inputs 由声明表（含 `{src}` 模板回退）给出并经**当前水位表**解析为具体版本。
    """
    if rc != 0:
        return
    try:
        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

        if not gs.enabled():
            return
        spec = _spec_of(artifact_key)
        if not version:
            plist = [p for p in paths if p and os.path.exists(p)]
            version = (
                gs.version_of_files(plist)
                if len(plist) > 1
                else gs.version_of_file(plist[0])
                if plist
                else ""
            )
        if not version:
            print(f"[watermark] 跳过 {artifact_key}：产物不存在或无版本")
            return
        inputs: dict[str, str] = {}
        for dep in _expand(spec.get("inputs")):
            w = gs.get_watermark(dep)
            if w and w.get("version"):
                inputs[dep] = w["version"]
        gs.record_watermark(
            artifact_key,
            spec.get("produced_by", ""),
            version,
            inputs=inputs,
            record_count=record_count,
            # v2 §3.3.1：轮次取当前 run_id（链内由 run_start 置入 env），
            # 供 dependency_edges 判 `ok_within_round`；stage 取声明表的阶段 id。
            round_id=gs.current_run_id(),
            stage=spec.get("stage", ""),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[watermark] WARN {artifact_key} 登记失败（不影响主链）: {type(e).__name__}: {e}")


def _wm_observations() -> None:
    """登记**观察型**产物（链外写方，只记录当前版本供下游引用/审计）。"""
    try:
        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

        if not gs.enabled():
            return
    except Exception:  # noqa: BLE001
        return
    obs = {
        "rfn_attr": os.path.join(CLS_DATA, "人身保险公司-文件归属表.csv"),
        "rfn_theme": os.path.join(CLS_DATA, "人身保险公司-主题归属表.csv"),
        "timeliness:state": os.path.join(REVIEW, "verification_state.json"),
        "internal_index": os.path.join(IPB_DATA, "internal_policy_index.json"),
        "clean_index": os.path.join(SCRAPERS, "clean_index", "index.json"),
    }
    for key, p in obs.items():
        _wm(key, paths=[p])


def _exit_semantic(rc: int) -> str:
    """退出码语义化（v2 §3.6 X4）：整数 → `ExitCode` 成员名；非受控值标注 `RAW(...)`。

    动机：编排器原先把 rc 当裸数字看，`2` 究竟是"数据问题/配置不可用/依赖缺失"
    无法区分（v2 §2.4.2 的四义冲突）。语义化后 `run_log.note` 与汇总 JSON 都带名字。
    """
    try:

        return ExitCode(rc).name
    except Exception:  # noqa: BLE001  非受控值（子进程自定义码）→ 显式标注
        return f"RAW({rc})"


def _run(step: str, argv, cwd=ROOT, timeout: int | None = None) -> dict:
    global _FROM_HIT
    t0 = time.time()
    rec = {
        "step": step,
        "cmd": " ".join(os.path.basename(a) if os.sep in a else a for a in argv),
        "rc": -1,
        "elapsed_s": 0,
        "tail": "",
        # X4（P1-5）：语义化退出码 + 完整 stderr 长度（tail 只留 2 行，长度反映真实体量）
        "exit_code": "",
        "stderr_tail": "",
        "stderr_tail_len": 0,
        "stdout_len": 0,
        # P2-6：步骤选择/续跑/试跑的可解释标记（进 run_step 台账与汇总 JSON）
        "skipped": False,
        "note": "",
    }
    # ---- 步骤选择与续跑（v2 §3.13.3，P2-6）----
    skip_reason = _skip_reason(step)
    if skip_reason:
        rec.update(
            {
                "rc": 0,
                "exit_code": "SKIP",
                "skipped": True,
                "note": skip_reason,
                "elapsed_s": round(time.time() - t0, 1),
            }
        )
        _record_step(rec)
        print(f"\n[step:{step}] SKIP（{skip_reason}）", flush=True)
        return rec
    print(f"\n[step:{step}] {' '.join(rec['cmd'])}", flush=True)
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"  # 子脚本 ✓/✗ 输出避免 Windows gbk 崩溃
        r = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        rec["rc"] = r.returncode
        rec["exit_code"] = _exit_semantic(r.returncode)
        rec["tail"] = "\n".join((r.stdout or "").strip().splitlines()[-3:])
        rec["stderr_tail"] = "\n".join((r.stderr or "").strip().splitlines()[-2:])
        rec["stderr_tail_len"] = len(r.stderr or "")
        rec["stdout_len"] = len(r.stdout or "")
    except subprocess.TimeoutExpired:
        rec["tail"] = "timeout"
        rec["exit_code"] = "TIMEOUT"
        rec["stderr_tail"] = f"timeout {timeout}s"
        rec["stderr_tail_len"] = len(rec["stderr_tail"])
    except Exception as e:  # noqa: BLE001
        rec["exit_code"] = f"EXC:{type(e).__name__}"
        rec["stderr_tail"] = f"{type(e).__name__}: {e}"
        rec["stderr_tail_len"] = len(rec["stderr_tail"])
    rec["elapsed_s"] = round(time.time() - t0, 1)
    if rec["tail"]:
        print("    " + "\n    ".join(rec["tail"].splitlines()), flush=True)
    print(
        f"    → rc={rec['rc']}({rec['exit_code']}) elapsed={rec['elapsed_s']}s"
        f" stderr={rec['stderr_tail_len']}B",
        flush=True,
    )
    _record_step(rec)
    return rec


# --------------------------------------------------------------------------- #
# 步骤选择 / 续跑 / 试跑（v2 §3.13.3，P2-6）
# --------------------------------------------------------------------------- #
# 步骤清单（**执行顺序**）：`--from` 的先后判定、`--list-steps` 的输出、`--only` 的合法性
# 都以此为准；`gate_config_integrity` 判据 R 断言它与本文件实际 `_run("<step>"` 调用点一致
# （防"清单漂移"——这正是本仓其它清单的既有治理方式）。
STEP_ORDER: tuple[str, ...] = (
    "collect:nfra_weekly",
    "collect:{src}",
    "supp:ingest_batch",
    "clean:{src}",
    "timeliness:consolidate",
    "apply:{src}",
    "classify:all",
    "relations:gen",
    "reconcile",
    "governance:sync",
    "recall",
    "internal:merged",
    "reports:build",
    "base:publish",
    "analysis:gen",
    "watch:baseline",
    "gates",
)
# ⚠️ 上表为**执行顺序**（取自本文件 `_run(...)` 调用点出现次序，2026-09-26 实测）。
#    **gates 已移为链尾**（P3-5 顺序解耦，同日）：原顺序 gates→analysis:gen→watch:baseline
#    使 `analysis:manifest` 无法声明依赖（门禁看到上一轮产物）。此处以**实装为准**；
#    模块 docstring 的阶段编号（6 gates / 6.5 watch / 6.8 analysis）为旧口径，
#    已在第四批报告 N-29 与第五批报告登记。
#    `{src}` 为动态步骤（五源循环），匹配按前缀进行。


def _step_index(step: str) -> int:
    """步骤在 `STEP_ORDER` 中的序号（动态步骤按 `{src}` 前缀匹配）；未登记 → -1。"""
    for i, pat in enumerate(STEP_ORDER):
        if "{src}" in pat:
            head = pat.split("{src}")[0]
            if step.startswith(head) and step != head:
                return i
        elif step == pat:
            return i
    return -1


def _step_matches(step: str, sel: str) -> bool:
    """步骤是否被选择器命中（精确、前缀、或对动态步骤的族前缀）。"""
    if not sel:
        return False
    if step == sel or step.startswith(sel):
        return True
    for pat in STEP_ORDER:
        if "{src}" in pat and (sel == pat or sel.rstrip(":{") == pat.split("{src}")[0].rstrip(":")):
            head = pat.split("{src}")[0]
            if step.startswith(head):
                return True
    return False


_RESUME_SKIP: set[str] = set()  # 续跑可跳过集（判据见 _resume_plan）
_ONLY_STEPS: tuple[str, ...] = ()
_FROM_STEP = ""
_FROM_HIT = False
_DRY_RUN = False


def _skip_reason(step: str) -> str:
    """该步骤是否跳过（返回原因；空串 = 执行）。"""
    global _FROM_HIT
    if _ONLY_STEPS and not any(_step_matches(step, s) for s in _ONLY_STEPS):
        return f"--only 未选中（{list(_ONLY_STEPS)}）"
    if _FROM_STEP and not _FROM_HIT:
        if _step_matches(step, _FROM_STEP):
            _FROM_HIT = True
        else:
            return f"--from {_FROM_STEP} 之前的步骤"
    if _DRY_RUN:
        return "--dry-run（只列 argv，不执行）"
    if step in _RESUME_SKIP:
        return "--resume：上次 rc=0 且水位无 stale/未登记"
    return ""


def _record_step(rec: dict) -> None:
    """把步骤结果写入治理库 `run_step`（旁路设施：登记失败不得中断主链）。"""
    try:
        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415

        gs.step_record(
            rec.get("step", ""),
            rec.get("rc", -1),
            exit_code=rec.get("exit_code", ""),
            elapsed_s=rec.get("elapsed_s", 0),
            skipped=bool(rec.get("skipped")),
            note=(rec.get("note") or rec.get("tail", ""))[:200],
        )
    except Exception:  # noqa: BLE001  旁路设施
        pass


def _resume_plan() -> dict:
    """计算续跑可跳过集。**跳过判据只与水位同源**（v2 R13：不得自建第二套判据）：

        skip ⟺ 「上次该步骤 rc=0（且非跳过）」**且**「全库水位无 stale/未登记」。

    水位有异常 → 一律全量重跑（宁重跑不跳：重跑的代价是时间，跳错的代价是数据陈旧）。
    """
    try:
        from std_lib.common_lib import governance_store as gs  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "reason": f"治理库不可导入：{type(e).__name__}"}
    if not gs.enabled():
        return {"enabled": False, "reason": "治理库未启用 → 不跳过任何步骤"}
    rid, steps = gs.last_run_with_steps()
    if not rid:
        return {"enabled": False, "reason": "无历史步骤台账 → 不跳过"}
    ok, det = gs.check_dependencies()
    if not ok:
        return {
            "enabled": False,
            "reason": "水位存在 stale/未登记 → 全量重跑",
            "from_run": rid,
            "watermark": det,
        }
    skip = {s["step"] for s in steps if s["rc"] == 0 and not s.get("skipped")}
    return {"enabled": True, "from_run": rid, "count": len(skip), "steps": sorted(skip)}


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
            out[src] = {"bytes": os.path.getsize(p) if os.path.exists(p) else 0, "records": None}
    return out


def _rc_of(report: list[dict], step: str):
    """取指定步骤的 rc；步骤未执行（如 --stop-on-error 提前中止）→ None（调用方跳过登记）。"""
    for r in reversed(report):
        if r["step"] == step:
            return r["rc"]
    return None


def _run_chain(args) -> int:
    report: list[dict] = []
    t_start = time.time()
    # ---- 阶段 0：抓取 ----
    if not args.no_scrape:
        want = [
            s
            for s in SOURCES
            if s
            in (args.collect == "all" and SOURCES or args.collect.replace("，", ",").split(","))
        ]
        # F-O04（2026-09-12）：nfra 周度增量链（nfra_weekly.py，原零消费）——周刷链已含
        # 列表顶部窗口刷新 + 详情续跑 + 离线重建，替代全量 collector，避免重复抓取。
        if "nfra-weekly" in args.collect.replace("，", ",").split(","):
            report.append(
                _run(
                    "collect:nfra_weekly",
                    [PY, os.path.join(COLLECTORS, "nfra_weekly.py")],
                    timeout=7200,
                )
            )
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
        report.append(
            _run(
                "supp:ingest_batch",
                [
                    PY,
                    os.path.join(COLLECTORS, "supp_ingest_batch.py"),
                    "--backlog",
                    args.supp_batch,
                ],
                timeout=1800,
            )
        )
    else:
        print(
            "[collect] supp：未触发本地摄取（经 --supp-batch <backlog.json> 或 "
            "collectors/supp_ingest_local_dir.py）",
            flush=True,
        )

    # ---- 阶段 1：clean（尾部自动 clause）----
    for src in SOURCES:
        report.append(
            _run(
                f"clean:{src}",
                [
                    PY,
                    os.path.join(SCRAPERS, "clean", "run_clean_pipeline.py"),
                    "--project",
                    src,
                    "--raw",
                    os.path.join(RAW_DIR, RAW_JSON[src]),
                ],
                timeout=1800,
            )
        )
        if args.stop_on_error and report[-1]["rc"]:
            break

    # ---- 阶段 1 水位：clauses（clean 尾部节点产物）----
    # 不声明 cleaned 上游：apply_timeliness 会在阶段 2 双轨回写 cleaned，
    # 届时阶段 1 的 cleaned 版本作废（已知顺序特性，见 GOV_ARTIFACTS 注释）。
    for src in SOURCES:
        _wm(f"clauses:{src}", rc=_rc_of(report, f"clean:{src}"), paths=[_clauses_jsonl(src)])

    # ---- 阶段 2：时效回写（consolidate → apply 五源）----
    led = None
    r1 = _run(
        "timeliness:consolidate",
        [PY, os.path.join(REVIEW, "consolidate_timeliness.py"), "--use-state"],
        timeout=600,
    )
    report.append(r1)
    # 找 consolidate 最新全量清单
    import glob  # noqa: PLC0415

    cands = sorted(glob.glob(os.path.join(REVIEW, "时效性标注结果清单_全量_*.jsonl")))
    if cands:
        led = cands[-1]
        print(f"[timeliness] 使用清单 {os.path.basename(led)}")
    for src in SOURCES:
        report.append(
            _run(
                f"apply:{src}",
                [
                    PY,
                    os.path.join(REVIEW, "apply_timeliness_to_cleaned.py"),
                    "--source",
                    src,
                    "--ledger",
                    led,
                ]
                if led
                else [PY, os.path.join(REVIEW, "apply_timeliness_to_cleaned.py"), "--source", src],
                timeout=600,
            )
        )

    # ---- 阶段 2 水位：cleaned（时效回写后的**最终**版本；下游（2.5/2.6/3/5.5）依赖此版本）----
    for src in SOURCES:
        _wm(
            f"cleaned:{src}",
            rc=_rc_of(report, f"apply:{src}"),
            paths=[_cleaned_jsonl(src)],
            record_count=_cleaned_count(src),
        )

    # ---- 阶段 2.5：classify（主题底座/明细/upper 强序重建）----
    # A-02/F-O01/F-C07（2026-09-12）：生产刷新历史上不接 classify → 归属表/clean 更新后
    # 底座/明细静默过时仍报"完成"。断点幂等（输入未变自动跳过），成本可控。
    report.append(
        _run("classify:all", [PY, os.path.join(ROOT, "cli.py"), "classify", "--all"], timeout=5400)
    )
    # 阶段 2.5 水位：分类产物族（以 11 份明细表为版本代表集——交付级产物、体量小）
    _wm("classify:products", rc=_rc_of(report, "classify:all"), paths=_detail_tables())

    # ---- 阶段 2.55：观察型水位**提前登记**（2026-09-19 修复顺序缺陷）----
    # 动因：`relations_index` 声明 `internal_index` 为依赖，而解析依赖版本走**当前水位表**；
    # 观察型登记历史上只在阶段 3 做 → 若 `internal_index` 在**本次链之前**被链外写方改动
    # （如 `cli internal backfill` 回刷章条数），阶段 2.6 解析到的是**上一轮的旧版本**，
    # 阶段 3 再把新版本登记进去 → `gate_relations` 判据 8 / `gate_watermark` 当场报"产物陈旧"
    # （实测 2026-09-19：declared=c2a9f380… vs current=dd919a09…）。
    # 位置纪律：紧邻 relations:gen 之前登记一次（取到链前终态），阶段 3 之后再登记一次
    # （reconcile 可能改写 attr → 保证观察值在该阶段后仍为终态）；同版本重复登记幂等。
    _wm_observations()

    # ---- 阶段 2.6：relations（依据/废止关系全量重抽取，R-F01）----
    # 动因（2026-09-14）：本链历史上**不接** `relations gen` → cleaned/归属表/内部正文更新后
    # `relations_index.jsonl` 静默过时，而**依赖它的消费面会被连带污染**：merged 引用原语、
    # drafter「§2 依据与废止关系」素材、以及交付库 2.1.2.4 关系图谱 / 2.1.2.5 补登清单
    # （后两者由阶段 6.8 `analysis gen` 从关系产物重建 → 若不重抽取，报告"刷新"了却反映旧数据）。
    # 位置纪律：必须在**阶段 1/2 写 cleaned 之后**（本阶段 2.6 即满足），且在下游消费者
    # （阶段 3 reconcile / 4.2 internal merged / 4.5 reports / 6.8 analysis）之前。
    # 不加 `--report`：报告统一由阶段 6.8 analysis gen 产出（**单一写入方**，避免重复写同一文件）。
    report.append(
        _run("relations:gen", [PY, os.path.join(ROOT, "cli.py"), "relations", "gen"], timeout=1800)
    )
    # 阶段 2.6 水位：关系事实源（声明 cleaned 五源 + internal_index 为依赖）
    _wm(
        "relations_index",
        rc=_rc_of(report, "relations:gen"),
        paths=[os.path.join(CLS_DATA, "relations", "relations_index.jsonl")],
    )

    # ---- 阶段 3：reconcile ----
    report.append(
        _run(
            "reconcile",
            [PY, os.path.join(CLASSIFIER, "scripts", "reconcile_clean_drift.py")],
            timeout=1200,
        )
    )
    # 阶段 3 水位：漂移核验状态（gate_rfn_drift 的判据载体）
    _wm(
        "reconcile:drift",
        rc=_rc_of(report, "reconcile"),
        paths=[os.path.join(CLS_DATA, "rfn_drift_state.json")],
    )
    # 观察型登记（第二次）：链外写方（归属表/主题表/时效 state/内部索引/clean_index）。
    # 放在 stage 3 之后 = 链内最后一个 attr 写方（reconcile）之后，版本才稳定。
    # 注：阶段 2.55 已提前登记一次（供 relations:gen 解析依赖版本），此处为终态刷新。
    _wm_observations()

    # ---- 阶段 3.5：治理库元数据投影（阶段 2「双写期」；2026-09-18）----
    # 语义：**文件仍是事实源**（读方不变），本步把四类元数据（归属表/主题表/内部主索引/
    # 关系产物/时效 state）事务化投影进 governance.db，并在同一命令内做**比对断言**
    # （库内摘要 vs 由文件重算的摘要）——不一致即"库与文件分叉"，计入失败步骤（不静默）。
    # 位置纪律：在链内最后一个 attr 写方（stage 3 reconcile）之后，保证投影取到终态。
    report.append(
        _run(
            "governance:sync",
            [PY, os.path.join(ROOT, "tools", "governance_sync.py"), "--apply"],
            timeout=900,
        )
    )

    # ---- 阶段 4：recall ----
    report.append(
        _run(
            "recall",
            [PY, os.path.join(CLASSIFIER, "recall_audit", "run_retrieval_after_checks.py")],
            timeout=3600,
        )
    )

    # ---- 阶段 4.2：internal merged（内部制度 × RFN 引用视图；F-O07 编排唯一化）----
    report.append(
        _run(
            "internal:merged",
            [PY, os.path.join(ROOT, "cli.py"), "internal", "merged"],
            timeout=1800,
        )
    )
    # 阶段 4.2 水位：制度×RFN 引用视图（声明 internal_index 为依赖；
    # 其 processed 目录签名新鲜度由既有 gate_citations 的 inputs 指纹继续覆盖，此处不重复声明）
    _wm(
        "merged_view",
        rc=_rc_of(report, "internal:merged"),
        paths=[os.path.join(IPB_DATA, "merged_view.json")],
    )

    # ---- 阶段 4.5：reports（全景/主题分类报告生成；F-O06 报告生成入编排）----
    report.append(
        _run(
            "reports:build",
            [
                PY,
                os.path.join(CLASSIFIER, "scripts", "report_builders", "build_overview_report.py"),
            ],
            timeout=1800,
        )
    )

    # ---- 阶段 5.5：base publish（双底座发布件 + SQLite/FTS5；Base Contract v1，F-K03）----
    # 发布层在 gates 前刷新：门禁校验的是底座产物，应用模块消费的是发布件（同一批快照）。
    report.append(
        _run("base:publish", [PY, os.path.join(ROOT, "cli.py"), "base", "publish"], timeout=1800)
    )
    # 阶段 5.5 水位：双底座发布件（版本取 publish_manifest.json——已含 sha/计数，
    # 避免对 733 MB 的 external_index.sqlite 二次哈希）
    _rc_pub = _rc_of(report, "base:publish")
    _m_ext = os.path.join(PUB_EXT, "publish_manifest.json")
    _m_int = os.path.join(PUB_INT, "publish_manifest.json")
    _wm("published:external", rc=_rc_pub, paths=[_m_ext], record_count=_pub_count(_m_ext))
    _wm("published:internal", rc=_rc_pub, paths=[_m_int], record_count=_pub_count(_m_int))

    # ---- 阶段 6.8：分析交付库刷新（F-L01）----
    # 数据重建后刷新规划 §2.1 五级分析 17 项交付（docs/reports/）；classify 阶段已
    # 自动触发一次，此处显式再跑确保 merged/publish 后数据面一致（幂等，~2s）。
    #
    # P3-5（2026-09-26）**顺序解耦**：本步与 6.5 已**前移到 gates 之前**。
    #   原顺序（gates 在阶段 6 → analysis:gen 在 6.8）使 `gate_watermark` 看到的是
    #   **上一轮**的 `analysis:manifest`，而它的依赖已在本轮推进 → artifact.round ≠ dep.round
    #   → 判 `stale` → **一旦声明 inputs 即结构性恒 FAIL**（v2 §3.3.1 的 `ok_within_round`
    #   对"产在被判之后"无解）。把 gates 收束为**链尾**后，门禁看到的是本轮产物，
    #   `analysis:manifest` 因而可正常声明依赖（见文件头 `_ARTIFACTS`）。
    report.append(
        _run("analysis:gen", [PY, os.path.join(ROOT, "cli.py"), "analysis", "gen"], timeout=600)
    )
    # 阶段 6.8 水位：分析交付库清单（版本取 _manifest.json）
    _wm(
        "analysis:manifest",
        rc=_rc_of(report, "analysis:gen"),
        paths=[os.path.join(DOCS_REPORTS, "_manifest.json")],
    )

    # ---- 阶段 6.5：变更监听基线记录（F-O02）----
    # 每次编排运行把各源"日期/记录数/内容 sha"追加到 data/watch_baseline.jsonl；
    # `cli.py source diff` 以此对比"上次运行 → 本次"（同日改写/条数变化可见）。
    report.append(
        _run(
            "watch:baseline",
            [PY, os.path.join(ROOT, "cli.py"), "source", "diff", "--record"],
            timeout=300,
        )
    )

    # ---- 阶段 6：gates（**链尾**，P3-5）----
    # 交付门禁应在**全部产物生成之后**运行：① 才覆盖本轮 6.8 的 `analysis:manifest`；
    # ② 与人工心智一致（"最后一道门"）；③ `gate_result` 仍记同一 run_id，无归档方式变化；
    # ④ 门禁失败时其**之前**的产物已落盘（与改动前一致：原顺序下 analysis:gen 也在门禁之后）。
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
    print(
        "raw: "
        + " | ".join(
            f"{k}={v.get('records')}条/{round(v['bytes'] / 1048576, 1)}MB"
            for k, v in summary["raw"].items()
        )
    )
    print(f"总耗时 {summary['total_elapsed_s']}s | 失败步骤 {len(summary['failed'])}")
    for f in summary["failed"]:
        print(
            f"  ✗ {f['step']} rc={f['rc']}({f.get('exit_code', '')})"
            f" stderr={f.get('stderr_tail_len', 0)}B"
            f" {f.get('tail', '')} {f.get('stderr_tail', '')}"
        )
    out_p = os.path.join(
        ROOT,
        "reports",
        "_tmp",
        f"生产刷新汇总_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(f"汇总已落盘: {out_p}")
    # P2-6：失败 → 告警通道（v2 §3.13.6；无人值守下"失败无人知晓"是原设计的硬缺口）
    if summary["failed"]:
        try:
            from std_lib.common_lib import notify as _notify  # noqa: PLC0415

            _notify.notify(
                "failed_step",
                f"生产刷新失败步骤 {len(summary['failed'])} 个",
                {
                    "summary_json": out_p,
                    "total_elapsed_s": summary["total_elapsed_s"],
                    "failed": [
                        {
                            k: f.get(k)
                            for k in (
                                "step",
                                "rc",
                                "exit_code",
                                "elapsed_s",
                                "stderr_tail_len",
                                "tail",
                            )
                        }
                        for f in summary["failed"]
                    ],
                    "evidence": [
                        f"{f['step']} rc={f['rc']}({f.get('exit_code', '')}) {f.get('tail', '')}"[
                            :200
                        ]
                        for f in summary["failed"]
                    ],
                },
            )
        except Exception as e:  # noqa: BLE001  告警失败不得改变退出码
            print(f"[refresh] WARN 告警发送失败（不影响退出码）: {type(e).__name__}: {e}")
    return 0 if not summary["failed"] else 2


def main(argv=None) -> int:
    """编排入口：单实例锁 → 运行台账 → 主链 → 归档（阶段 0/1，2026-09-18）。"""
    # Windows 控制台/重定向下默认 GBK：状态标记含 ✗ 等非 GBK 字符 → UnicodeEncodeError
    # （2026-09-17 已记录该教训：长跑脚本须纯 ASCII + 强置 PYTHONIOENCODING/UTF-8）。
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="生产五源全量刷新（抓取→clean→时效→reconcile→recall→gates）"
    )
    ap.add_argument("--no-scrape", action="store_true", help="跳过网络抓取（仅清洗+全链）")
    ap.add_argument("--collect", default="all", help="抓取源: gov/mof/nfra/pbc/supp 逗号分隔或 all")
    ap.add_argument(
        "--supp-batch",
        default="",
        help="supp 批量摄取 backlog JSON（可选；F-O05：本地补全文件显式入链）",
    )
    ap.add_argument(
        "--stop-on-error", action="store_true", help="任一步 rc!=0 即中止（默认继续并汇总）"
    )
    # ---- v2 §3.13.3（P2-6）：与 `cli.py run` 同语义的四个开关 ----
    ap.add_argument(
        "--resume",
        action="store_true",
        help="从上次失败/未执行步骤续跑（跳过判据与水位同源：仅跳过 rc=0 且水位无 stale 的步骤）",
    )
    ap.add_argument(
        "--from", dest="from_step", default="", help="从指定步骤名开始（见 --list-steps）"
    )
    ap.add_argument("--only", default="", help="只执行指定步骤名（逗号分隔；前缀匹配）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的 argv 列表，不执行")
    ap.add_argument(
        "--json-logs",
        action="store_true",
        help="结构化日志（JSON lines；等价 REG_ORCH_JSON_LOGS=1）",
    )
    ap.add_argument("--list-steps", action="store_true", help="列出步骤名（执行顺序）后退出")
    args = ap.parse_args(argv)

    global _DRY_RUN, _FROM_STEP, _ONLY_STEPS
    _DRY_RUN = bool(args.dry_run)
    _ONLY_STEPS = tuple(s.strip() for s in (args.only or "").split(",") if s.strip())
    _FROM_STEP = (args.from_step or "").strip()
    if getattr(args, "list_steps", False):
        for i, s in enumerate(STEP_ORDER, 1):
            print(f"{i:>2}. {s}" + ("   （动态：每源一步）" if "{src}" in s else ""))
        return ExitCode.OK
    if args.json_logs:
        try:
            from std_lib.common_lib.logging import setup_cli_logging  # noqa: PLC0415

            setup_cli_logging("run_production_refresh", json_logs=True)
        except Exception as e:  # noqa: BLE001  日志设施不可用不得阻断主链
            print(f"[refresh] WARN 日志初始化失败（退回 stdout）: {type(e).__name__}")
    if args.resume:
        plan = _resume_plan()
        _RESUME_SKIP.update(plan.get("steps") or [])
        print(
            f"[refresh] --resume：跳过集 {len(_RESUME_SKIP)} 步"
            f"（{plan.get('from_run') or plan.get('reason')}）"
        )

    # ---- 单实例锁（阶段 0 止血，2026-09-18）----
    # 此前只有各 collector 自带 ProcessLock，**编排本体无锁** → 调度器抖动/人工重入会让
    # 两条链同时改写 cleaned / 归属表 / published（无锁临界区）。max_age 取 48h：
    # 采集阶段本身可达 30 小时（zhengceku 全量），超龄抢占阈值须大于该量级。
    from std_lib.common_lib.fs_lock import ProcessLock  # noqa: PLC0415

    lock = ProcessLock(
        os.path.join(ROOT, "data", "run_production_refresh.lock"), max_age_sec=48 * 3600
    )
    if not lock.acquire():
        print("[refresh] 已有实例在运行（锁被占用）——本次退出以避免并发改写数据面")
        print(f"[refresh] 锁文件：{lock.lock_path}（陈旧锁可删除或等待 PID 退出）")
        return ExitCode.ENV

    # ---- 运行台账（治理库为**旁路观测设施**：建库/登记失败一律降级为告警，不得阻断主链）----
    gs = None
    run_id = ""
    try:
        from std_lib.common_lib import governance_store as _gs  # noqa: PLC0415

        _gs.init_db()  # 幂等；治理库仅放元数据/水位/审计（方案 §4.1）
        run_id = _gs.run_start(
            ["python", "tools/run_production_refresh.py"]
            + (list(argv) if argv is not None else sys.argv[1:]),
            note="生产刷新链",
        )
        gs = _gs
        print(f"[refresh] run_id={run_id}（治理库：{gs.db_path()}）")
    except Exception as e:  # noqa: BLE001
        print(
            f"[refresh] WARN 治理库不可用（本次全部水位登记降级为 no-op）: {type(e).__name__}: {e}"
        )

    try:
        rc = _run_chain(args)
    except BaseException as e:  # noqa: BLE001  主链异常仍要落 run_log 并释放锁
        rc = 1
        print(f"[refresh] 主链异常终止: {type(e).__name__}: {e}")
    finally:
        lock.release()

    if gs is not None and run_id:
        try:
            # X4（P1-5）：run_log.note 带**语义化**退出码（原为裸数字，2 的四义无法区分）；
            # 逐步骤的 exit_code / stderr_tail_len 见汇总 JSON（路径随本次输出打印）。
            gs.run_finish(run_id, rc == 0, note=f"rc={rc}({_exit_semantic(rc)})")
        except Exception as e:  # noqa: BLE001
            print(f"[refresh] WARN 运行台账收尾失败: {type(e).__name__}: {e}")
    return rc


if __name__ == "__main__":
    # v2 §3.13.7（P2-6）：本文件保留为**库**（其 `main()` 被 `cli.py run` 调用）；
    # 直调路径保留一个版本用于回滚/排障，但一律提示改用唯一入口。
    print(
        "[refresh] DEPRECATED：请改用 `python cli.py run [同参数]`"
        "（唯一执行入口，v2 §3.13）；本直调路径保留一个版本后移除。",
        flush=True,
    )
    raise SystemExit(main())
