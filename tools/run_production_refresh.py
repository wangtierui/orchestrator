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
from dataclasses import dataclass, replace
from typing import Final

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
import paths
from config.enums import FAIL_SEMANTICS, SOURCE_ORDER, STEP_KINDS, failure_action
from config.exitcodes import ExitCode

SOURCES = list(SOURCE_ORDER)
# 各源采集命令（全量语义）。supp 无网站增量 → 由 clean 刷新即可。
COLLECT_CMD = {
    # —— R-C 子源拆分（2026-10-08）——
    # 原 gov 是**单条步骤**内含两个子源（xzfgk 行政法规库 / zhengceku 国务院政策文件库·部门文件），
    # 但二者在**体量、耗时结构、增量语义**上完全不同：
    #   · xzfgk   约 600 条；已有"整页无新增即停" ⇒ 通常秒级~分钟级；
    #   · zhengceku 629 页 / 1.7 万条；原实现 `for i in range(n_pages)` **恒扫全部页**
    #     ⇒ 周度空转 20+ 分钟（实测单轮 collect:gov 2394.9s，其中绝大部分是"已知页"翻页）。
    # 故拆为**独立步骤**，各自 argv / 超时 / 步骤日志 / 采集统计 / 失败隔离（互不拖累），
    # 且均可**单独调用与排程**（`--collect gov_xzfgk` / `--collect gov_zhengceku`）⇒ 单独维护与扩展。
    # **整体流程一致性**的落点：两者写**同一主库** `gov_laws.jsonl`（`--resume` 合并、detail_url 去重、
    #   新优先）⇒ 下游 clean/apply/classify/… **零改动**；并用 `--env-source/--env-category`
    #   固定信封为 gov（否则"最后一个子源"会覆盖主库信封的 source/category 标识）。
    # `--max-seconds`（**软预算**，N-190）＝ 硬超时 − **收尾余量**（载入主库 + 合并 + 1GB 原子写，
    # 实测 ≈3~4 分钟）⇒ 到达预算即"停止取新详情并正常落盘退出"（rc=0），而不是被硬超时**强杀**
    # （强杀会让未走完的产物落盘并被下游采用 —— N-152 的教训）。
    "gov_xzfgk": [
        PY, os.path.join(COLLECTORS, "gov_collector.py"),
        "--source", "xzfgk", "--max-seconds", "1500",           # 硬 1800 − 300
        "--env-source", "gov", "--env-category", "行政法规+部门文件",
    ],
    "gov_zhengceku": [
        PY, os.path.join(COLLECTORS, "gov_collector.py"),
        "--source", "zhengceku", "--max-seconds", "5100",       # 硬 5400 − 300
        "--env-source", "gov", "--env-category", "行政法规+部门文件",
    ],
    # 历史存量补全（**月度独立排程**）：不早停、扫完 629 页，补齐"在主库但尚无正文"的历史条目。
    # 原设计意图（持续补全历史存量）保留，但与周度"追新"**分离** ⇒ 代价显式化、各自计时。
    "gov_zhengceku_backfill": [
        PY, os.path.join(COLLECTORS, "gov_collector.py"),
        "--source", "zhengceku", "--backfill", "--max-seconds", "21000",   # 硬 21600 − 600
        "--env-source", "gov", "--env-category", "行政法规+部门文件",
    ],
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
# 采集**别名**（R-C，2026-10-08）：`--collect gov`（以及 `all`）展开为两个子源步骤 ——
# 使既有排程 `incremental_weekly` 的 argv（`gov,mof,pbc`）与人工命令**逐字不变**，
# 而步骤粒度已按子源拆分（步骤名 `collect:gov_xzfgk` / `collect:gov_zhengceku`）。
COLLECT_ALIAS = {"gov": ["gov_xzfgk", "gov_zhengceku"]}
# 采集步骤超时（秒）：**按实测分档**，替代原"一律 7200s"（一刀切会让小源失败也等 2 小时才报）。
COLLECT_TIMEOUT = {
    "gov_xzfgk": 1800,                 # 约 600 条 + 详情；实测秒级~分钟级
    "gov_zhengceku": 5400,             # 增量：早停后仅顶部数页 + 新增详情（N-190）
    "gov_zhengceku_backfill": 21600,   # 补全：须扫全 629 页（月度、离线时段）
    "nfra": 7200,
    "nfra-weekly": 7200,
    # 批 55/F2：实测 2026-10-10 `collect:mof` elapsed=7200.3s **恰好用尽上限被强杀**；
    # mof 为「列表 + 详情 + 附件」三段抓取且附件主机限流/退避重（429/500 退避至 60s、502 连续 8 次熔断）
    # ⇒ 单独放宽至 4h（其余源保持 7200s）；并已对齐 gov 的 legacy 跳过策略从源头降耗。
    "mof": 14400,
    "pbc": 3600,
}
RAW_JSON = {
    # S-A（N-206，2026-10-09）：gov 主库**改为 JSONL**（`gov_laws.jsonl`，首行 `_meta` 信封）。
    # 动因：`.json` 单数组解析 1GB 主库实测峰值 **5.24~6.13GB**（≈6× 体积）；JSONL 读写均可**流式**
    # ⇒ 峰值与语料体积解耦（`load_raw_records` 与 `MasterView` 均已支持双格式，迁移期不中断）。
    "gov": "gov_laws.jsonl",
    "mof": "mof_laws.jsonl",
    "nfra": "nfra_regulations.jsonl",
    "pbc": "pbc_laws.jsonl",
    "supp": "supplementary_regulations.jsonl",
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
    # N-46（2026-09-27）：报告类产物**此前无任何水位声明**——`reports:build` 只接
    # `build_overview_report.py`（全景/主题分类报告），`build_theme_report.py`（T0–T10
    # 主题报告 ×11）**从未接线** → 自 2026-09-08 起滞后 19 天且无水门禁可发现。
    # 本条令"报告是否随底座刷新"成为可校验项：inputs 取两类报告的真实读取面。
    "reports:classifier": {
        "produced_by": (
            "regulatory_classifier.scripts.report_builders.build_overview_report"
            "+build_theme_report"
        ),
        "stage": "4.5",
        "inputs": ["classify:products", "rfn_attr", "rfn_theme"],
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
    # W1-3（2026-10-13）：判据 R4「`produces` ∈ GOV_ARTIFACTS」暴露的**两处血缘缺口**——
    # 下列产物真实存在却从未登记，故「相关性与时效汇总」无血缘可查（旁路产物同样应可追溯）：
    #   · `relevance:views`——`filter_clean_relevance.py` 落
    #     `modules/regulatory_classifier/relevance/<src>_relevance_<stamp>.jsonl` + 同名摘要
    #     （批 63 入链为旁路步骤，但产物此前不在声明面）；
    #   · `timeliness:ledger`——`consolidate_timeliness.py` 落
    #     `时效性标注结果清单_全量_*.jsonl`（apply:{src} 的实际输入）。
    # 均为**观察型**（不声明 inputs）：它们不被下游 `inputs` 引用，登记只解决「可追溯/可判龄」。
    "relevance:views": {
        "produced_by": "regulatory_classifier.scripts.filter_clean_relevance",
        "stage": "2.1",
        "inputs": [],
    },
    "timeliness:ledger": {
        "produced_by": "regulatory_scrapers.timeliness_review.consolidate_timeliness",
        "stage": "2.2",
        "inputs": ["timeliness:state"],
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
    # `wiki:sync`（2026-10-13 W0-1 收尾 D-1）：llm_wiki 源同步的**基线水位**——版本 = 双底座
    # 发布件清单的联合版本，是 `triggers.yaml` 的 `publish_manifest_changed` 条件的比对基线
    # （缺此登记 → 条件「无基线 → 恒满足」→ 每轮全量重同步）。**刻意不声明 inputs**：其
    # 「陈旧」由触发条件判定（发布件变 → 条件满足 → 重跑），若声明 published:* 为依赖，
    # 则「发布件已更新但本轮条件未满足而 SKIP」会被 dependency_edges 误判为 stale → 假阳性
    # FAIL。观察型语义（只记版本，不进依赖图）。
    "wiki:sync": {
        "produced_by": "tools.sync_wiki_sources",
        "stage": "6.7",
        "inputs": [],
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


def _expand(keys, src: str = "") -> list[str]:
    """把 `{src}` 占位展开为**当前产物的源**（如 `clauses:supp` 的 inputs `cleaned:{src}`
    → `cleaned:supp`）；`src` 缺省（无源产物）才回退五源展开。

    N-45（P9，2026-09-26）：此前**一律五源展开**，使 `clauses:supp` 错误依赖全部 5 个
    cleaned（`cleaned:gov/mof/nfra/pbc/supp`）——任一源单独重跑（如 clean:nfra）即让
    `clauses:supp` 的水位依赖版本失配、误报 stale。正确语义是 clauses:{src} 只依赖
    同源 cleaned:{src}。
    """
    out: list[str] = []
    for k in keys or ():
        if "{src}" in k:
            if src:
                out.append(k.replace("{src}", src))
            else:
                out.extend(k.replace("{src}", s) for s in SOURCES)
        else:
            out.append(k)
    return out


def _clean_idx():
    """clean_index 单例（唯一入口）；不可用返回 None（调用方降级）。"""
    try:
        if SCRAPERS not in sys.path:
            sys.path.insert(0, SCRAPERS)
        from clean_index import get_clean_index

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


def _classifier_reports() -> list[str]:
    """报告类产物代表集：`docs/reports/` 下的 全景/主题分类/T0–T10 主题报告（13 份 md）。

    N-46（2026-09-27）：与 `_detail_tables()` 同风格——取目录内交付级 md 作为版本代表集
    （体量小、覆盖两类报告），避免对全部底座 JSON 逐一哈希。
    """
    d = os.path.join(CLASSIFIER, "docs", "reports")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".md")]


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
        from std_lib.common_lib import governance_store as gs

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
        _src = artifact_key.split(":")[-1] if ":" in artifact_key else ""
        for dep in _expand(spec.get("inputs"), _src):
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
        from std_lib.common_lib import governance_store as gs

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
    rec: dict = {
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
        # N-152（2026-09-30）：步骤**时间窗**（epoch 秒）。为何需要：`clean:gov` 超时被强杀后
        #   产物仍落盘、被下游采用而门禁无从判断 —— 有了时间窗，门禁即可判定
        #   "某失败步骤的产物是否正是由该步产生（mtime 落在窗内）"，即**危险组合**。
        "started_at": 0.0,
        "ended_at": 0.0,
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
                "started_at": round(t0, 3),
                "ended_at": round(time.time(), 3),
            }
        )
        _record_step(rec)
        print(f"\n[step:{step}] SKIP（{skip_reason}）", flush=True)
        return rec
    print(f"\n[step:{step}] {' '.join(rec['cmd'])}", flush=True)
    # N-189（2026-10-08）：**步骤日志落盘**（原 `capture_output=True` ⇒ 长步骤在运行中**完全不可观测**）。
    #   本批实测：`collect:gov`（zhengceku 629 页列表窗口）跑了 12+ 分钟，链日志里只有**一行步骤名**，
    #   既无法判断"在推进"还是"卡死"，也无法按需 tail（这是运维盲区，不是风格偏好）。
    # 改法：stdout/stderr **重定向到文件** ——
    #   · 运行中可 `Get-Content -Wait <log>` 实时观察（并可事后审计完整输出）；
    #   · 台账字段（`tail`/`stderr_tail`/两处长度）**语义不变**，只是改从文件尾部读取；
    #   · N-151「超时也要保留部分输出」因此**天然成立**（文件已在盘上，不再依赖异常携带）。
    log_dir = os.path.join(ROOT, "reports", "_tmp", "step_logs")
    os.makedirs(log_dir, exist_ok=True)
    _safe = re.sub(r"[^0-9A-Za-z_.-]", "_", step)
    out_path = os.path.join(log_dir, f"{_safe}_{int(t0)}.out.log")
    err_path = os.path.join(log_dir, f"{_safe}_{int(t0)}.err.log")

    def _tail(path: str, n: int) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().strip().splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"  # 子脚本 ✓/✗ 输出避免 Windows gbk 崩溃
        with (
            open(out_path, "w", encoding="utf-8", newline="\n") as _fo,
            open(err_path, "w", encoding="utf-8", newline="\n") as _fe,
        ):
            # 批 58：**隐藏窗口 + 让路优先级**（子进程及其后代继承 BELOW_NORMAL）＋ **强制无缓冲**。
            # 前者防闪窗抢焦点；后者修"日志假死"（此前子进程 stdout 重定向到文件时块缓冲 ⇒ 长时间不刷新，
            # 被误判为"采集停滞" ✗）。让路可用 `RCO_YIELD_TO_USER=0` 关闭。
            env = dict(env or {})
            env.setdefault("PYTHONUNBUFFERED", "1")
            from std_lib.scraper_std.proc import child_kwargs as _child_kwargs
            r = subprocess.run(argv, cwd=cwd, stdout=_fo, stderr=_fe, timeout=timeout, env=env,
                               **_child_kwargs())
        rec["rc"] = r.returncode
        rec["exit_code"] = _exit_semantic(r.returncode)
        rec["tail"] = _tail(out_path, 3)
        rec["stderr_tail"] = _tail(err_path, 2)
        rec["stderr_tail_len"] = _size(err_path)
        rec["stdout_len"] = _size(out_path)
    except subprocess.TimeoutExpired:
        # N-151（2026-09-30）**超时也要采集部分输出**。为何：`clean:gov` 超时那次 `stdout_len=0`
        # → 子进程明明有进度打印（`[gov] 原始数据：…`），却因父进程未从异常里取回而**全丢**，
        # 致使"超时不可诊断"（只能看到"timeout"三个字）。
        # N-189 起输出**始终在盘上** ⇒ 这里直接读文件尾部即可（比依赖异常更稳）。
        rec["exit_code"] = "TIMEOUT"
        rec["tail"] = _tail(out_path, 5) or "timeout（子进程无已捕获输出）"
        rec["stderr_tail"] = _tail(err_path, 3) or f"timeout {timeout}s"
        rec["stderr_tail_len"] = _size(err_path)
        rec["stdout_len"] = _size(out_path)  # 披露真实输出体量（原恒为 0）
    except Exception as e:  # noqa: BLE001
        rec["exit_code"] = f"EXC:{type(e).__name__}"
        rec["stderr_tail"] = f"{type(e).__name__}: {e}"
        rec["stderr_tail_len"] = len(rec["stderr_tail"])
    # N-189：完整步骤日志路径（可 tail / 可审计；`reports/_tmp` 已在门禁排除集内）
    rec["log_path"] = os.path.relpath(out_path, ROOT)
    rec["err_log_path"] = os.path.relpath(err_path, ROOT)
    rec["elapsed_s"] = round(time.time() - t0, 1)
    # N-152：填时间窗（门禁据此判定"失败步骤的产物是否由该步产生"）
    rec["started_at"] = round(t0, 3)
    rec["ended_at"] = round(t0 + rec["elapsed_s"], 3)
    if rec["tail"]:
        print("    " + "\n    ".join(rec["tail"].splitlines()), flush=True)
    print(
        f"    → rc={rec['rc']}({rec['exit_code']}) elapsed={rec['elapsed_s']}s"
        f" stderr={rec['stderr_tail_len']}B",
        flush=True,
    )
    _record_step(rec)
    return rec


def _run_conditional(step: str, tid: str) -> dict:
    """链内**条件步骤**（N-53 修复，2026-09-27）：把 `config/triggers.yaml` 的触发项作为
    `STEP_ORDER` 的一步执行——**决策与执行全部复用 `common_lib.triggers`**
    （该文件是条件与步骤序列的唯一事实源），本函数只负责"入链"：接 `_skip_reason`
    （选择/续跑/试跑）与 `_record_step`（run_step 台账）的既有语义。

    动机：`--triggers` 是**可选标志**且 `config/schedule.yaml` 的 refresh 任务未传参
    → 触发项声明完备却**从未执行**（`internal_update` 长期有未索引原件、processed 停在
    09-20 即其证据）。按 v2「数据处理节点归入全流程链路」，此处在链内**按判据**执行：
    条件不满足 → SKIP（原因入台账），满足 → 跑该触发项的全部 steps。
    """
    global _FROM_HIT
    t0 = time.time()
    rec: dict = {
        "step": step,
        "cmd": f"triggers:{tid}",
        "rc": -1,
        "elapsed_s": 0,
        "tail": "",
        "exit_code": "",
        "stderr_tail": "",
        "stderr_tail_len": 0,
        "stdout_len": 0,
        "skipped": False,
        "note": "",
    }
    skip_reason = _skip_reason(step)
    if skip_reason:
        rec.update({
            "rc": 0,
            "exit_code": "SKIP",
            "skipped": True,
            "note": skip_reason,
            "elapsed_s": round(time.time() - t0, 1),
        })
        _record_step(rec)
        print(f"\n[step:{step}] SKIP（{skip_reason}）", flush=True)
        return rec
    print(f"\n[step:{step}] triggers:{tid}", flush=True)
    try:
        from std_lib.common_lib import triggers as trg

        # 批 61：**注入上下文变量**（`vault` 唯一供给点 ⇒ `{vault}` 不再留字面量 ✗）；并消费 `resume` 字段：
        # 失败时打印**可直接执行的续跑命令**（W-AK：原为"纸面字段"）。
        try:
            res = trg.run_trigger(tid, {"argv": list(sys.argv), "vars": trg.ctx_vars()})
        except trg.UnresolvedPlaceholder as e:
            # 批 61（W-AJ'）：占位符缺值（如未设 `REG_ORCH_VAULT`）⇒ **本条件步骤跳过并写明原因**，
            # 绝不把字面量 `{vault}` 传给脚本（旧实现会造出字面目录 ✗）。
            print(f"[step:{step}] SKIP（占位符缺值：{e}；请设 REG_ORCH_VAULT 或 config/vault.yaml 的 vault_dir）",
                  flush=True)
            return {"step": step, "rc": 0, "skipped": True, "reason": "占位符缺值: %s" % e}   # 由调用点 append ✓
        if res.get("status") in ("failed", "partial"):
            _rp = trg.resume_path(tid)
            if _rp:
                print(f"[step:{step}] 续跑命令：{PY} {_rp} --py {PY}", flush=True)
    except Exception as e:  # noqa: BLE001  触发链异常不拖垮主链（但须显式记录）
        res = {"status": "failed", "steps": [], "note": f"{type(e).__name__}: {e}"}
    status = res.get("status", "")
    if status == "skipped":
        rec.update({
            "rc": 0,
            "exit_code": "SKIP",
            "skipped": True,
            "note": f"条件未满足：{res.get('note', '')}",
        })
        print(f"    → SKIP（{res.get('note', '')}）", flush=True)
    elif status == "ran":
        rec.update({"rc": 0, "exit_code": "OK"})
        tails = [str(s.get("tail") or "") for s in (res.get("steps") or []) if s.get("tail")]
        rec["tail"] = "\n".join("\n".join(t.splitlines()[-2:]) for t in tails)[-800:]
        rec["stdout_len"] = len(rec["tail"])
        print(f"    → rc=0（{len(res.get('steps') or [])} 步）", flush=True)
    else:  # failed / unknown
        bad: dict = next((s for s in (res.get("steps") or []) if s.get("rc") != 0), {})
        rc = int(bad.get("rc") or 1)
        tail = str(bad.get("tail") or res.get("note") or "")
        rec.update({
            "rc": rc,
            "exit_code": _exit_semantic(rc),
            "tail": tail[-800:],
            "stderr_tail": tail[-400:],
            "stderr_tail_len": len(tail),
            "note": f"触发项 {tid} 失败（on_fail={res.get('on_fail', '')}）",
        })
        print(f"    → rc={rc} 失败（{res.get('note', '')}）", flush=True)
    rec["elapsed_s"] = round(time.time() - t0, 1)
    _record_step(rec)
    return rec


# --------------------------------------------------------------------------- #
# 步骤选择 / 续跑 / 试跑（v2 §3.13.3，P2-6）
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 步骤声明面（W1，2026-10-13）——`STEP_LAYERS` 为**语义唯一事实源**
#
# 分层动因（v2 §3.13.3 D-12/D-13/D-6/D-7）：原 `STEP_ORDER` 是**平铺字符串元组**，
# 步骤的「用途 / 超时 / 触发器 / 产物 / 失败语义」散落三处（本文件注释、`gen_flow_map.PURPOSE`、
# `STEP_FAILURE`），导致三处漂移且 README §8 渲染靠人工同步。本块把语义收敛为**声明**：
#   · `StepSpec` —— 步骤语义（受控 kind/fail_semantics 校验在构造时，不合规即导入失败）；
#   · `LayerSpec` —— 层语义（层的默认 kind/fail_semantics + 层间 note）；
#   · `LAYER_EXEC_PLAN` —— **执行顺序的单一声明处**（层可被拆成多段插入：见下注）；
#   · `STEP_ORDER` —— **派生兼容视图**（`--from`/`--only`/`--list-steps`/门禁判据 R 语义不变）。
#
# 顺序事实注（与方案 §3.2 的差异，已实证校正）：方案示例 `LAYER_EXEC_ORDER` 把 L2 整层排在
# 条件步骤 `timeliness:verify` 之前，但现行链序是 **verify 必须先于 consolidate**（verify 产出
# 台账 → consolidate 汇总 → apply 回写；数据依赖，非同层批处理）。故此处用**分段计划**
# （同一层可出现在多个段）精确复刻现行 31 步顺序，等价性由 `tests/test_chain_equivalence.py`
# 对 golden 夹具逐字节比对。
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StepSpec:
    """步骤语义声明（README §8 / MCP 编排 / 门禁判据 R 全部派生自此）。

    name          : 步骤名；`{src}` 为五源动态模板（口径与旧 `STEP_ORDER` 一致）
    kind          : main（主链）/ side（旁路治理）/ disclosure（只读披露）/ conditional（条件触发）
                    / gate（链尾门禁）/ enhancement（增强层，可缺失且降级可观测）
    fail_semantics: abort（失败即断）/ warn（告警继续，**不污染链 rc**）/ rc0（恒 rc=0）/
                    skip（条件不满足即跳过）
    purpose       : 作用一句话（README §8「作用」列；旧 `gen_flow_map.PURPOSE` 的替代事实源）
    timeout       : 固定超时（秒）；None = 调用点动态计算（clean 按 raw 体量 / collect 按
                    `COLLECT_TIMEOUT` 配置 / 条件步骤按 triggers.yaml）
    trigger_id    : kind=conditional 专用：`config/triggers.yaml` 触发项 id
    depends_on    : 步骤名或产物键（供水位/血缘与判据 R4 拓扑校验）
    produces      : 该步产生的产物键（`GOV_ARTIFACTS` 键族）
    degrade_note  : 降级/失败语义的自然语言声明（README §8「降级/失败语义」列；旧
                    `STEP_FAILURE` 的替代事实源）
    """

    name: str
    kind: str
    fail_semantics: str
    purpose: str
    timeout: int | None = None
    layer: str = ""          # 所属层键（W1-3 起显式打戳：MCP 资源/确认台桑基图都需层归属）
    trigger_id: str = ""
    depends_on: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    degrade_note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in STEP_KINDS:
            raise ValueError(f"{self.name}: kind={self.kind!r} 非受控值 {sorted(STEP_KINDS)}")
        if self.fail_semantics not in FAIL_SEMANTICS:
            raise ValueError(
                f"{self.name}: fail_semantics={self.fail_semantics!r} 非受控值 {sorted(FAIL_SEMANTICS)}"
            )
        if self.kind == "conditional" and not self.trigger_id:
            raise ValueError(f"{self.name}: 条件步骤必须声明 trigger_id")
        if self.kind != "conditional" and self.trigger_id:
            raise ValueError(f"{self.name}: 非条件步骤不得声明 trigger_id（trigger_id={self.trigger_id!r}）")


@dataclass(frozen=True)
class LayerSpec:
    """层声明：层的默认语义可被步骤覆盖；`note` 记录该层的编排要点。"""

    kind: str
    fail_semantics: str
    steps: tuple[StepSpec, ...]
    depends_on: tuple[str, ...] = ()
    note: str = ""


STEP_LAYERS: Final[dict[str, LayerSpec]] = {
    # ---- L1 采集（main）----
    "L1_ingest": LayerSpec(
        kind="main",
        fail_semantics="abort",
        steps=(
            StepSpec("collect:nfra_weekly", "main", "abort", "nfra 周报采集（按周触发）",
                     None,  # 按 COLLECT_TIMEOUT['nfra-weekly'] 动态取
                     degrade_note="未到周度触发 → SKIP（rc=0）；超时按 COLLECT_TIMEOUT 配置"),
            StepSpec("collect:{src}", "main", "abort",
                     "各源原始采集（gov 拆 xzfgk/zhengceku 两个子源步骤；mof/nfra/pbc/supp）",
                     None,  # 按 COLLECT_TIMEOUT[src] 动态取
                     degrade_note="失败即停；--no-scrape → SKIP（rc=0）；--resume 可从该步续跑"),
            StepSpec("supp:ingest_batch", "main", "abort", "补充库批量摄取（backlog 驱动）", 1800,
                     degrade_note="backlog 为空 → SKIP（rc=0）"),
        ),
    ),
    # ---- L2 加工（main；relevance 为旁路 side —— 批 63 入链，D-7 语义矛盾修正）----
    "L2_transform": LayerSpec(
        kind="main",
        fail_semantics="abort",
        steps=(
            StepSpec("clean:{src}", "main", "abort", "原始 → 清洗（6 态抽取 + 校验隔离）",
                     None,  # 调用点按 raw 体量动态计算：min(max(1800, MB×2), 21600)
                     produces=("cleaned:{src}", "clauses:{src}"),
                     degrade_note="**超时按源体量配置**（N-150；N-188 重标定 0.24~0.41 s/MB）；"
                                  "当日无 raw 变化 → SKIP（rc=0）"),
            StepSpec("relevance:{src}", "side", "warn",
                     "清洗产物监管相关性边界筛选（BOUNDARY 边界案例 → worklist 待办）", 900,
                     produces=("relevance:views",),
                     degrade_note="**旁路分析件**：单源失败 WARN 不阻断主链；--stop-on-error 时中断"),
            StepSpec("timeliness:consolidate", "main", "abort", "时效核验结果汇总", 600,
                     produces=("timeliness:ledger",),
                     degrade_note="无核验输入 → 空汇总（rc=0）"),
            StepSpec("apply:{src}", "main", "abort", "时效核验结果回写清洗产物", 600,
                     produces=("cleaned:{src}",),
                     degrade_note="无核验结果可回写 → SKIP（rc=0）"),
        ),
        note="条件步骤 timeliness:verify 插在 clean/relevance 与 consolidate 之间（数据依赖："
             "verify 产出台账 → consolidate 汇总 → apply 回写）",
    ),
    # ---- L3 知识（main）----
    "L3_knowledge": LayerSpec(
        kind="main",
        fail_semantics="abort",
        steps=(
            StepSpec("classify:all", "main", "abort", "主题分类（_t*_base / _t*_final 派生）", 5400,
                     produces=("classify:products",),
                     degrade_note="断点幂等：输入未变自动跳过"),
            StepSpec("relations:gen", "main", "abort",
                     "关系抽取（依据/废止，含条款级定位：窗口优先 + 跨度退路）", 1800,
                     produces=("relations_index",),
                     degrade_note="失败即停；必须在 cleaned 写入后、下游消费者（base:publish）之前"),
            StepSpec("reconcile", "main", "abort", "清洗漂移对账（clean_drift）", 1200,
                     produces=("reconcile:drift",),
                     degrade_note="C2 漂移待人工 → worklist 待办，不阻断主链"),
            StepSpec("recall", "main", "abort", "召回复核与四门禁（clean/validity/contract/schema）",
                     3600, degrade_note="四门禁不过即 FAIL，留人工"),
        ),
    ),
    # ---- L4 内部制度（main）----
    "L4_internal": LayerSpec(
        kind="main",
        fail_semantics="abort",
        steps=(
            StepSpec("internal:merged", "main", "abort",
                     "内部制度合并视图（供条款对照与检索）", 1800,
                     produces=("merged_view",),
                     degrade_note="必须在 inbox:drop / internal:update（索引与原件同步）之后"),
        ),
    ),
    # ---- L5 交付（main）----
    "L5_delivery": LayerSpec(
        kind="main",
        fail_semantics="abort",
        steps=(
            StepSpec("reports:build", "main", "abort", "总览报告生成", 1800),
            StepSpec("reports:theme", "main", "abort", "主题报告生成（T0–T10 × 11）", 1800,
                     depends_on=("classify:products",)),
            StepSpec("draft:clause", "main", "abort", "条款对照素材（drafter 视图）", 3600,
                     depends_on=("merged_view",),
                     degrade_note="N-52：此前无任何调度导致滞后，故入链并置于 merged_view 之后"),
            StepSpec("base:publish", "main", "abort", "底座发布（发布清单 publish_manifest）", 1800,
                     produces=("published:external", "published:internal"),
                     degrade_note="发布清单是 wiki_sync 条件触发（publish_manifest_changed）的观察对象 —— "
                                  "可达性由门禁 T4 守护"),
            StepSpec("analysis:gen", "main", "abort",
                     "分析交付物（docs/reports，含 17 项 manifest sha）", 600,
                     produces=("analysis:manifest",)),
        ),
    ),
    # ---- L6 治理（side：失败告警不阻断主链）----
    "L6_governance": LayerSpec(
        kind="side",
        fail_semantics="warn",
        steps=(
            StepSpec("governance:artifacts", "side", "warn", "原件注册（治理库 artifact 表）", 3600,
                     degrade_note="N-63：此前**从未入链**（仅 docstring 自述）→ 原件 sha256/路径集"
                                  "长期不刷新；治理库不可用为合法降级"),
            StepSpec("governance:sync", "side", "warn",
                     "治理库同步（run_log / gate_result / relation 等投影）", 900,
                     degrade_note="N-64：投影后移到 internal:update **之后**（否则投影落后一轮）；"
                                  "库/文件摘要不一致 → 计入失败步骤（分叉判定）"),
            StepSpec("watch:baseline", "side", "warn", "观测基线快照（source diff --record）", 300),
        ),
        note="旁路观测设施：失败仅告警，**不阻断主链**（治理库不可用为合法降级）",
    ),
    # ---- L7 只读披露（恒 rc=0）----
    "L7_disclosure": LayerSpec(
        kind="disclosure",
        fail_semantics="rc0",
        steps=(
            StepSpec("retention:plan", "disclosure", "rc0",
                     "数据生命周期只读披露（dry-run + 台账）", 900,
                     degrade_note="恒 rc=0（无待归档是合法状态）；`--apply` 才是人工闸门"),
            StepSpec("quarantine:triage", "disclosure", "rc0",
                     "清洗隔离件分类与处置登记（零回写 → worklist 待办）", 600,
                     degrade_note="恒 rc=0（零回写保证：不修复数据，只分类登记）"),
            StepSpec("semantic:preflight", "disclosure", "rc0",
                     "P1 语义增强启用前置披露（五道闸逐项）", 300,
                     degrade_note="恒 rc=0；未启用是合法状态（真正的启用判定用 semantic_tools "
                                  "--preflight 的 rc）"),
            StepSpec("pg:health", "disclosure", "rc0",
                     "向量存储只读健康检查（pgvector / sqlite_vec 降级链披露）", 300,
                     degrade_note="恒 rc=0；后端不可用为合法状态"),
        ),
    ),
    # ---- L8 增强层（零硬依赖，可缺失且降级可观测）----
    "L8_enhancement": LayerSpec(
        kind="enhancement",
        fail_semantics="rc0",
        steps=(
            StepSpec("semantic:assist", "enhancement", "rc0",
                     "P1 语义增强入链调用点（嵌入 → 主题辅助裁定；只产分析视图）", 1800,
                     degrade_note="未启用（usage_policy.enabled=false）或嵌入链不可用 → 显式 rc=0；"
                                  "**绝不写事实源**"),
            StepSpec("docreader:extract", "enhancement", "rc0",
                     "docreader 入链调用点（版面解析 + 段落级定位；只产分析视图）", 1800,
                     degrade_note="不可用/自检不过 → 显式 SKIP（rc=0），回退既有 6 态抽取；"
                                  "不替换抽取事实源（替换须先度量）"),
        ),
        note="增强层可缺失、降级可观测、绝不阻断（N-177/N-180 入链背景）",
    ),
    # ---- L9 条件同步（判据事实源 = config/triggers.yaml）----
    "L9_sync": LayerSpec(
        kind="conditional",
        fail_semantics="skip",
        steps=(
            StepSpec("timeliness:verify", "conditional", "skip",
                     "时效核验（北大法宝；Node 侧 MCP）", None,
                     trigger_id="timeliness_verify",
                     degrade_note="无 token/工具缺失 → verification_state=unavailable（**不误标**）；"
                                  "配额耗尽自动停（断点续跑）；条件未触发 → SKIP（rc=0）"),
            StepSpec("inbox:drop", "conditional", "skip",
                     "收件箱投放（人工投递的制度文件入库）", None,
                     trigger_id="inbox_drop",
                     degrade_note="投放区无新文件 → SKIP（rc=0）；产出 index/align/processed → "
                                  "必须在 internal:merged 之前"),
            StepSpec("internal:update", "conditional", "skip",
                     "内部制度索引更新（原件同步）", None,
                     trigger_id="internal_update",
                     degrade_note="原件库无未索引文件 → SKIP（rc=0）"),
            StepSpec("wiki:sync", "conditional", "skip",
                     "llm_wiki 源同步（发布件变化时执行）", None,
                     trigger_id="wiki_sync",
                     degrade_note="发布件未变化 / vault 不可达 → SKIP（rc=0）；on_fail=skip_and_warn；"
                                  "观察对象可达性由门禁 T4 守护（D-1 修复）"),
        ),
        note="条件判据唯一事实源 = config/triggers.yaml；四步按数据依赖分散插入不同层段",
    ),
    # ---- L10 链尾门禁（阻断）----
    "L10_gate": LayerSpec(
        kind="gate",
        fail_semantics="abort",
        steps=(
            StepSpec("gates", "gate", "abort", "全部门禁（阻断；置于链尾以终态产物为准）", 1800,
                     degrade_note="失败即停并触发 notify(gate_fail)；P3-5 顺序解耦（原 gates 在链首）"),
        ),
    ),
}

# 执行顺序计划：`(层键, 步骤名序列)` 有序段。**同一层可出现在多段**（条件步骤按数据依赖
# 插入、L2 被 verify 切分、L6 的 watch:baseline 在交付之后）。
LAYER_EXEC_PLAN: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("L1_ingest", ("collect:nfra_weekly", "collect:{src}", "supp:ingest_batch")),
    ("L2_transform", ("clean:{src}", "relevance:{src}")),
    ("L9_sync", ("timeliness:verify",)),
    ("L2_transform", ("timeliness:consolidate", "apply:{src}")),
    ("L3_knowledge", ("classify:all", "relations:gen")),
    ("L8_enhancement", ("semantic:assist", "docreader:extract")),
    ("L3_knowledge", ("reconcile", "recall")),
    ("L9_sync", ("inbox:drop", "internal:update")),
    ("L6_governance", ("governance:artifacts", "governance:sync")),
    ("L4_internal", ("internal:merged",)),
    ("L5_delivery", ("reports:build", "reports:theme", "draft:clause", "base:publish",
                     "analysis:gen")),
    ("L6_governance", ("watch:baseline",)),
    ("L7_disclosure", ("retention:plan", "quarantine:triage", "semantic:preflight", "pg:health")),
    ("L9_sync", ("wiki:sync",)),
    ("L10_gate", ("gates",)),
)


def step_specs() -> tuple[StepSpec, ...]:
    """全链步骤声明（**执行序**，每步带 `layer` 归属；派生自 STEP_LAYERS + LAYER_EXEC_PLAN）。"""
    return _specs_in_exec_order()


def _specs_in_exec_order() -> tuple[StepSpec, ...]:
    """全量步骤（**执行序**，由 `LAYER_EXEC_PLAN` 从 `STEP_LAYERS` 派生）。

    每步带 `layer` 戳（所属层键）——MCP 资源 / 确认台桑基图 / E-3 作业归属断言都需要
    「这一步属于哪一层」，而声明面把步骤挂在 LayerSpec 下、执行序由计划分段决定，
    故层归属必须在**派生时**盖章，不能靠调用方反查。
    """
    out: list[StepSpec] = []
    for layer_key, names in LAYER_EXEC_PLAN:
        layer = STEP_LAYERS[layer_key]
        by_name = {s.name: s for s in layer.steps}
        for n in names:
            spec = by_name[n]  # KeyError = 计划与声明不一致 → 导入即失败（不静默）
            out.append(spec if spec.layer == layer_key else replace(spec, layer=layer_key))
    return tuple(out)


def step_by_name() -> dict[str, StepSpec]:
    """步骤名 → StepSpec（派生视图；W1 起语义查询统一走此）。"""
    return {s.name: s for s in step_specs()}


def step_layer_of(name: str) -> str:
    """步骤 → 所属层键（派生；README 分层渲染与 E-3 作业归属断言共用）。"""
    for layer_key, names in LAYER_EXEC_PLAN:
        if name in names:
            return layer_key
    raise KeyError(name)


# 兼容视图（派生）：**现行 `STEP_ORDER` 语义逐项保留** —— `--from`/`--only`/`--list-steps`
# 与门禁判据 R 全部按此消费（类型仍是 tuple[str, ...]，调用方零改动）。
STEP_ORDER: Final[tuple[str, ...]] = tuple(s.name for s in step_specs())
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
        from std_lib.common_lib import governance_store as gs

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
        from std_lib.common_lib import governance_store as gs
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


# W0-3（2026-10-13，D-2）：raw 记录数**流式双格式计数**。
# 原 `_raw_size()` 用 `json.load()` 全量解析——批 47/49 起五源主库均为 **JSONL**（首行
# `_meta` 信封 + 一行一记录），对 JSONL 必然抛 `JSONDecodeError: Extra data` → 落 except
# → `records=None` ⇒ 运行汇总的 raw 记录数自 JSONL 化起**静默失效**（实测复现）。
# 且即便对遗留 JSON 数组，458MB 主库全量载入也与「流式化」目标相悖。
# 现按**首字节嗅探**分流：`[` → 遗留数组（raw_decode 增量计数）；`{` → JSONL（逐行计数，
# 跳过 `_meta` 信封行）。两种格式都不把文件载入内存。
def _count_jsonl(path: str) -> int:
    """JSONL 记录数（流式；跳过空行与首行 `_meta` 信封）。"""
    n = 0
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            s = line.strip()
            if not s:
                continue
            if i == 0 and s.startswith("{") and '"_meta"' in s:
                continue  # 信封行（_meta）不计入记录数
            n += 1
    return n


def _count_json_array(path: str) -> int:
    """遗留 JSON 数组元素数（**流式**：raw_decode 增量消化，不整文件载入）。"""
    dec = json.JSONDecoder()
    count = 0
    with open(path, encoding="utf-8") as fh:
        buf = ""
        eof = False
        while not eof:
            chunk = fh.read(1 << 20)
            if not chunk:
                eof = True
            buf += chunk
            while True:
                s = buf.lstrip()
                buf = s
                if not buf:
                    break
                c = buf[0]
                if c == "]":
                    return count
                if c == ",":
                    buf = buf[1:]
                    continue
                if c != "{":
                    # 非对象元素（历史数据不应出现）→ 跳过该字符继续（防御性，不猜格式）
                    buf = buf[1:]
                    continue
                try:
                    _obj, end = dec.raw_decode(buf)
                except ValueError:
                    if eof:
                        # EOF 仍无法解析 → 截断/损坏：以已计数返回（调用方可见偏差）
                        return count
                    break  # 元素跨块：读更多
                count += 1
                buf = buf[end:]
    return count


def _raw_count(path: str) -> int | None:
    """raw 主库记录数（双格式流式）；文件缺失/不可读 → None（调用方披露，不伪造 0）。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192).lstrip()
        if not head:
            return 0
        if head[0:1] == b"[":
            return _count_json_array(path)
        return _count_jsonl(path)
    except (OSError, ValueError):
        return None


def _raw_size() -> dict:
    out = {}
    for src, fn in RAW_JSON.items():
        p = os.path.join(RAW_DIR, fn)
        out[src] = {
            "bytes": os.path.getsize(p) if os.path.exists(p) else 0,
            "records": _raw_count(p),
        }
    return out


def _rc_of(report: list[dict], step: str):
    """取指定步骤的 rc；步骤未执行（如 --stop-on-error 提前中止）→ None（调用方跳过登记）。"""
    for r in reversed(report):
        if r["step"] == step:
            return r["rc"]
    return None


def collect_targets(collect: str) -> list[str]:
    """`--collect` → **采集目标序列**（别名展开、去重保序；R-C，2026-10-08）。

    与 `SOURCES`（**事实源**口径：gov/mof/nfra/pbc/supp，供 clean/apply/… 使用）**解耦**：
      · `gov` → `["gov_xzfgk", "gov_zhengceku"]` —— 子源级步骤（各自 argv / 超时 / 步骤日志 /
        采集统计 / 失败隔离），可单独调用与排程；
      · `all` → 各事实源依次展开（gov 仍展开为两个子源）；
      · 其余键（mof/nfra/pbc/`nfra-weekly`/`gov_zhengceku_backfill`）原样透传；
      · 未登记键也透传，由调用方按 `COLLECT_CMD` 判定"无网络采集 → 跳过"（保持既有语义）。
    """
    _sel = [x.strip() for x in (collect or "").replace("，", ",").split(",") if x.strip()]
    pick = list(SOURCES) if collect == "all" else _sel
    out: list[str] = []
    for s in pick:
        for t in COLLECT_ALIAS.get(s, [s]):
            if t not in out:
                out.append(t)
    return out


def _spec_for(step_name: str):
    """步骤名 → StepSpec（**精确 → 模板 → 头键**三级回退）。

    W1-4：矩阵消费的是**实跑实例名**（`relevance:gov` / `collect:gov_xzfgk`），而声明面用
    模板与头键（`relevance:{src}` / `collect:{src}`）——只做精确匹配会查不到声明、误按
    `main/abort` 处理（旁路失败被当成链失败，正是本次要修的 D-7 形态）。
    """
    idx = step_by_name()
    if step_name in idx:
        return idx[step_name]
    head, sep, _tail = step_name.partition(":")
    if sep:
        tpl = f"{head}:{{src}}"
        if tpl in idx:
            return idx[tpl]
        if head in idx:
            return idx[head]
    for name, spec in idx.items():
        if name.split(":")[0] == head:
            return spec
    return None


def _apply_failure_matrix(report: list[dict]) -> None:
    """按**失败传播矩阵**归一化每步 rc（W1-4，v2 §3.3.3；矩阵 SSOT = `config.enums.FAILURE_MATRIX`）。

    动因（D-7）：原实现用「`rc != 0` 即失败」一刀切 ⇒ 旁路步骤（`relevance:{src}`，
    fail_semantics=warn）失败会把整条链标成失败（rc=2）并触发 `failed_step` 告警；
    只读披露/增强层后端不可用（合法状态）同理。现按声明语义分流：
      · `force_rc0`    → 原始 rc 存 `raw_rc`，对外一律 rc=0（后端未就绪是合法状态）；
      · `warn_continue`→ 保留 `raw_rc`，标 `degraded=True`（台账/汇总/告警可见，不阻断）；
      · `stop_chain`   → 保留非零 rc（计入链失败）。
    """
    for r in report:
        name = str(r.get("step") or "")
        spec = _spec_for(name)
        kind = getattr(spec, "kind", "main")
        fs = getattr(spec, "fail_semantics", "abort")
        action = failure_action(kind, fs)
        raw = int(r.get("rc") or 0)
        r["kind"] = kind
        r["action"] = action
        if r.get("skipped"):
            r["rc"] = 0  # 跳过不是失败（条件未触发/未选中）
            continue
        if raw == 0:
            continue
        if action == "force_rc0":
            r["raw_rc"] = raw
            r["rc"] = 0
            r["forced_rc0"] = True
        elif action == "warn_continue":
            r["raw_rc"] = raw
            r["degraded"] = True


def _stop_requested(args, entry: dict | None) -> bool:
    """`--stop-on-error` 的**统一**判定（W1-4：此前只有三个循环各自判断，单步调用点漏判）。

    仅 `stop_chain` 类失败才触发中止；`warn_continue`/`force_rc0` 不中止（与矩阵一致）。
    `action` 优先取矩阵归一化后的值；未归一化时按声明现查（矩阵 SSOT 单一）。
    """
    if not getattr(args, "stop_on_error", False) or not entry:
        return False
    if int(entry.get("rc") or 0) == 0:
        return False
    action = entry.get("action")
    if not action:
        spec = _spec_for(str(entry.get("step") or ""))
        action = failure_action(
            getattr(spec, "kind", "main"), getattr(spec, "fail_semantics", "abort")
        )
    return action == "stop_chain"


def _run_chain(args) -> int:
    report: list[dict] = []
    t_start = time.time()
    # ---- 阶段 0：抓取 ----
    if not args.no_scrape:
        # R-C（2026-10-08）：采集目标序列（子源级步骤）由 `collect_targets` 统一派生。
        _sel = [x.strip() for x in args.collect.replace("，", ",").split(",") if x.strip()]
        targets = collect_targets(args.collect)
        # F-O04（2026-09-12）：nfra 周度增量链（nfra_weekly.py，原零消费）——周刷链已含
        # 列表顶部窗口刷新 + 详情续跑 + 离线重建，替代全量 collector，避免重复抓取。
        if "nfra-weekly" in _sel:
            report.append(
                _run(
                    "collect:nfra_weekly",
                    [PY, os.path.join(COLLECTORS, "nfra_weekly.py")],
                    timeout=COLLECT_TIMEOUT.get("nfra-weekly", 7200),
                )
            )
            targets = [t for t in targets if t != "nfra"]
        for src in targets:
            cmd = COLLECT_CMD.get(src)
            if not cmd:
                print(f"[collect] {src} 无网络采集（本地摄取/清洗刷新）→ 跳过")
                continue
            if src == "mof":
                # A-14（2026-09-12）：改 shlex.split（原 .split() 遇引号/中文逗号即拆错）。
                cmd = cmd + shlex.split(os.environ.get("MOF_COLLECT_ARGS", ""))
            argv = cmd + [OUT_FLAG.get(src, "--out-dir"), RAW_DIR]
            print(f"[collect] {src} argv={argv}", flush=True)
            report.append(_run(f"collect:{src}", argv, timeout=COLLECT_TIMEOUT.get(src, 7200)))
            if _stop_requested(args, report[-1]):
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
    # N-150（2026-09-29，**N-188 于 2026-10-08 重标定**）**超时按源体量配置**（原为固定 `timeout=1800`）。
    # 背景：真实运行暴露 `clean:gov` **必然超时** —— 原固定 1800s 使**最大的源永远跑不完**（端到端阻断）；
    #   且超时被强杀时产物可能已落盘、被 `apply`/`classify` 下游采用而门禁 rc=0（见 N-152 的时间窗判据）。
    # ⚠️ **为何必须重标定**：N-150 当时的 `15 s/MB` 是**在被缺陷污染的数据上标定的** ——
    #   彼时 `clean:nfra` 实测 13.4 s/MB、`clean:gov` 撞满 4.3h，其根因是
    #   `ocr_correction.JiebaDict.check_and_fix` 的 **O(tokens×vocab)** 全表模糊匹配被新装的
    #   jieba **激活**（N-182；同一份数据在该缺陷休眠时仅 **0.14~0.19 s/MB**）。
    #   ⇒ 15 s/MB 把"**缺陷耗时**"误当"**体量成本**"，系数虚高约 **60×**，副作用是：
    #   **真实的挂死要等 6h 才被发现**（告警太晚）。
    # 重标定依据（N-182 修复后的**生产实测**，2026-10-05/06/07 调度链）：
    #   `clean:gov` **246 / 259 / 290s** ÷ 1035MB = **0.24~0.28 s/MB**；mof/nfra 为 0.29~0.41 s/MB。
    # 口径：`max(1800, 体量MB × 2)`，上限 21600s（6h）—— 对最大源留 **~7× 余量**，
    #   同时把"挂死"的**发现时间从 4.3h 压到 ~35 分钟**；体量取**实际 raw 文件大小**（缺失则退回下限）。
    _RAW_MB_PER_S = 2.0
    for src in SOURCES:
        try:
            _raw_mb = os.path.getsize(os.path.join(RAW_DIR, RAW_JSON[src])) / (1024 * 1024)
        except OSError:
            _raw_mb = 0.0
        _clean_timeout = int(min(max(1800.0, _raw_mb * _RAW_MB_PER_S), 21600.0))
        print(
            f"[clean] {src}: raw {_raw_mb:.0f}MB → 超时 {_clean_timeout}s"
            "（N-150 按体量配置；原固定 1800s）",
            flush=True,
        )
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
                timeout=_clean_timeout,
            )
        )
        if _stop_requested(args, report[-1]):
            break

    # ---- 阶段 1 水位：clauses（clean 尾部节点产物）----
    # 不声明 cleaned 上游：apply_timeliness 会在阶段 2 双轨回写 cleaned，
    # 届时阶段 1 的 cleaned 版本作废（已知顺序特性，见 GOV_ARTIFACTS 注释）。
    for src in SOURCES:
        _wm(f"clauses:{src}", rc=_rc_of(report, f"clean:{src}"), paths=[_clauses_jsonl(src)])

    # ---- 阶段 1.9：时效核验（← triggers.yaml:timeliness_verify，条件步骤）----
    # N-53：原由 `cli.py run --only 6.9` 驱动，而 `--only` 只认**步骤名**（实测 6.9
    # 使全部步骤 SKIP = 空跑）。入链后由 `days_since: 7` 条件自守（核验后 7 天内跳过）。
    report.append(_run_conditional("timeliness:verify", "timeliness_verify"))

    # 批 63（T4/W-AW）：接线 `filter_clean_relevance.py`（原为「零调用脚本」✗）⇒
    # 门禁 J7 的「产生方」**真正可达**；旁路分析件，失败不阻断主链。
    # W0-2（2026-10-13，D-3）：**包入 for src in SOURCES 循环**——原实现挂在 clean 循环
    # 之后、用**残留循环变量** `src`（循环退出时恒为最后一源 supp）⇒ 只跑 `relevance:supp`、
    # 其余四源的边界筛选从不执行（台账实证）。与 `clean:{src}` 同构：逐步 try/WARN
    # （旁路语义：单源失败不拖垮主链），`--stop-on-error` 时中断（与 clean 同语义）。
    for src in SOURCES:
        try:
            report.append(
                _run(
                    f"relevance:{src}",
                    [PY, os.path.join(ROOT, "modules", "regulatory_classifier", "scripts",
                                        "filter_clean_relevance.py"), "--source", src],
                    timeout=900,
                )
            )
            if _stop_requested(args, report[-1]):
                break
        except Exception as e:  # noqa: BLE001
            print(f"[refresh] WARN relevance:{src} 跳过: {type(e).__name__}: {e}")

    # ---- 阶段 2：时效回写（consolidate → apply 五源）----
    led = None
    r1 = _run(
        "timeliness:consolidate",
        [PY, os.path.join(REVIEW, "consolidate_timeliness.py"), "--use-state"],
        timeout=600,
    )
    report.append(r1)
    # 找 consolidate 最新全量清单
    import glob

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
        paths=[paths.relations_index()],
    )

    # ---- 阶段 2.7：P1 语义增强**入链**（`semantic:assist`，N-177）----
    # 动因（用户 2026-09-30 指出）：`bge_base_zh`/`text2vec`/`youtu_embedding` 的**权重与依赖早已就绪**
    #   （external/models 预置、preflight 五道闸全过），但主链**没有任何调用点** →
    #   即「模型就绪却未入链」；且 MODEL_BINDINGS 曾把它们绑在 `classify:all` 上，
    #   而该节点**并无嵌入调用点**（绑定指向不存在的调用点 = 纸面接线）。
    # 纪律：**非阻断** —— 增强层零硬依赖：未启用（usage_policy.enabled=false）或嵌入链不可用
    #   均为**合法状态**，由工具自身显式披露并 rc=0；是否启用由 `usage_policy` + `semantic:preflight` 决定。
    # 产物：`reports/semantic/semantic_assist_<date>.{json,md}`（**分析视图**，绝不写事实源）。
    report.append(
        _run(
            "semantic:assist",
            [
                PY,
                "-m",
                "tools.semantic_assist",
                "--source",
                "all",
                "--limit",
                os.environ.get("REG_ORCH_SEMANTIC_ASSIST_LIMIT", "200"),
            ],
            timeout=1800,
        )
    )

    # ---- 阶段 2.8：docreader 入链（`docreader:extract`，N-180）----
    # 动因：`weknora_docreader`（v2 P2-1）长期"未装/未接线"；本轮**源码级部署**完成后给出**真实调用点**
    #   —— 否则仍属纸面状态（本仓纪律：绑定必须指向真实调用点）。
    # 形态：**跨解释器子进程**调 venv 中的 docreader（无端口、无长驻、异常即非零退出）。
    # 纪律：**非阻断** —— 不可用/自检不过均为合法状态（工具显式披露且 rc=0）；
    #   产物为**分析视图**（`reports/docreader/`，含与既有 6 态抽取的增量度量），**不写事实源**。
    report.append(
        _run(
            "docreader:extract",
            [
                PY,
                "-m",
                "tools.docreader_extract",
                "--limit",
                os.environ.get("REG_ORCH_DOCREADER_LIMIT", "20"),
            ],
            timeout=1800,
        )
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

    # ---- 阶段 3.5：治理库元数据投影 —— **N-64（2026-09-27）已后移至 `internal:update` 之后** ----
    # 原位置纪律为"在链内最后一个 attr 写方（reconcile）之后，保证投影取到终态"；但阶段 2
    # 还投影 `document(internal)` 自 `internal_policy_index.json`，其生产者 `internal:update`
    # 在链中更靠后 → 原位置使 `internal:update` **真正执行的那一轮**投影到的是**上一轮索引**。
    # 现统一置于 `internal:update`（及其阶段 1 前置 `governance:artifacts`）之后，见下方。

    # ---- 阶段 4：recall ----
    report.append(
        _run(
            "recall",
            [PY, os.path.join(CLASSIFIER, "recall_audit", "run_retrieval_after_checks.py")],
            timeout=3600,
        )
    )

    # ---- 阶段 4.0/4.1：内部制度增量摄取（← triggers.yaml，条件步骤；N-53）----
    # 顺序按数据依赖：投放区投递（inbox_drop）→ 原件库增量摄取（internal_update：
    #   normalize/index/align/reocr）→ 两者都写 index/align/processed，必须在 merged 之前。
    # 原状态：二者声明的 stage（7.4/7.5）为旧口径，且 `--triggers` 未传 → 从未执行
    #   （证据：`unindexed_originals` 长期为真、processed 停在 09-20）。
    report.append(_run_conditional("inbox:drop", "inbox_drop"))
    report.append(_run_conditional("internal:update", "internal_update"))

    # ---- 阶段 3.5：治理库**双阶段**投影（N-63 / N-64，2026-09-27）----
    # 阶段 1 · 原件注册：`原件 → artifact 表（sha256[:16] → path_keys 集合 + inode + 字节数）`。
    #   原件保持原格式/原路径、不经处理器读盘，故**原件库更新即需重登记**；
    #   该工具自述"阶段 1"却**从未入链**（N-63 → 登记长期不随原件库变化刷新）。
    report.append(
        _run(
            "governance:artifacts",
            [PY, os.path.join(ROOT, "tools", "governance_register_artifacts.py")],
            timeout=3600,
        )
    )
    # 阶段 2 · 元数据四表投影 + 比对断言（**文件仍是事实源**，本步只写库；库/文件摘要不一致
    #   即"分叉"，计入失败步骤）。位置后移至 `internal:update` 之后 → 取**终态**内部索引（N-64）。
    report.append(
        _run(
            "governance:sync",
            [PY, os.path.join(ROOT, "tools", "governance_sync.py"), "--apply"],
            timeout=900,
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
    # ---- 阶段 4.6：reports:theme（T0–T10 主题报告 ×11；N-46 修复 2026-09-27）----
    # 此前 `build_theme_report.py` **从未接入编排**（阶段 4.5 只调 build_overview_report.py）
    # → `modules/regulatory_classifier/docs/reports/T{n}_主题报告.md` 自 2026-09-08 起
    # 19 天未更新，且因报告类产物无水位声明而无门禁可发现（链外遗漏，N-46）。
    report.append(
        _run(
            "reports:theme",
            [
                PY,
                os.path.join(CLASSIFIER, "scripts", "report_builders", "build_theme_report.py"),
            ],
            timeout=1800,
        )
    )
    # 阶段 4.6 水位：报告类产物族（rc 取两报告步骤中**先出现的失败**；均成功则 0）
    _rc_rep = _rc_of(report, "reports:build")
    _rc_theme = _rc_of(report, "reports:theme")
    _rc_reports = _rc_theme if _rc_theme not in (None, 0) else (_rc_rep or 0)
    _wm("reports:classifier", rc=_rc_reports, paths=_classifier_reports())

    # ---- 阶段 4.7：draft（条款对照素材；N-52 修复 2026-09-27）----
    # 此前 `cli.py draft`（build_draft_clause_view.py → drafter/data/draft_clause/）
    # **既不在 STEP_ORDER 也不在 triggers.yaml** → 自 2026-09-08 起滞后（真链外节点）。
    # 依赖 merged_view（阶段 4.2）+ processed（阶段 4.1），故排在此处。
    report.append(
        _run("draft:clause", [PY, os.path.join(ROOT, "cli.py"), "draft"], timeout=3600)
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

    # ---- 阶段 6.6：数据生命周期披露（retention dry-run；N-73b，2026-09-28）----
    # `tools/retention.py` 是 v2 §3.10（P2-3）的**数据生命周期节点**，此前仅人工执行 →
    # 备份/运行日志的超保留量条目**无链内可见性**（台账靠人记得跑）。
    # 改为链内**只读披露**：默认走 dry-run（**不动文件**），落 `reports/retention_<date>.json`
    # 台账（入库可审计）；台账自身已纳入 POLICY 自管（`retention_ledgers`，保留 30 份）。
    # 实际归档仍为人工确认后 `python tools/retention.py --apply`（破坏面保留人工闸门）。
    report.append(
        _run(
            "retention:plan",
            [PY, os.path.join(ROOT, "tools", "retention.py")],
            timeout=900,
        )
    )

    # ---- 阶段 6.6b：清洗隔离记录分类与登记（N-93，2026-09-28）----
    # `gates/gate_clean_schema` 明文：隔离记录属**数据治理**待办、**需人工清理源数据**、
    # 只披露不阻断 → 故此处**零回写**：只分类 + `governance_store.worklist_add` 登记
    # （kind=`clean_quarantine_triage`，处置走 `cli.py worklist resolve`）。
    # 与 `retention:plan` 同款纪律：链内**只读披露**，破坏面保留人工闸门。
    report.append(
        _run(
            "quarantine:triage",
            [PY, os.path.join(ROOT, "tools", "quarantine_triage.py")],
            timeout=600,
        )
    )

    # ---- 阶段 6.6b：P1 语义增强**启用前置披露**（N-114，2026-09-28）----
    # `--preflight-report`：五道闸（deps/offline/fp/baseline/license）逐项打印并**恒 rc=0**。
    # 为何用披露口径而非判定口径：P1 **未启用是合法状态**，若因"不可启用"使步骤 FAIL，
    # 会把"尚未启用"误报成"链路故障"。判定口径 (`--preflight`) 保留给人工/CI 当闸门。
    report.append(
        _run(
            "semantic:preflight",
            [PY, "-m", "std_lib.common_lib.semantic_tools", "--preflight-report"],
            timeout=300,
        )
    )

    # ---- 阶段 6.6c：向量存储**只读**健康检查（P2-2 接入，2026-09-29）----
    # `std_lib/common_lib/vector_store.py --health`：披露后端（pgvector / sqlite_vec / none）、
    # DSN 目标（**已脱敏**，口令不回显）、扩展版本、当前用户是否非超管、`vec` schema 是否可用。
    # 纪律与 `retention:plan` / `semantic:preflight` 同款：**只读披露、恒 rc=0、不动数据**
    # （后端不可用是**合法状态**，不得把披露误报成链路故障）。
    report.append(
        _run(
            "pg:health",
            [PY, "-m", "std_lib.common_lib.vector_store", "--health"],
            timeout=300,
        )
    )

    # ---- 阶段 6.7：llm_wiki 源同步（← triggers.yaml:wiki_sync，条件步骤；N-53）----
    # 依赖 published 清单（`publish_manifest_changed` 判据）；vault 不可达时跳过（on_fail:
    #   skip_and_warn）。原由 `cli.py run --only 21.5` 驱动 → 阶段号不匹配步骤名，空跑。
    _wiki_rec = _run_conditional("wiki:sync", "wiki_sync")
    report.append(_wiki_rec)
    # W0-1（2026-10-13，D-1 收尾）：同步**成功后**登记 `wiki:sync` 水位（版本 = 双清单联合版本，
    # 与 triggers 条件同口径）——它是 `publish_manifest_changed` 的基线来源；SKIP/失败不登记
    # （基线须停在「上次成功同步的版本」）。条件侧观察路径修复见 common_lib/triggers.py。
    if _wiki_rec.get("rc") == 0 and not _wiki_rec.get("skipped"):
        _wm("wiki:sync", paths=[_m_ext, _m_int])

    # ---- 阶段 6：gates（**链尾**，P3-5）----
    # 交付门禁应在**全部产物生成之后**运行：① 才覆盖本轮 6.8 的 `analysis:manifest`；
    # ② 与人工心智一致（"最后一道门"）；③ `gate_result` 仍记同一 run_id，无归档方式变化；
    # ④ 门禁失败时其**之前**的产物已落盘（与改动前一致：原顺序下 analysis:gen 也在门禁之后）。
    _gates_rec = _run("gates", [PY, os.path.join(ROOT, "cli.py"), "gates"], timeout=1800)
    report.append(_gates_rec)
    if _gates_rec.get("rc"):
        # 批 61：补齐 `notify.on` 声明面的**发送者**（声明 4 类，此前仅 failed_step 有产生方 ✗）
        try:
            from std_lib.common_lib import notify as _notify_g

            _notify_g.notify("gate_fail", "门禁未通过（链尾 gates rc=%s）" % _gates_rec.get("rc"),
                          {"step": "gates", "rc": _gates_rec.get("rc")})
        except Exception as e:  # noqa: BLE001  告警失败不得改变退出码
            print(f"[refresh] WARN 告警发送失败（不影响退出码）: {type(e).__name__}: {e}")

    summary: dict = {
        "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_s": round(time.time() - t_start, 1),
        "raw": _raw_size(),
        "steps": report,
        # 三桶分离（W1-4）：`failed`=stop_chain（计入链失败）；`degraded`=warn_continue
        # （可见不阻断）；`forced_rc0`=force_rc0（后端未就绪属合法状态）。
        "failed": [r for r in report if int(r.get("rc") or 0) != 0],
        "degraded": [r for r in report if r.get("degraded")],
        "forced_rc0": [r for r in report if r.get("forced_rc0")],
    }
    # W1-4：矩阵归一化必须在汇总/台账/告警**之前**（否则 degraded/forced 会被算成链失败）
    _apply_failure_matrix(report)

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
    if summary["degraded"]:
        print(f"[降级] 旁路步骤 {len(summary['degraded'])} 个（可见不阻断）:")
        for d in summary["degraded"]:
            print(f"  ~ {d['step']} raw_rc={d.get('raw_rc')}({d.get('exit_code', '')})")
    if summary["forced_rc0"]:
        print(f"[强制 rc=0] {len(summary['forced_rc0'])} 个（后端未就绪属合法状态）:")
        for d in summary["forced_rc0"]:
            print(f"  · {d['step']} raw_rc={d.get('raw_rc')}({d.get('exit_code', '')})")
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

    # N-152（2026-09-30）：**稳定运行台账**（固定路径，供门禁读取）。
    #   为何不能只靠上面的带时间戳汇总：① 门禁无从"找最新"；② `reports/_tmp` 会被轮转清理。
    #   事故背景：`clean:gov` 超时被杀，而**产物仍落盘并被 apply/classify 采用**，
    #   最终 `gates` 仍报"全部门禁通过" —— 失败被**无感吞掉**（端到端可审计性缺口）。
    #   本台账把"哪一步失败、何时开始/结束"固化下来，由 `gate_run_steps` 判定危险组合。
    try:
        _rs_dir = os.path.join(paths.DATA_DIR, "run_state")
        os.makedirs(_rs_dir, exist_ok=True)
        _rs_p = os.path.join(_rs_dir, "last_run_steps.json")
        with open(_rs_p, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
        print(f"[run_state] 稳定运行台账已更新: {_rs_p}")
    except Exception as _e:  # noqa: BLE001  台账落盘失败不得中断主链，但必须可见
        print(f"[run_state] WARN 运行台账落盘失败（门禁将无法判定失败步骤）: {type(_e).__name__}: {_e}")
    # W1-4：degraded（旁路 WARN / force_rc0）**独立告警**——「可见但不阻断」不等于「不告警」。
    # 旁路步骤长期失败若只进台账无人看，等于回到 D-1 式的静默；单列事件便于运营区分
    # 「链失败（failed_step）」与「降级运行（degraded_step）」。
    if summary["degraded"] or summary["forced_rc0"]:
        try:
            from std_lib.common_lib import notify as _notify

            _notify.notify(
                "degraded_step",
                f"生产刷新降级 {len(summary['degraded'])} 旁路 / "
                f"{len(summary['forced_rc0'])} 未就绪（不阻断链）",
                {
                    "summary_json": out_p,
                    "total_elapsed_s": summary["total_elapsed_s"],
                    "degraded": [
                        {"step": d["step"], "raw_rc": d.get("raw_rc"),
                         "kind": d.get("kind"), "action": d.get("action"),
                         "exit_code": d.get("exit_code"), "tail": d.get("tail", "")[:200]}
                        for d in summary["degraded"]
                    ],
                    "forced_rc0": [
                        {"step": d["step"], "raw_rc": d.get("raw_rc"),
                         "kind": d.get("kind"), "exit_code": d.get("exit_code")}
                        for d in summary["forced_rc0"]
                    ],
                    "evidence": [
                        f"{d['step']} raw_rc={d.get('raw_rc')}({d.get('exit_code', '')}) "
                        f"kind={d.get('kind')}/{d.get('action')} {d.get('tail', '')[:160]}"
                        for d in summary["degraded"] + summary["forced_rc0"]
                    ][:20],
                },
            )
        except Exception as e:  # noqa: BLE001  告警失败不得改变退出码
            print(f"[refresh] WARN 降级告警发送失败（不影响退出码）: {type(e).__name__}: {e}")

    # P2-6：失败 → 告警通道（v2 §3.13.6；无人值守下"失败无人知晓"是原设计的硬缺口）
    if summary["failed"]:
        try:
            from std_lib.common_lib import notify as _notify

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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
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
            from std_lib.common_lib.logging import setup_cli_logging

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
    from std_lib.common_lib.fs_lock import ProcessLock

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
        from std_lib.common_lib import governance_store as _gs

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
    # 批 61（W-AD）：**让路实装** —— 本进程切到 Windows 后台模式（CPU+I/O 双低优先级，子进程继承），
    # 使前台办公软件优先获得资源；置 `RCO_YIELD_TO_USER=0` 关闭（详见 proc.py 模块头）。
    import os as _os

    if _os.environ.get("RCO_YIELD_TO_USER", "1").strip() not in ("0", "false", "False"):
        try:
            from std_lib.scraper_std import proc as _proc

            _proc.set_self_background()
        except Exception:  # noqa: BLE001  让路失败不阻断
            pass
    # v2 §3.13.7（P2-6）：本文件保留为**库**（其 `main()` 被 `cli.py run` 调用）；
    # 直调路径保留一个版本用于回滚/排障，但一律提示改用唯一入口。
    print(
        "[refresh] DEPRECATED：请改用 `python cli.py run [同参数]`"
        "（唯一执行入口，v2 §3.13）；本直调路径保留一个版本后移除。",
        flush=True,
    )
    raise SystemExit(main())
