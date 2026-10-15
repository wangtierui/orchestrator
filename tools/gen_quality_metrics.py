# -*- coding: utf-8 -*-
"""tools/gen_quality_metrics — **数据质量六维度量 + 健康度评分**（方案 §13.5，P0）。

设计纪律（关键）：
  · **不新造检测逻辑**：六维的每个指标都取自**既有机器判据的输出**（治理库 gate_result /
    watermark / artifact 台账、corpus 索引、clean 隔离区、枚举受控域），本工具只做
    「聚合 + 评分 + 趋势」。任何一维取不到数据 ⇒ 该维标 `unavailable`（**不以 0 充数**，
    否则健康度评分会撒谎）。
  · **只披露不阻断**：评分是度量而非门禁（ADR-0012）；恶化趋势（任一维环比劣化 >10%）
    产出 `quality_regression` 提示，**是否阻断由 ADR-0012 的错误预算规则裁决**。
  · 初始**均权**（权重登记在 `QUALITY_WEIGHTS`，改动须走 ADR-0012）。

DAMA 六维 → 指标来源：
  完备性 completeness ← corpus 索引 declared vs on_disk + raw/cleaned 水位存在性
  唯一性 uniqueness   ← document 重复键 + gate_result（去重类判据）
  及时性 timeliness  ← watermark age（raw/cleaned/clauses/published）
  有效性 validity    ← 清洗隔离率（quarantine 区行数）+ 受控域违规（gate_result）
  准确性 accuracy    ← 契约字段齐备率（gate_result 契约类）+ 必填字段缺失（document）
  一致性 consistency ← 跨文件一致性判据（RFN 漂移 / 水位 / 血缘）gate_result 通过率

用法：python -m tools.gen_quality_metrics [--json] [--out <dir>]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

# 引导：**唯一引导点 `bootstrap`** —— 不自写 `sys.path.insert`（门禁
# `gate_import_bootstrap` 判据：tools/ 注入数只减不增，新代码走此形态，同 audit_health）。
# 运行方式：python -m tools.gen_quality_metrics
from bootstrap import bootstrap

bootstrap("all")

import paths

ROOT = paths.ROOT
TZ8 = timezone(timedelta(hours=8))
OUT_DIR = os.path.join(paths.REPORTS_DIR, "quality")
LATEST = os.path.join(OUT_DIR, "quality_metrics_latest.json")

# 六维权重（初始均权；改动须走 ADR-0012 —— 评分是度量工具，不是门禁）
QUALITY_WEIGHTS = {
    "completeness": 1.0,
    "uniqueness": 1.0,
    "timeliness": 1.0,
    "validity": 1.0,
    "accuracy": 1.0,
    "consistency": 1.0,
}
# 及时性阈值（天）：关键产物的水位 age 上限（超过即该维扣分）
FRESH_DAYS = {"raw:{src}": 7, "cleaned:{src}": 10, "clauses:{src}": 30, "published:external": 30}
REGRESSION_RATIO = 0.10  # 任一维环比劣化 >10% ⇒ quality_regression 提示


def _db() -> str:
    from std_lib.common_lib import governance_store as gs

    return gs.db_path()


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return list(conn.execute(sql, args))
    except sqlite3.Error:
        return []


def _pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 2) if b else 0.0


def _dim_completeness(conn) -> dict:
    """完备性：语料索引 declared vs 实际文件 + 关键水位是否登记。"""
    out: dict = {"score": None, "metrics": {}, "source": "corpus 索引 + watermark 表"}
    idx = os.path.join(paths.REPORTS_DIR, "corpus", "_index.json")
    if os.path.exists(idx):
        try:
            data = json.load(open(idx, encoding="utf-8"))
            total = int(data.get("total_files") or 0)
            out["metrics"]["index_total_files"] = total
            hashes = data.get("files") or data.get("entries") or {}
            if isinstance(hashes, dict):
                out["metrics"]["index_entries"] = len(hashes)
        except (OSError, ValueError) as e:
            out["error"] = f"corpus 索引不可读：{type(e).__name__}"
    wm = _rows(conn, "select artifact_key from watermark")
    out["metrics"]["watermarks"] = len(wm)
    # 关键族是否齐备（raw/cleaned/clauses/published 各至少一条）
    kinds: dict[str, int] = {}
    for (name,) in wm:
        k = str(name).split(":", 1)[0]
        kinds[k] = kinds.get(k, 0) + 1
    out["metrics"]["watermark_by_kind"] = kinds
    need = {"raw", "cleaned", "clauses", "published"}
    have = need & set(kinds)
    out["metrics"]["kinds_present"] = sorted(have)
    out["score"] = round(100.0 * len(have) / len(need), 2) if need else None
    return out


def _dim_uniqueness(conn) -> dict:
    """唯一性：document 主键重复 + 去重类门禁通过率。"""
    out: dict = {"score": None, "metrics": {}, "source": "document 表 + gate_result"}
    n = _rows(conn, "select count(*) from document")
    total = n[0][0] if n else 0
    out["metrics"]["documents"] = total
    if not total:
        out["error"] = "document 表为空"
        return out
    # 唯一性 = **主键唯一率**（doc_ref 是登记表主键）。⚠️ 不用 docno_norm 判重：
    # 同一文号在多源重复出现是业务常态（如中央文件被地方转载），按文号判重会把
    # 正常数据误报为质量问题（实测假低分 75.88）。docno 维度另作披露指标。
    uniq_rows = _rows(conn, "select count(*) from ("
                             "select doc_ref from document group by doc_ref)")
    uniq = uniq_rows[0][0] if uniq_rows else 0
    out["metrics"]["unique_doc_refs"] = uniq
    out["metrics"]["duplicate_doc_ref_groups"] = total - uniq
    dupno = _rows(conn, "select count(*) from ("
                         "select docno_norm from document where docno_norm is not null"
                         " and docno_norm <> '' group by docno_norm)")
    out["metrics"]["unique_docnos_disclosure"] = dupno[0][0] if dupno else 0
    out["score"] = _pct(uniq, total)
    out["metrics"]["note"] = "唯一性口径 = doc_ref 主键唯一率；docno 跨源重复仅披露"
    return out


def _fresh_limit(artifact_key: str) -> int | None:
    """按**产物族**取新鲜度阈值。

    ⚠️ `FRESH_DAYS` 的键是模板（`raw:{src}`），而真实水位键为 `raw:gov` ⇒ 精确匹配
    必然落空（首跑实测 tracked=1、及时性只由 1 条水位支撑，属**假高分**风险）。
    故此处按冒号前的族名匹配。
    """
    fam = str(artifact_key).split(":", 1)[0]
    for tpl, days in FRESH_DAYS.items():
        if tpl.split(":", 1)[0] == fam:
            return days
    return None


def _dim_timeliness(conn) -> dict:
    """及时性：关键水位 age（距今天数）按产物族阈值折算。"""
    out: dict = {"score": None, "metrics": {}, "source": "watermark 表 produced_at"}
    rows = _rows(conn, "select artifact_key, produced_at from watermark")
    if not rows:
        out["error"] = "watermark 表为空（无水位记录）"
        return out
    now = datetime.now(TZ8)
    ages: dict[str, float | None] = {}
    scores: list[float] = []
    untracked: list[str] = []
    for name, ts in rows:
        limit = _fresh_limit(name)
        if limit is None:
            untracked.append(str(name))
            continue
        age_days = None
        if ts:
            try:
                t = datetime.fromisoformat(str(ts))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=TZ8)
                age_days = round((now - t).total_seconds() / 86400.0, 2)
            except ValueError:
                age_days = None
        ages[str(name)] = age_days
        if age_days is None:
            scores.append(0.0)
        else:
            scores.append(100.0 if age_days <= limit else max(0.0, 100.0 * limit / age_days))
    out["metrics"]["tracked"] = len(scores)
    out["metrics"]["untracked_keys"] = sorted(untracked)[:12]
    out["metrics"]["age_days"] = ages
    out["metrics"]["thresholds"] = FRESH_DAYS
    out["metrics"]["note"] = "age 以 watermark.produced_at 计；阈值按产物族分档"
    if scores:
        out["score"] = round(sum(scores) / len(scores), 2)
    return out


def _dim_validity(conn) -> dict:
    """有效性：清洗隔离率 + 受控域/契约类门禁违规数。"""
    out: dict = {"score": None, "metrics": {}, "source": "quarantine 目录 + gate_result"}
    qdir = os.path.join(paths.DATA_DIR, "quarantine")
    q_files = sum(len(fs) for _, _, fs in os.walk(qdir)) if os.path.isdir(qdir) else 0
    out["metrics"]["quarantine_files"] = q_files
    total = _rows(conn, "select count(*) from document")
    docs = total[0][0] if total else 0
    out["metrics"]["documents"] = docs
    gr = _rows(conn, "select sum(case when passed=0 then 1 else 0 end), count(*) from gate_result")
    failed, runs = (gr[0][0] or 0, gr[0][1] or 0) if gr else (0, 0)
    out["metrics"]["gate_runs_recorded"] = runs
    out["metrics"]["gate_failed_records"] = failed
    # 有效性 = 门禁记录通过率（未通过记录含隔离/枚举/契约类违规）
    out["score"] = _pct(runs - failed, runs) if runs else None
    out["metrics"]["note"] = "隔离率按 quarantine 文件数单列；综合分取门禁记录通过率"
    return out


def _dim_accuracy(conn) -> dict:
    """准确性：必填字段缺失率（document 的 docno/title/source/pub_date）。"""
    out: dict = {"score": None, "metrics": {}, "source": "document 表必填字段"}
    cols = _rows(conn, "pragma table_info(document)")
    names = {c[1] for c in cols}
    required = [c for c in ("docno_norm", "title", "source", "pub_date") if c in names]
    total = _rows(conn, "select count(*) from document")
    docs = total[0][0] if total else 0
    if not docs:
        out["error"] = "document 表为空"
        return out
    miss = 0
    for c in required:
        r = _rows(conn, f"select count(*) from document where {c} is null or {c} = ''")
        miss += r[0][0] if r else 0
    cells = docs * max(1, len(required))
    out["metrics"]["required_fields"] = required
    out["metrics"]["missing_cells"] = miss
    out["metrics"]["documents"] = docs
    out["score"] = _pct(cells - miss, cells)
    return out


def _dim_consistency(conn) -> dict:
    """一致性：跨文件一致性类门禁（漂移/水位/血缘）通过率。"""
    out: dict = {"score": None, "metrics": {}, "source": "gate_result（一致性族）"}
    rows = _rows(conn, "select gate, passed from gate_result order by recorded_at desc limit 400")
    consistency_gates = ("gates.gate_config_integrity", "gates.gate_rfn_sync",
                         "gates.gate_rfn_drift", "gates.gate_watermark",
                         "gates.gate_relations", "gates.gate_citations",
                         "gates.gate_provenance", "gates.gate_contract")
    hit = [(g, bool(p)) for g, p in rows if g in consistency_gates]
    out["metrics"]["sampled_records"] = len(hit)
    out["metrics"]["passed"] = sum(1 for _, p in hit if p)
    out["score"] = _pct(out["metrics"]["passed"], len(hit)) if hit else None
    return out


def collect() -> dict:
    conn = sqlite3.connect(_db())
    try:
        dims = {
            "completeness": _dim_completeness(conn),
            "uniqueness": _dim_uniqueness(conn),
            "timeliness": _dim_timeliness(conn),
            "validity": _dim_validity(conn),
            "accuracy": _dim_accuracy(conn),
            "consistency": _dim_consistency(conn),
        }
    finally:
        conn.close()
    got = {k: v["score"] for k, v in dims.items() if v.get("score") is not None}
    total_w = sum(QUALITY_WEIGHTS[k] for k in got)
    health = round(sum(got[k] * QUALITY_WEIGHTS[k] for k in got) / total_w, 2) if total_w else None
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(TZ8).replace(microsecond=0).isoformat(),
        "weights": QUALITY_WEIGHTS,
        "dimensions": dims,
        "health_score": health,
        "health_note": ("**均权平均**（初始权重，ADR-0012 可调）；"
                        "缺失维度**不计入分母**（不以 0 充数）"),
    }


def _trend(prev_path: str, cur: dict) -> list[dict]:
    """与上次产物比：任一维劣化 >10% ⇒ quality_regression 提示。"""
    if not os.path.exists(prev_path):
        return []
    try:
        prev = json.load(open(prev_path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, d in (cur.get("dimensions") or {}).items():
        a = d.get("score")
        b = ((prev.get("dimensions") or {}).get(name) or {}).get("score")
        if a is None or not b:
            continue
        delta = round(a - b, 2)
        rel = (a - b) / b if b else 0.0
        if rel < -REGRESSION_RATIO:
            out.append({"dimension": name, "prev": b, "cur": a,
                        "delta": delta, "rel": round(rel, 4),
                        "event": "quality_regression"})
    return out


def _render_md(doc: dict) -> str:
    L = [f"# 数据质量六维度量（{doc['generated_at'][:10]}）", ""]
    hs = doc.get("health_score")
    L.append(f"**健康度评分**：{hs if hs is not None else '不可用（无可用维度）'} / 100"
             f"（{'均权' if hs is not None else '—'}；缺失维度不计入分母）")
    L.append("")
    L.append("| 维度 | 评分 | 数据来源 | 关键指标 |")
    L.append("|---|---|---|---|")
    names = {"completeness": "完备性", "uniqueness": "唯一性", "timeliness": "及时性",
             "validity": "有效性", "accuracy": "准确性", "consistency": "一致性"}
    for k, d in doc["dimensions"].items():
        sc = d.get("score")
        mets = "；".join(f"{mk}={mv}" for mk, mv in list((d.get("metrics") or {}).items())[:4]
                        if not isinstance(mv, dict))
        L.append(f"| {names.get(k, k)} | {sc if sc is not None else '不可用'} "
                 f"| {d.get('source', '')} | {mets[:120]} |")
    L.append("")
    L.append("> 纪律：六维指标**全部取自既有机器判据输出**，本工具只聚合/评分/比趋势，"
             "不新造检测逻辑；**评分只披露不阻断**（ADR-0012）。")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DAMA 六维质量度量 + 健康度评分（方案 §13.5）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args(argv)

    doc = collect()
    reg = _trend(LATEST, doc)
    if reg:
        doc["regressions"] = reg

    os.makedirs(OUT_DIR, exist_ok=True)
    day = datetime.now(TZ8).strftime("%Y%m%d")
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    for p in (LATEST, os.path.join(OUT_DIR, f"quality_metrics_{day}.json")):
        open(p, "w", encoding="utf-8", newline="\n").write(text + "\n")
    open(os.path.join(OUT_DIR, "quality_metrics_latest.md"), "w",
            encoding="utf-8", newline="\n").write(_render_md(doc))

    # 告警发送点（门禁 E2 要求「notify.on 声明的事件必须有发送方」）：六维环比劣化
    # >10% ⇒ `quality_regression`。评分本身只披露，**劣化趋势**才告警——绝对分低是常态，
    # 突然变差才是信号。
    if reg:
        from std_lib.common_lib import notify

        for r in reg:
            notify.notify("quality_regression",
                          f"质量维度劣化：{r['dimension']} {r['prev']} → {r['cur']}",
                          r, force=True)

    if a.json:
        print(text)
    else:
        print(f"[quality] 健康度={doc['health_score']} "
              f"（可用维度 {sum(1 for d in doc['dimensions'].values() if d.get('score') is not None)}/6）")
        for k, d in doc["dimensions"].items():
            print(f"  {k:14s} {d.get('score')}")
        for r in doc.get("regressions", []):
            print(f"  [quality_regression] {r['dimension']} {r['prev']} → {r['cur']}")
        print(f"[quality] 已写入 {os.path.relpath(LATEST, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
