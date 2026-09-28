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
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import paths
from config.exitcodes import ExitCode

_TODAY = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

CLS_DATA = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")
INT_DATA = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data")
STATE_JSON = os.path.join(
    paths.MODULES_DIR, "regulatory_scrapers", "timeliness_review", "verification_state.json"
)
# N-75（2026-09-28）：**删除本地主题名副本 `CLS_MAP`**。它自称"近似名"、注释指向权威
# `THEME_MAP`，实测已与权威**漂移 9/11 项**（且 T2/T3 语义错位：本地 T2"偿付能力" vs 权威
# T2"产品与精算制度"；本地 T3"产品条款费率" vs 权威 T3"资本与偿付能力监管"），
# 且**全仓零引用**（纯死副本 → 只误导读者、无任何收益）。
# 主题名一律经 `interfaces.theme_api`（→ `rfn.THEME_MAP`）取用；禁止再建本地副本。


def _csv_len(p: str) -> int:
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def gather() -> dict:
    out: dict = {"generated_at": _TODAY, "python": sys.version.split()[0]}

    # 1) gates
    gates_dir = os.path.join(ROOT, "gates")
    g_files = sorted(
        f for f in os.listdir(gates_dir) if f.startswith("gate_") and f.endswith(".py")
    )
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
    dets = sorted(
        f for f in os.listdir(cd) if re.match(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$", f)
    )
    det_tot = sum(_csv_len(os.path.join(cd, f)) for f in dets)
    brid = os.path.join(cd, "rfn_clean_bridge.csv")
    out["classifier"] = {
        "attr_rows": attr_len,
        "theme_rows": theme_len,
        "base_total": base_tot,
        "final_total": final_tot,
        "per_theme": theme_rows,
        "detail_files": len(dets),
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
        if os.path.isdir(oreg)
        else 0,
        "processed_files": len(
            [f for f in os.listdir(proc) if os.path.isfile(os.path.join(proc, f))]
        )
        if os.path.isdir(proc)
        else 0,
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
    # 覆盖率基线（审查 P2-7，2026-09-12）：有 .coverage 数据则取 TOTAL%（"只升不降"回归闸）
    try:
        if os.path.exists(os.path.join(ROOT, ".coverage")):
            r = subprocess.run(
                [sys.executable, "-m", "coverage", "report", "--format=total"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            out["coverage_total"] = (
                ((r.stdout or "").strip().splitlines() or ["?"])[-1].strip()
                if r.returncode == 0
                else "?"
            )
        else:
            out["coverage_total"] = "（未采集；python -m coverage run -m pytest tests -q）"
    except Exception:  # noqa: BLE001
        out["coverage_total"] = "?"
    return out


def _read_count(p: str, key: str) -> int:
    if not os.path.exists(p):
        return ExitCode.OK
    try:
        x = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ExitCode.OK
    if isinstance(x, dict):
        if isinstance(x.get(key), int):
            return x[key]
        recs = x.get("records")
        return len(recs) if isinstance(recs, list) else 0
    return len(x) if isinstance(x, list) else 0


# --------------------------------------------------------------------------- #
# 语义质量基线（N-91 / 优化方案 v2 · P0-2「铸尺」）
# --------------------------------------------------------------------------- #
# 背景：本表原有区块量化的是"**体量**"（文件数/行数/门禁数），但**从未量化"语义质量"**——
# 分句/结构/主题/关系/关联五项语义增强因此**无验收依据**（不知现状多准 → 无法证明引入模型后变好）。
# 本区块把**既有事实源**聚合为可 diff 的质量基线：**不引入任何新依赖、不新建度量框架**
# （原方案 v1 提议新建度量框架；v2 评估指出应**增强本生成器**，故折叠于此，零新增脚本/零新增引导）。
RELATIONS_JSONL = os.path.join(CLS_DATA, "relations", "relations_index.jsonl")
THEME_CSV = os.path.join(CLS_DATA, "人身保险公司-主题归属表.csv")
RECALL_OUT = os.path.join(
    paths.MODULES_DIR, "regulatory_classifier", "recall_audit", "output"
)
CLEANED_DIR = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned")
# 「依据来源分层」：把人工裁定的**判定依据**归类，供评测集**剔除低置信样本**（口径固定、可复现）
_LOW_CONF_MARKERS = ("无正文", "registry自动登记")


def _basis_class(basis: str) -> str:
    b = (basis or "").strip()
    if not b:
        return "空"
    for m in _LOW_CONF_MARKERS:
        if m in b:
            return m
    if "精读" in b or "复核" in b:
        return "精读裁定"
    if "语义" in b:
        return "语义验证"
    if "上位法" in b:
        return "上位法锚点"
    return "其他"


def _q_theme_gold() -> dict:
    """主题判定**金标准**（人工裁定事实源）→ 样本分层与可评集。"""
    out: dict = {"file": os.path.relpath(THEME_CSV, ROOT), "exists": False}
    if not os.path.exists(THEME_CSV):
        return out
    out["exists"] = True
    dist: dict = {}
    basis: dict = {}
    evaluable = 0
    rows = list(csv.DictReader(open(THEME_CSV, encoding="utf-8-sig", newline="")))
    for r in rows:
        theme = (r.get("主题") or "").strip()
        cls = _basis_class(r.get("判定依据") or "")
        dist[theme] = dist.get(theme, 0) + 1
        basis[cls] = basis.get(cls, 0) + 1
        if cls not in _LOW_CONF_MARKERS:
            evaluable += 1
    out.update(
        {
            "total": len(rows),
            "themes": dict(sorted(dist.items())),
            "basis_class": dict(sorted(basis.items())),
            "evaluable": evaluable,
            "evaluable_ratio": round(evaluable / max(len(rows), 1), 4),
            "excluded_markers": list(_LOW_CONF_MARKERS),
        }
    )
    return out


def _q_clause_structure() -> dict:
    """条文结构基线（**复用既有** `clause_index.validate_schema`，不另写校验器）。"""
    try:
        from config.enums import SOURCE_ORDER
        from interfaces import clause_index_api as ci

        if not any(ci.latest_clause_path(s) for s in SOURCE_ORDER):
            return {"skipped": "无条文产物（未跑过 clause_index build）"}
        v = ci.validate_schema()
    except Exception as e:  # noqa: BLE001  基线生成：异常须可见但不得中断整表
        return {"error": f"{type(e).__name__}: {e}"}
    keep = (
        "files", "articles", "chapters", "structures", "law",
        "degraded", "fallback", "invalid", "warned",
        "article_structures", "item_nodes",
        "title_swallow", "tail_contam", "space_contam", "law_items",
        "parse_modes",
        # N-94（2026-09-28）：但书计数（度量先行）
        "proviso", "proviso_strict",
    )
    out: dict = {k: v.get(k) for k in keep if k in v}
    files = max(int(v.get("files") or 0), 1)
    # N-101（2026-09-28）**口径纠偏**：原报 `degraded_ratio`（非 law 模式占比）会被读成"84% 质量
    # 降级"，但 `parse_mode` 是**体裁**（notice/plain/law/bulletin/plan/empty）而非质量等级——
    # 实测 notice 55.0% + plain 29.2% + empty 0.2%，**非 law 占多数属正常**（多数监管文件不是
    # 条文式）。故改为报**体裁分布** + 三个真正的质量信号：`empty`（解析为空）/`fallback`（回退
    # 路径）/`invalid`（契约不合）。`degraded` 仍保留原值供追溯，但不再换算成"比例"以免误读。
    pm = v.get("parse_modes") or {}
    out["parse_modes"] = dict(sorted(pm.items()))
    out["empty_mode"] = int(pm.get("empty") or 0)
    out["fallback_ratio"] = round(int(v.get("fallback") or 0) / files, 4)
    out["empty_ratio"] = round(out["empty_mode"] / files, 4)
    out["consistent"] = bool(v.get("consistent"))
    return out


def _q_relations_locate_audit(rows: list[dict]) -> dict:
    """目标侧定位**正确性上界/下界**估计（N-106，2026-09-28）——**纯只读，不改任何产物**。

    问题：N-95/N-98 只测得**覆盖率**（窗口定位 237/4192 = 5.7%），但"命中的**对不对**"无从判断
    （缺金标）。N-98 新增的 `source_span`（引用跨度，引用自身文本）为此提供了**低成本锚点**：
    同一行用**同一函数**（`locate_dst_article`，不另写抽取逻辑）分别以**窗口**与**跨度**为输入，
    两者**冲突**即证明至少一方错 —— 无需人工标注即可给出量化边界：

      · `disagree`  —— 窗口与跨度给出**不同**条号 → **误定位下界**（确定存在错，且必有一方错）；
      · `win_only`  —— 仅窗口抽到（跨度无）→ "可能的错"上限增量（条号可能在引用**邻域**而非引用内）；
      · `span_only` —— 仅跨度抽到（窗口无）→ 窗口**漏定位**（弃权）证据；
      · `agree` / `none` —— 一致 / 两侧皆无。

    → **误定位下界 = disagree**；**上界 = disagree + win_only**。
    纪律：仅统计，**不参与**任何判定、不写产物、不改门禁（避免"用可疑信号反向改数据"）。
    """
    out: dict = {"rows": len(rows)}
    try:
        from std_lib.common_lib.clause_locator import (
            dst_article_candidates,
            locate_dst_article,
        )
        from std_lib.common_lib.relations import snippet_half_window
    except Exception as e:  # noqa: BLE001  审计失败不得中断基准
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    # 窗口的**半宽**：`_snippet` 取 `text[max(0,pos-h) : pos+h]` → 窗口内"引用起点"的下标
    # = `pos - max(0, pos-h)`（即 `h`；仅当 pos<h 时为 pos）。N-109 起**唯一派生**于 relations。
    _half = snippet_half_window()

    n_key = agree = disagree = win_only = span_only = none = 0
    # N-109：把"仅窗口"按**条号在窗口内的位置**归因（引用前文 / 引用之后 / 两处都有）
    win_only_before = win_only_after = win_only_both = win_only_unknown = 0
    # N-112：多半径互证分档（零人工）
    win_only_strong = win_only_weak = 0
    off_tight = off_mid = off_far = 0
    # D5：弱证据细分（竞争者距离）
    weak_nohit = weak_far_only = weak_rival1 = weak_rival_many = 0
    samples: list[dict] = []
    for r in rows:
        key = str(r.get("dst_ref") or r.get("dst_key") or "")
        if not key:
            continue
        n_key += 1
        win = str(r.get("dst_article") or "")
        span = locate_dst_article(str(r.get("source_span") or ""), key)
        if win and span:
            if win == span:
                agree += 1
            else:
                disagree += 1
                if len(samples) < 5:
                    samples.append(
                        {
                            "relation_id": r.get("relation_id"),
                            "window": win,
                            "span": span,
                            "dst_name": str(r.get("dst_name") or "")[:24],
                        }
                    )
        elif win:
            win_only += 1
            # N-109（2026-09-28）**位置归因**：唯一窗口命中的条号，落在**引用起点之前**还是之后？
            #   · 之前 → 属**源侧前文**（自指/污染嫌疑）→ 构成"误定位"的**可归因**部分；
            #   · 之后 → 属**引用本身或紧随后文** → 极可能是目标条款（合法）。
            # 这一步把"无法证伪的上界"拆成"可归因的可疑量 vs 合法量"，**零人工标注**。
            snip = str(r.get("source_snippet") or "")
            pos = int(r.get("source_offset") or -1)
            start_in_win = max(0, pos - max(0, pos - _half)) if pos >= 0 else 0
            # N-109：**与写入侧同口径** —— 判定候选位置时只看"引用之后"的切片（写入侧已剔除前文，
            # 诊断若仍用整窗会把"永不可能是 dst_article 的前文条号"计入归因 → 归因失真）。
            full_hits = dst_article_candidates(snip, key)
            hits = [p for p, no in full_hits if no == win and p >= start_in_win]
            before_hits = [p for p, no in full_hits if no == win and p < start_in_win]
            before = bool(before_hits)
            after = bool(hits)
            if before and after:
                win_only_both += 1
            elif before:
                win_only_before += 1
            elif after:
                win_only_after += 1
                # N-112（2026-09-28）**多半径互证**：这 418 行"未经跨度互证"，但可用**位置结构**
                # 内在证据分档（零人工）：
                #   · `hit_off` —— 命中条号距引用起点的字符距离（越小＝越紧邻引用名，越像目标条款）；
                #   · `rivals`  —— 该条号**之后**（更大距离处）是否还存在其他候选条号。
                #     若有 → 目标条号之所以"唯一"，是因为它**更近**、后被排除者在更远处；
                #     若无 → 全窗口内**只有它**一个候选 → 强证据。
                fwd = snip[start_in_win:]
                cand = dst_article_candidates(fwd, key)
                offs = sorted(p for p, no in cand if no == win)
                hit_off = offs[0] if offs else -1
                rivals = sum(1 for p, no in cand if no != win and p > hit_off)
                if hit_off >= 0 and rivals == 0 and hit_off <= 40:
                    win_only_strong += 1   # 紧邻引用名（≤40 字符）且全窗无竞争者
                else:
                    win_only_weak += 1
                # D5（2026-09-28）**弱者细分**：弱证据此前是一个黑箱。按**竞争者距离**拆开，
                # 使"该先看哪一批"有据可依：`rivals = 0` 只是位置偏远（条号离引用名远，
                # 但全窗独一份）→ 可疑度低；`rivals > 0` 则**存在更远的其他候选** → 可疑度高
                # （目标条号之所以被选中，仅因它比另一个候选更近）。
                if hit_off < 0:
                    weak_nohit += 1
                elif rivals == 0:
                    weak_far_only += 1
                elif rivals == 1:
                    weak_rival1 += 1
                else:
                    weak_rival_many += 1
                if hit_off <= 40:
                    off_tight += 1
                elif hit_off <= 80:
                    off_mid += 1
                else:
                    off_far += 1
        elif span:
            span_only += 1
        else:
            none += 1
    out.update(
        {
            "with_target_key": n_key,
            "agree": agree,
            "disagree": disagree,
            "win_only": win_only,
            "span_only": span_only,
            "none": none,
            "misalign_lower": disagree,
            "misalign_upper": disagree + win_only,
            "disagree_ratio": round(disagree / max(n_key, 1), 4),
            "misalign_upper_ratio": round((disagree + win_only) / max(n_key, 1), 4),
            "win_only_before": win_only_before,
            "win_only_after": win_only_after,
            "win_only_both": win_only_both,
            "win_only_unknown": win_only_unknown,
            "win_only_strong": win_only_strong,
            "win_only_weak": win_only_weak,
            "off_tight": off_tight,
            "off_mid": off_mid,
            "off_far": off_far,
            "weak_nohit": weak_nohit,
            "weak_far_only": weak_far_only,
            "weak_rival1": weak_rival1,
            "weak_rival_many": weak_rival_many,
            "samples": samples,
        }
    )
    return out


def _q_relations() -> dict:
    """关系抽取基线（含条款级定位覆盖率）。"""
    out: dict = {"file": os.path.relpath(RELATIONS_JSONL, ROOT), "exists": False}
    if not os.path.exists(RELATIONS_JSONL):
        return out
    out["exists"] = True
    n = src_ok = dst_ok = both = span_ok = 0
    ids: set = set()
    place: dict = {}
    rows: list[dict] = []   # N-106：留存行供定位正确性差异分析（4192 行，内存可忽略）
    with open(RELATIONS_JSONL, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            n += 1
            rows.append(r)
            ids.add(str(r.get("relation_id") or ""))
            s = bool((r.get("src_article_located") or "").strip())
            d = bool((r.get("dst_article") or "").strip())
            src_ok += s
            dst_ok += d
            both += s and d
            # N-98（2026-09-28）：引用跨度**披露字段**覆盖率（`source_span` 为纯增量字段，
            # 不参与定位；此处披露其覆盖，使"字段是否有值"可被机器发现而非靠人工抽查）。
            if (r.get("source_span") or "").strip():
                span_ok += 1
            k = (r.get("article_placement") or "").strip()
            place[k] = place.get(k, 0) + 1
    out.update(
        {
            "rows": n,
            "distinct_relation_id": len(ids),
            "id_unique": n == len(ids),
            "src_article_located": src_ok,
            "dst_article": dst_ok,
            "both": both,
            "src_ratio": round(src_ok / max(n, 1), 4),
            "dst_ratio": round(dst_ok / max(n, 1), 4),
            "source_span": span_ok,
            "span_ratio": round(span_ok / max(n, 1), 4),
            "placement": dict(sorted(place.items())),
            # N-106：以引用跨度（`source_span`）为**锚**估目标侧定位的**正确性边界**
            "locate_audit": _q_relations_locate_audit(rows),
        }
    )
    return out


def _q_recall() -> dict:
    """召回覆盖基线（**复用** `recall_audit` 既有产物，不重算）。"""
    out: dict = {"dir": os.path.relpath(RECALL_OUT, ROOT), "exists": os.path.isdir(RECALL_OUT)}
    sp = os.path.join(RECALL_OUT, "_stats.json")
    rp = os.path.join(RECALL_OUT, "重跑执行报告.json")
    if os.path.exists(sp):
        try:
            raw = json.load(open(sp, encoding="utf-8"))
            # 只收**标量**摘要：基线是"可 diff 的尺子"须小而稳定（明细仍在 recall_audit 自身产物里）
            if isinstance(raw, dict):
                out["stats_scalars"] = dict(
                    sorted(
                        {
                            k: v
                            for k, v in raw.items()
                            if isinstance(v, (int, float, str, bool)) or v is None
                        }.items()
                    )[:30]
                )
                out["stats_top_keys"] = sorted(raw)[:24]
            else:
                out["stats_type"] = type(raw).__name__
        except (OSError, ValueError) as e:
            out["stats_error"] = f"{type(e).__name__}: {e}"
    if os.path.exists(rp):
        try:
            d = json.load(open(rp, encoding="utf-8"))
            out["generated_at"] = d.get("generated_at")
            out["gates"] = {
                k: (v.get("passed") if isinstance(v, dict) else v)
                for k, v in (d.get("gates") or {}).items()
            }
        except (OSError, ValueError) as e:
            out["report_error"] = f"{type(e).__name__}: {e}"
    return out


def _q_cleaning() -> dict:
    """清洗体量基线（各源 cleaned / 隔离记录条数）。"""
    out: dict = {"dir": os.path.relpath(CLEANED_DIR, ROOT), "sources": {}}
    if not os.path.isdir(CLEANED_DIR):
        return out
    for fn in sorted(os.listdir(CLEANED_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        p = os.path.join(CLEANED_DIR, fn)
        n = sum(1 for ln in open(p, encoding="utf-8", errors="replace") if ln.strip())
        out["sources"][fn.replace("_quarantine.jsonl", "·隔离").replace(".jsonl", "")] = n
    return out


def gather_quality() -> dict:
    """→ 语义质量基线（供 `BENCHMARK.md` 新区块 + `reports/评测基线_<date>.json`）。"""
    return {
        "theme_gold": _q_theme_gold(),
        "clause_structure": _q_clause_structure(),
        "relations": _q_relations(),
        "recall": _q_recall(),
        "cleaning": _q_cleaning(),
        "semantic": _q_semantic_capability(),
    }


def _q_semantic_capability() -> dict:
    """P1 语义增强**能力披露**（N-102）：当前环境具备哪些工具 + 指纹。

    为什么放在基准里：v2 的"降级链"要求**回退可观测**。把能力状态随每轮全链刷进 `BENCHMARK.md`，
    即可回答"这一轮跑的是增强路径还是正则回退路径"——避免"增强已上线但从未生效"长期无人察觉。
    """
    try:
        from std_lib.common_lib import semantic_tools as st

        return {
            "summary": st.summary_line(),
            "fingerprint": st.fingerprint(),
            "probes": {
                n: {
                    "available": p["available"],
                    "version": p["version"],
                    "pipeline_ref": p["pipeline_ref"],
                }
                for n, p in st.probe_all().items()
            },
        }
    except Exception as e:  # noqa: BLE001  披露失败不得中断基准生成
        return {"error": f"{type(e).__name__}: {e}"}


def render_quality(q: dict) -> list:
    """质量基线 → Markdown 行（供 `render()` 追加为末章）。"""
    L: list = []
    L.append("## 6 语义质量基线（铸尺 / 优化方案 v2 · P0-2）\n")
    L.append(
        "> 口径：全部取自既有事实源（金标准表 / `clause_index.validate_schema` / "
        "`relations_index` / `recall_audit`），**不新增模型与依赖**。"
    )
    L.append("> 用途：任何语义增强（分句/结构/主题/关系/关联）**上线前后逐键比对**；任一指标劣化即回退。\n")

    t = q["theme_gold"]
    L.append("### 6.1 主题判定（金标准）")
    if not t.get("exists"):
        L.append("\n- ⚠️ 主题归属表缺失")
    else:
        L.append(
            f"\n- 样本总数 **{t['total']}**；可评样本（剔除 `{'/'.join(t['excluded_markers'])}`）"
            f"**{t['evaluable']}**（{t['evaluable_ratio']:.1%}）"
        )
        L.append(f"- 依据来源分层：`{t['basis_class']}`")
        L.append("")
        L.append("| 主题 | 条数 |")
        L.append("|---|---|")
        for k, v in t["themes"].items():
            L.append(f"| {k} | {v} |")

    c = q["clause_structure"]
    L.append("\n### 6.2 条文结构（既有 `validate_schema` 判据）")
    if "skipped" in c or "error" in c:
        L.append(f"\n- ⚠️ {c.get('skipped') or c.get('error')}")
    else:
        L.append(
            f"\n- 文件 **{c['files']}** / 条 **{c['articles']}** / 章 **{c['chapters']}**，"
            f"契约自检 `consistent={c['consistent']}`"
        )
        L.append(
            f"- 体裁分布（`parse_mode`）：`{c.get('parse_modes')}`"
            "　※ `degraded` 旧口径＝非 law 体裁计数，**非质量降级**（N-101）"
        )
        L.append(
            f"- 质量信号：`empty {c.get('empty_mode')}（{c.get('empty_ratio', 0):.2%}）` / "
            f"`fallback {c['fallback_ratio']:.2%}` / `invalid {c.get('invalid')}`"
        )
        L.append(
            f"- 结构语义指标（「曾被静默放过」的直接堵漏项）：`title_swallow={c.get('title_swallow')}` / "
            f"`tail_contam={c.get('tail_contam')}` / `space_contam={c.get('space_contam')}` / "
            f"`law_items={c.get('law_items')}`"
        )
        L.append(
            f"- 但书计数（N-94 度量先行，**只披露不判定**）：宽档 `proviso={c.get('proviso')}` 条 / "
            f"严档 `proviso_strict={c.get('proviso_strict')}` 条"
            "　※ 用途：为\"是否值得改解析器切分逻辑\"提供量级证据"
        )

    r = q["relations"]
    L.append("\n### 6.3 关系抽取（`relations_index.jsonl`）")
    if not r.get("exists"):
        L.append("\n- ⚠️ relations_index 缺失")
    else:
        L.append(
            f"\n- 行数 **{r['rows']}**；`relation_id` 唯一性 `{r['id_unique']}`"
            f"（distinct {r['distinct_relation_id']}）"
        )
        L.append(
            f"- 源侧条款定位 **{r['src_article_located']}/{r['rows']}**（{r['src_ratio']:.1%}）；"
            f"目标侧 **{r['dst_article']}**（{r['dst_ratio']:.1%}）；双侧 **{r['both']}**"
        )
        L.append(f"- 定位来源分布：`{r['placement']}`")
        L.append(
            f"- 引用跨度（N-98 新增 / **N-106 起参与定位**：窗口未唯一命中时的退路）：`source_span` "
            f"{r.get('source_span')}/{r.get('rows')}（{r.get('span_ratio', 0):.1%}）"
        )
        la = r.get("locate_audit") or {}
        if la.get("error"):
            L.append(f"- 定位正确性审计不可用：`{la['error']}`")
        elif la:
            L.append(
                "- **定位正确性边界**（N-106，以跨度为锚；有目标键 "
                f"{la.get('with_target_key')} 行）：一致 {la.get('agree')} ／ "
                f"**冲突 {la.get('disagree')}** ／ 仅窗口 {la.get('win_only')} ／ "
                f"仅跨度 {la.get('span_only')} ／ 皆无 {la.get('none')}"
            )
            L.append(
                "- **窗口命中位置归因**（N-109，写入侧已**剔除前文**）："
                f"引用**之前** {la.get('win_only_before')}（**应为 0**，非 0 即有源侧自指残留）／ "
                f"引用之后 {la.get('win_only_after')} ／ 两处皆有 {la.get('win_only_both')} ／ "
                f"定位不到出处 {la.get('win_only_unknown')}"
            )
            L.append(
                "- **互证分档**（N-112，零人工）：强证据（条号距引用名 ≤40 字符且全窗无竞争者）"
                f"{la.get('win_only_strong')} ／ 弱证据 {la.get('win_only_weak')}；"
                f"距离分布 ≤40 {la.get('off_tight')} ／ 40–80 {la.get('off_mid')} ／ >80 {la.get('off_far')}"
            )
            L.append(
                "  → W2 决议（**保持后向 100 字符**）：抽样证实 `>80` 行的 **88%** 为**长引用列表**"
                "（“依据《A》《B》《C》等，制定本法第X条”→ 条号必然偏远）→ 远距离**不是**可疑信号；"
                "故**不收窄**（会丢合法长引用命中）**不加宽**（无正确性增益）"
            )
            L.append(
                "- **唯一性不变式**（D5）：竞争者数 >0 的行 `"
                f"{la.get('weak_rival1', 0) + la.get('weak_rival_many', 0)}`"
                "（**应为 0**；>0 即「唯一命中才填」守卫被破坏 → 报警）；"
                f"弱证据细分（供「该先看哪批」）：全窗独一份仅**位置偏远** {la.get('weak_far_only')}"
                f"（低可疑）／定位不到出处 {la.get('weak_nohit')}"
            )
            L.append(
                "  → D5 结论：`rivals` **结构上恒为 0**（「唯一命中才填」⇒目标表内本就唯一）"
                "→ 该维度**无信息量**，故改判为**不变式校验**而非分档依据；"
                "弱证据的真实分档仍以**距离**（≤40/40–80/>80）为准"
            )
            L.append(
                f"  → **误定位下界 `{la.get('misalign_lower')}`"
                f"（{la.get('disagree_ratio', 0):.1%}）**、"
                f"上界 `{la.get('misalign_upper')}`"
                f"（{la.get('misalign_upper_ratio', 0):.1%}）"
                "　※ 下界＝窗口与跨度**冲突**（必有一方错）；上界额外计入「仅窗口」——"
                "但**前文剔除（N-109）后归因显示「引用之前」已为 0**，即「仅窗口」全部落在"
                "引用之后（合法位），故上界已**不宜读作「可能的错」**，宜读作"
                "「**未经跨度互证的量**」"
            )

    rc = q["recall"]
    L.append("\n### 6.4 召回覆盖（`recall_audit` 既有产物）")
    L.append(
        f"\n- 产物目录存在 `{rc.get('exists')}`；报告生成于 {rc.get('generated_at') or '—'}"
    )
    if rc.get("gates"):
        L.append(f"- 四门禁：`{rc['gates']}`")

    cl = q["cleaning"]
    L.append("\n### 6.5 清洗体量")
    if cl.get("sources"):
        L.append("")
        L.append("| 产物 | 条数 |")
        L.append("|---|---|")
        for k, v in cl["sources"].items():
            L.append(f"| {k} | {v} |")
    else:
        L.append("\n- ⚠️ cleaned 目录为空")
    L.append("")
    L.append(
        "**纪律**：主题判定新增任何分类器时，须在 §6.1 的**可评样本**上报告 P/R，"
        "并**对齐**既有 `判定依据` 的「排名 + margin」留痕格式。"
    )

    sm = q.get("semantic") or {}
    L.append("\n### 6.6 P1 语义增强能力（当前环境）")
    if "error" in sm:
        L.append(f"\n- ⚠️ 能力披露不可用：{sm['error']}")
    else:
        L.append(f"\n- {sm.get('summary', '')}")
        fp = sm.get("fingerprint") or {}
        L.append(f"- 指纹：可用 `{fp.get('available')}`；未装 `{len(fp.get('unavailable') or [])}` 项")
        L.append("")
        L.append("| 工具 | 状态 | 版本 | 对应方案项 |")
        L.append("|---|---|---|---|")
        for n, p in (sm.get("probes") or {}).items():
            L.append(
                f"| `{n}` | {'可用' if p['available'] else '未装'} | {p['version'] or '—'} | "
                f"{p['pipeline_ref']} |"
            )
        L.append("")
        L.append(
            "> 未装工具不阻断全链：调用方一律经 `std_lib.common_lib.semantic_tools` 探测后惰性导入，"
            "不可用时回退既有正则实现并**记录回退说明**（`fallback_notice()`）。清单："
            "`config/schema/semantic_tools.json`；启用：`pip install -e .[semantic]`。"
        )
    return L


def render(g: dict) -> str:
    L = []
    L.append("# BENCHMARK —— 交付基准登记（回归对照基线）\n")
    L.append(f"> 自动生成：tools/gen_benchmark.py @ {g['generated_at']} | python {g['python']}")
    L.append(
        "> 用途：数据重建/重构后重跑 `python tools/gen_benchmark.py` 刷新；数值漂移即回归信号。\n"
    )

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
    L.append(
        f"**覆盖率基线（只升不降）**：TOTAL {g.get('coverage_total', '?')}%"
        "（采自 `.coverage`；刷新：`python -m coverage run -m pytest tests -q`）\n"
    )

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
    L.append(
        f"| processed 处理文件（fulltext/main/json/md） | {g['internal']['processed_files']} |"
    )
    L.append(f"| 条文结构 _clauses.json | {g['internal']['clauses_json']} |")
    L.append(f"| 条文视图 _clauses.md | {g['internal']['clauses_md']} |")
    L.append(f"| merged_view 记录 | {g['internal']['merged_records']} |")
    L.append("")
    L.append("## 4 运行入口速查\n")
    L.append("| 命令 | 职责 |")
    L.append("|---|---|")
    L.append(f"| `python cli.py gates` | {len(g['gates'])} 道交付门禁（以 ALL_GATES 为准） |")
    L.append(
        "| `python cli.py classify --all --steps base,cluster,match,detail,upper,clause_graph` | 底座强序重建（R8 幂等断点） |"
    )
    L.append("| `python cli.py source list / add --id` | 源目录路由（R15） |")
    L.append("| `python cli.py internal index/align/merged` | 内部制度链路 |")
    L.append(
        "| `python cli.py timeliness verify --source all` | 时效核验三态（R13，需北大法宝 token） |"
    )
    L.append("| `clean\\run_clean_pipeline.py --project <源>` | 单源清洗 |")
    L.append("| `recall_audit\\run_retrieval_after_checks.py` | retrieval 四门禁编排（幂等） |")
    L.append("| `python tools/gen_benchmark.py` | 刷新本基准 |")
    L.append("")
    L.append("## 5 回归说明\n")
    L.append(
        "1. 归属表/时效数据变更后：重跑 `classify` 底座链 → `reconcile_clean_drift`（桥）→ gates → 刷新本表。"
    )
    L.append(
        "2. internal 源变后：`internal index`（backfill_clauses 幂等）→ `internal align` → merged 视图。"
    )
    L.append("3. 任一基线与上表不符且非预期升级 → 先查对应门禁 FAIL 输出，勿静默覆盖。")
    L.append("4. 本表只登记当前仓产物；历史一次性脚本（旧仓）不在此列。")
    L.append("")
    L.extend(render_quality(g.get("quality") or gather_quality()))
    return "\n".join(L) + "\n"


def main() -> int:
    g = gather()
    g["quality"] = gather_quality()
    text = render(g)
    out_p = os.path.join(ROOT, "BENCHMARK.md")
    with open(out_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    # 语义质量基线**另落程序可读副本**（供"上线前后逐键比对"做机器 diff；md 供人读）
    day = _TODAY[:10].replace("-", "")
    q_p = os.path.join(ROOT, "reports", f"评测基线_{day}.json")
    with open(q_p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {
                "generated_at": _TODAY,
                "generator": "tools/gen_benchmark.py",
                "schema_version": "1.0",
                "note": "语义质量基线（现状量化）；供语义增强上线前后逐键比对。",
                **g["quality"],
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )
        fh.write("\n")
    print(f"BENCHMARK.md 已写入 {out_p}")
    print(f"评测基线已写入 {q_p}")
    print(
        f"  gates={len(g['gates'])} tests={len(g['test_files'])} "
        f"attr={g['classifier']['attr_rows']} base={g['classifier']['base_total']} "
        f"final={g['classifier']['final_total']} detail_rows={g['classifier']['detail_rows_total']} "
        f"bridge={g['classifier']['bridge_rows']} state={g['state_records']} "
        f"merged={g['internal']['merged_records']}"
    )
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
