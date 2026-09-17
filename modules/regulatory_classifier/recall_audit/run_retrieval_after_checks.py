# -*- coding: utf-8 -*-
"""
run_retrieval_after_checks.py — 检索流程自动重跑编排器（门禁式·确定性）

设计定位：
    本脚本是 regulatory_classifier/recall_audit 下「检索→交付物」链路的自动化入口。
    它并不直接轮询定时，而是作为【门禁式】编排器：无论被何种节奏触发（手动 / 自动化每日巡检），
    都先确定性校验四个前置条件是否已“成功完成”，通过后再判断是否相对上次重跑有数据变化，
    仅当「四门禁均通过 且 数据签名变化」时才重跑 scanner→build_outputs→report，并产出结构化报告。

四门禁（确定性，无 cron 隐含语义）：
  Gate 1 · 清洗成功：
      - clean_index 持久化索引存在且读取成功；
      - 五源 latest csv 均存在且 sha256 与索引一致（validate_files：missing/hash_mismatch=0）；
      - 索引与磁盘实时扫描（scan_sources, hash_files=False）的 latest 日期/记录数一致（索引未陈旧）；检测到索引陈旧时**自动重建 clean_index 并重试**，免去手动 rebuild；
       - 全仓下游脚本（scanner.py / probe_mapping.py / scanner_protocol.py /
         match_theme_docs.py 等）均已从 clean_index 动态派生 latest，并经全局
         lint_hardcoded_snapshots 校验无硬编码 *_cleaned_(YYYYMMDD) 文件路径字面量，
         避免重跑读旧快照（N-3 门禁，与 run_gates.py 第 4 道门禁同口径）。
  Gate 2 · 效力检查成功：
      - verification_state.json 存在、非空；
      - 全部条目 last_checked_at ≤ 90 日（核验缓存为近期，非陈旧/废弃）；
      - 全部条目 verification_source 落于既定合法类别（「北大法宝*」直接核验 / 「规则判断」合规回退 / 「数据层统一清洗」清洗占位符），无「来源异常」或「过期」条目；
      - 「数据层统一清洗」占位符占比作为透明告警（不阻塞），提示存在未走北大法宝核验的条目。
  Gate 3 · 链路契约（2026-09-01 新增，补 Gate1/Gate2 的能力缺口）：
      - 校验重跑链路脚本与权威表结构的**兼容性**；
      - 背景：06:00 调度 Gate1/Gate2 全 PASS，build_outputs.py 仍以 KeyError('主题') 崩溃
        （2026-08-31 归属表重构将「主题」列拆出至主题归属表，脚本未同步）。
        既有两门禁只校验「数据是否新鲜 / 效力是否有效」，拦不住此类结构性断裂；
      - 判定：脚本以**裸下标**访问 row["主题"] 且未接入主题注入（主题归属表.csv /
        rfn.load_attr_rows）→ 契约破裂，阻断重跑。.get() 与自造 dict 形态不误报。
  Gate 4 · schema 预检（2026-09-01 新增，与 Gate 3 互补的「数据侧」兜底）：
      - 校验链路消费的数据文件结构 vs 权威 schema 定义：文件归属表 8 列（rfn.registry.CSV_FIELDS）、
        主题归属表 3 列（THEME_FIELDS）、明细表 10 列（scripts/build_detail_tables.FIELDS）、
        数据底座 40 个（T1–T10 × base/final/matched/citerefs）的结构类型与元素键集；
      - 背景：Gate 3 防「脚本裸下标」，但若数据 schema 偏离权威定义（重构回退恢复主题列、
        误写列头、底座键集漂移/缺失），链路仍会崩溃或静默产出错数；本门禁从数据侧阻断；
      - 判定：任一文件缺失、列头 ≠ 权威、底座结构/键集偏离 → 阻断重跑；
        键集按**超集**判定（允许未来加字段，缺字段必报）。

仅当四门禁通过 且 当前数据签名 ≠ 上次成功重跑签名 时，才重跑三步；否则绝不执行。

产物：《重跑执行报告.json》（结构化、程序可直接读取）+ 既有 retrieval 交付物。
用法：
    python run_retrieval_after_checks.py            # 四门禁+变更检测；有变化才重跑
    python run_retrieval_after_checks.py --force    # 四门禁通过即重跑（忽略“未变化”跳过）
    python run_retrieval_after_checks.py --check-only  # 仅校验四门禁并落报告，不重跑
"""
from __future__ import annotations

import collections
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# —— 路径装配（P4：相对 orchestrator 根，无盘符） ——
_THIS = os.path.dirname(os.path.abspath(__file__))          # modules/regulatory_classifier/recall_audit
_MOD_CLASS = os.path.dirname(_THIS)                          # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
RECALL_DIR = _THIS
ROOT = _MOD_CLASS  # （rfn 包 / scripts 所在根）
CLASSIFIER_DATA = os.path.join(_MOD_CLASS, "data")
ATTR = os.path.join(CLASSIFIER_DATA, "人身保险公司-文件归属表.csv")
REPORT_PATH = os.path.join(RECALL_DIR, "output", "重跑执行报告.json")
CHECKPOINT_PATH = os.path.join(RECALL_DIR, "output", ".retrieval_checkpoint.json")
SCANNER_PATH = os.path.join(RECALL_DIR, "scanner.py")
_OUT_DIR = os.path.join(RECALL_DIR, "output")
os.makedirs(_OUT_DIR, exist_ok=True)   # 产物目录（代码/产物分离）

_TZ = timezone(timedelta(hours=8))
VALIDITY_MAX_AGE_DAYS = 90
# 核验覆盖率门禁阈值（F-C06）：state 条目 / 五源 cleaned 最新快照全量记录。
# 当前基线约 0.74（2975/4043）；低于阈值即 FAIL（覆盖率跌破需显式修复而非静默）。
COVERAGE_MIN_RATIO = 0.70

# —— 依赖装配（实测先于推断：依赖既存 clean_index / verification_state / lint 门禁） ——
sys.path.insert(0, _ORCH_ROOT)  # 根级 config / std_lib / interfaces
sys.path.insert(0, ROOT)  # Gate 4 schema 预检：rfn.registry / scripts.build_detail_tables
# 阶段 3（2026-09-18）：五源 cleaned 索引与时效核验状态一律经 interfaces 唯一入口，
# 本模块不再把兄弟模块目录插进 sys.path（`_SCRAPERS_MOD` 引导已移除）。

CLEAN_INDEX_OK = True
VERIFICATION_OK = True
LINT_OK = True
try:
    from interfaces.clean_index_api import get_clean_index, scan_sources  # noqa: E402
except Exception as e:  # pragma: no cover
    CLEAN_INDEX_OK = False
    _import_err_clean = repr(e)
try:
    from verification_state_mirror import MIRROR_PATH, load_mirror  # noqa: E402  (N-5 本地镜像)
except Exception as e:  # pragma: no cover
    VERIFICATION_OK = False
    _import_err_ver = repr(e)
try:
    # F-S02 接线修复：旧顶层模块 lint_hardcoded_snapshots 已迁移为 gates 包内实现
    # （gates.gate_hardcoded_snapshots.lint_hardcoded），原导入路径恒 ModuleNotFoundError。
    from gates.gate_hardcoded_snapshots import lint_hardcoded  # noqa: E402  (N-3 门禁复用)
except Exception as e:  # pragma: no cover
    LINT_OK = False
    _import_err_lint = repr(e)


def _utc8_now() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def _now() -> datetime:
    return datetime.now(_TZ)


def _tail(text: str, n: int = 1500) -> str:
    if not text:
        return ""
    return text[-n:]


# --------------------------------------------------------------------------- #
# Gate 1 · 清洗成功
# --------------------------------------------------------------------------- #
def _latest_file_sha(idx, src_id):
    """取该源最新快照的 jsonl（优先）/csv 的 sha256（F-D05③：同日改写检测用；无则 None）。"""
    l = idx.latest(src_id) or {}
    sn = idx.snapshot(src_id, l.get("date") or "") or {}
    files = sn.get("files") or {}
    for ext in ("jsonl", "csv"):
        sha = (files.get(ext) or {}).get("sha256")
        if sha:
            return sha
    return None


def gate_clean():
    """返回 (passed, detail)。detail 含逐源 latest、校验、索引陈旧、scanner 对齐。"""
    detail = {
        "index_loaded": False,
        "validate": {"missing": [], "hash_mismatch": [], "ok_count": 0},
        "index_stale": False,
        "stale_sources": [],
        "scanner_aligned": True,
        "scanner_mismatch": [],
        "per_source": {},
    }
    if not CLEAN_INDEX_OK:
        detail["error"], detail["index_loaded"] = "clean_index 模块导入失败: " + _import_err_clean, False
        return False, detail

    try:
        idx = get_clean_index()
    except Exception as e:
        detail["error"], detail["index_loaded"] = "get_clean_index 失败: " + repr(e), False
        return False, detail
    detail["index_loaded"] = True

    # 1) 完整性：索引引用文件存在 + sha256 一致
    vf = idx.validate_files()
    detail["validate"] = {
        "missing": vf.get("missing", []),
        "hash_mismatch": vf.get("hash_mismatch", []),
        "ok_count": len(vf.get("ok", [])),
        "degraded_no_sha": len(vf.get("degraded_no_sha") or []),   # F-D05②：无 sha 降级比对条数
    }
    if vf.get("missing") or vf.get("hash_mismatch"):
        return False, detail

    # 2) 索引 vs 磁盘实时扫描（只读，不写）是否陈旧
    try:
        live = scan_sources(hash_files=False)
    except Exception as e:
        detail["error"] = "scan_sources 失败: " + repr(e)
        return False, detail
    for src_id in idx.source_ids():
        il = idx.latest(src_id)
        ls = live.get(src_id, {})
        ll = ls.get("latest") if isinstance(ls, dict) else None
        rec = {
            "index_latest_date": (il or {}).get("date"),
            "index_record_count": (il or {}).get("record_count"),
            "live_latest_date": (ll or {}).get("date"),
            "live_record_count": (ll or {}).get("record_count"),
            "index_sha": _latest_file_sha(idx, src_id),   # F-D05③：内容指纹（同日改写可感）
        }
        detail["per_source"][src_id] = rec
        if (rec["index_latest_date"] != rec["live_latest_date"]
                or rec["index_record_count"] != rec["live_record_count"]):
            detail["index_stale"] = True
            detail["stale_sources"].append(src_id)
    # 2b) 索引陈旧 → 自动重建并重试（方案A：免去手动 rebuild 步骤，同时保留透明通知）
    if detail["index_stale"]:
        try:
            idx = get_clean_index(rebuild=True)
            vf = idx.validate_files()
            live = scan_sources(hash_files=False)
            rebuilt_ok = not (vf.get("missing") or vf.get("hash_mismatch"))
            stale2 = False
            new_per: dict[str, dict] = {}
            for src_id in idx.source_ids():
                il = idx.latest(src_id)
                ls = live.get(src_id, {})
                ll = ls.get("latest") if isinstance(ls, dict) else None
                rec = {
                    "index_latest_date": (il or {}).get("date"),
                    "index_record_count": (il or {}).get("record_count"),
                    "live_latest_date": (ll or {}).get("date"),
                    "live_record_count": (ll or {}).get("record_count"),
                }
                new_per[src_id] = rec
                if (rec["index_latest_date"] != rec["live_latest_date"]
                        or rec["index_record_count"] != rec["live_record_count"]):
                    stale2 = True
                    detail["stale_sources"].append(src_id)
            if rebuilt_ok and not stale2:
                detail["index_rebuilt"] = True
                detail["index_stale"] = False
                detail["per_source"]  = new_per
                detail["stale_sources"] = []
                # 继续进入下方 scanner 对齐校验
            else:
                detail["index_rebuilt"] = True
                detail["index_stale"] = True
                detail["stale_sources"] = [
                    s for s, r in new_per.items()
                    if r["index_latest_date"] != r["live_latest_date"]
                    or r["index_record_count"] != r["live_record_count"]
                ]
                return False, detail
        except Exception as e:
            detail["index_rebuilt"] = False
            detail["index_stale"] = True
            detail["error"] = "clean_index 自动重建失败: " + repr(e)
            return False, detail

    # 3) scanner.py 是否已从 clean_index 动态派生 latest（无硬编码快照日期）
    #    改造口径（2026-08-29）：scanner 必须 import clean_index 并经 latest_csv_path 派生，
    #    禁止硬编码 *_cleaned_(YYYYMMDD)；否则重跑可能读旧快照。
    if os.path.exists(SCANNER_PATH):
        txt = open(SCANNER_PATH, encoding="utf-8").read()
        # 阶段 3（2026-09-18）：判据的**意图**是"scanner 经 clean_index 动态派生 latest"，
        # 而**访问路径**已按纪律收敛到 `interfaces.clean_index_api`（不再跨模块裸 import）。
        # 故两种形态均判合规；仍要求出现 `get_clean_index`（不得改为硬编码快照路径）。
        uses_clean_index = (
            ("from clean_index import" in txt) or ("interfaces.clean_index_api" in txt)
        ) and ("get_clean_index" in txt)
        # 检测残留硬编码快照日期：形如 {src}_cleaned_{8位数字}
        hardcoded = re.findall(r'(?:gov|mof|nfra|pbc|supp)_cleaned_(\d{8})', txt)
        detail["scanner_uses_clean_index"] = uses_clean_index
        detail["scanner_hardcoded_dates"] = sorted(set(hardcoded))
        if not uses_clean_index:
            detail["scanner_aligned"] = False
            detail["scanner_mismatch"].append(
                {"issue": "scanner.py 未依赖 clean_index 动态派生 latest，存在读旧快照风险"})
        if hardcoded:
            detail["scanner_aligned"] = False
            detail["scanner_mismatch"].append(
                {"issue": "scanner.py 仍存在硬编码快照日期", "dates": sorted(set(hardcoded))})

    # 3b) 全局快照日期 lint（N-3）：覆盖 scanner.py / probe_mapping.py /
    #     scanner_protocol.py / match_theme_docs.py 等**全部下游脚本**，
    #     而非仅查 scanner.py；任何 ``{src}_cleaned_{YYYYMMDD}.csv|.jsonl``
    #     文件路径字面量即视为回归，须改为 clean_index 动态取 latest。
    #     复用 lint_hardcoded_snapshots.lint_hardcoded()，与 run_gates.py 第 4 道门禁同口径。
    if LINT_OK:
        lint_findings = lint_hardcoded()
        detail["hardcoded_findings"] = [
            {"file": fp, "line": ln, "token": tok} for fp, ln, tok in lint_findings
        ]
        if lint_findings:
            detail["scanner_aligned"] = False
            detail["scanner_mismatch"].append(
                {"issue": "全仓存在硬编码快照日期（应改 clean_index 动态取 latest）",
                 "count": len(lint_findings),
                 "examples": [f"{fp}:{ln}: {tok}" for fp, ln, tok in lint_findings[:10]]})
    else:
        # F-S02：N-3 lint 模块导入失败 → 硬编码快照检查无法执行，不得静默视为通过。
        detail["lint_import_error"] = _import_err_lint
        detail["scanner_aligned"] = False
        detail["scanner_mismatch"].append(
            {"issue": "N-3 lint 模块（lint_hardcoded_snapshots）导入失败，硬编码快照检查无法执行",
             "error": _import_err_lint})

    if not detail["scanner_aligned"]:
        return False, detail

    return True, detail


# --------------------------------------------------------------------------- #
# Gate 2 · 效力检查成功
# --------------------------------------------------------------------------- #
# 核验来源合法类别（据本项目既定口径）：
#   「北大法宝」*     —— 北大法宝 CLI 直接核验（含「无同名命中，维持原判定」回退标记）
#   「规则判断」      —— 既定合规回退口径（北大法宝无同名命中，非失败）
#   「数据层统一清洗」—— 统一清洗管道占位符（未走北大法宝核验，建议按需补验，但不阻塞）
ACCEPT_SOURCE_PREFIXES = ("北大法宝", "规则判断", "数据层统一清洗", "人工复核")

def gate_validity():
    detail = {
        "state_file_exists": False,
        "entries_total": 0,
        "fresh": 0,
        "stale": 0,
        "categories": {},
        "stale_examples": [],
        "warnings": [],
    }
    if not VERIFICATION_OK:
        detail["error"] = "verification_state_mirror 模块导入失败: " + _import_err_ver
        return False, detail
    state = load_mirror()  # N-5：读本地镜像（自动按源 mtime 刷新，解耦 scrapers 运行环境）
    detail["state_file_exists"] = bool(state)
    if not state:
        return False, detail
    now = _now()
    max_age = timedelta(days=VALIDITY_MAX_AGE_DAYS)
    fresh = stale = 0
    cat = collections.Counter()
    for key, rec in state.items():
        vs = (rec.get("verification_source", "") or "")
        lu = rec.get("last_checked_at", "")
        try:
            last = datetime.strptime(lu, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ)
        except ValueError:
            last = None
        ck = "unknown"
        for p in ACCEPT_SOURCE_PREFIXES:
            if vs.startswith(p):
                ck = p
                break
        cat[ck] += 1
        bad = False
        if last is None:
            bad = True
            detail["stale_examples"].append({"key": key, "reason": "last_checked_at 解析失败"})
        elif (now - last) > max_age:
            bad = True
            detail["stale_examples"].append({"key": key, "reason": "超过90日"})
        elif ck == "unknown":
            bad = True
            detail["stale_examples"].append({"key": key, "reason": "核验来源异常: " + vs[:30]})
        if bad:
            stale += 1
        else:
            fresh += 1
    detail["entries_total"] = len(state)
    detail["fresh"] = fresh
    detail["stale"] = stale
    detail["categories"] = dict(cat)
    placeholder = cat.get("数据层统一清洗", 0)
    rule = cat.get("规则判断", 0)
    if placeholder:
        detail["warnings"].append(
            f"{placeholder} 条 verification_source 为「数据层统一清洗」占位符，未走北大法宝核验，建议按需补验。")
    if rule:
        detail["warnings"].append(
            f"{rule} 条为「规则判断」回退（北大法宝无同名命中），属既定合规回退口径，非失败。")
    # 核验覆盖率（F-C06）：已核验 state 条目 vs 五源 cleaned 最新快照全量记录（4043 实测）。
    # 未覆盖记录在 state 中无核验结论，不得视为"已完成时效判定"；覆盖率跌破阈值即 FAIL。
    if CLEAN_INDEX_OK:
        try:
            _idx = get_clean_index()
            total = 0
            for _sid in _idx.source_ids():
                _rec = _idx.latest(_sid) or {}
                total += int(_rec.get("record_count") or 0)
            cov = {"state_records": len(state), "source_records": total,
                   "uncovered": max(total - len(state), 0)}
            cov["ratio"] = (round(len(state) / total, 4) if total else None)
            detail["coverage"] = cov
            if total and len(state) < total:
                detail["warnings"].append(
                    f"核验覆盖率 {len(state)}/{total}（未覆盖 {total - len(state)} 条，占 "
                    f"{round((total - len(state)) / total * 100, 1)}%）——未覆盖记录无核验结论，"
                    "不得视为已完成时效判定（可运行 verify_missing.py 补验）。")
            if total and cov["ratio"] is not None and cov["ratio"] < COVERAGE_MIN_RATIO:
                detail["warnings"].append(
                    f"核验覆盖率 {cov['ratio']} 低于门禁阈值 {COVERAGE_MIN_RATIO}。")
                return False, detail
        except Exception as e:  # noqa: BLE001
            detail["coverage_error"] = repr(e)
    if stale > 0 or fresh == 0:
        return False, detail
    return True, detail


# --------------------------------------------------------------------------- #
# 数据签名 + 检查点（幂等：未变化则跳过重跑）
# --------------------------------------------------------------------------- #
def compute_signature(clean_detail, validity_detail):
    clean_part = {}
    for src, rec in clean_detail.get("per_source", {}).items():
        # F-D05③：签名纳入内容 sha（同日改写 → 签名变化 → 触发重跑，而非幂等跳过）。
        clean_part[src] = {"date": rec.get("index_latest_date"),
                           "records": rec.get("index_record_count"),
                           "sha": rec.get("index_sha")}
    sig = {
        "clean": clean_part,
        "validity": {
            "entries_total": validity_detail.get("entries_total"),
            "fresh": validity_detail.get("fresh"),
        },
    }
    state_path = MIRROR_PATH if VERIFICATION_OK else None  # N-5：镜像 mtime 纳入签名
    if state_path and os.path.exists(state_path):
        sig["validity"]["state_mtime"] = os.path.getmtime(state_path)
    return json.dumps(sig, sort_keys=True, ensure_ascii=False)


def load_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        try:
            return json.load(open(CHECKPOINT_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_checkpoint(signature):
    data = {"signature": signature, "saved_at": _utc8_now()}
    tmp = CHECKPOINT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKPOINT_PATH)


# --------------------------------------------------------------------------- #
# Gate 3 · 链路契约（脚本 ↔ 数据结构兼容性预检，2026-09-01 新增）
# --------------------------------------------------------------------------- #
# 背景（本门禁的设立理由）：2026-09-01 06:00 调度中 Gate1/Gate2 全部 PASS，
# 但 build_outputs.py 仍以 KeyError('主题') 崩溃。根因是 2026-08-31 归属表重构
# 将「主题」列拆出至主题归属表，而脚本未同步。既有两门禁只校验
# 「数据是否新鲜」「效力是否有效」，**不校验脚本与数据结构是否兼容**，
# 因此拦不住此类结构性断裂。Gate 3 专门补上这一缺口。
#
# 判定规则（三条件同时成立才判 FAIL）：
#   ① 脚本确实读取权威归属表（源码出现 文件归属表.csv）；
#   ② 以**下标方式**访问「主题」列（row["主题"]）；
#   ③ 未接入任何主题注入途径（主题归属表.csv / rfn.load_attr_rows）。
# 条件 ① 用于排除「自造 dict 也含主题键」的脚本（如 build_sync_plan.py），避免误报阻断。
CONTRACT_SCRIPTS = ["scanner.py", "build_outputs.py", "report.py"]
_ATTR_MARKER = "文件归属表.csv"
# 主题列访问（捕获组 1 为可选的 .get 前缀）；注：Python re 不支持可变宽度 lookbehind，
# 故改用捕获组在 _has_bare_theme_subscript 中判定，而非 (?<!...) 断言。
_THEME_ACCESS_RE = re.compile(r'(\.\s*get\s*)?\[\s*["\']主题["\']\s*\]')
_THEME_INJECTION_MARKERS = ("主题归属表.csv", "load_attr_rows")


def _has_bare_theme_subscript(src):
    """是否存在「裸下标」访问主题列（row["主题"]）。.get("主题") 形态视为安全。"""
    return any(m.group(1) is None for m in _THEME_ACCESS_RE.finditer(src))


def gate_contract(scripts_dir=None):
    """Gate 3：校验重跑链路脚本与权威表结构的契约兼容性。

    scripts_dir：脚本目录，默认 RECALL_DIR；仅供测试注入临时目录使用。
    """
    base = scripts_dir or RECALL_DIR
    violations = []
    for name in CONTRACT_SCRIPTS:
        path = os.path.join(base, name)
        if not os.path.exists(path):
            violations.append({"script": name, "issue": "脚本缺失", "detail": path})
            continue
        try:
            src = open(path, encoding="utf-8").read()
        except Exception as e:
            violations.append({"script": name, "issue": "源码读取失败", "detail": repr(e)})
            continue
        if (_ATTR_MARKER in src
                and _has_bare_theme_subscript(src)
                and not any(m in src for m in _THEME_INJECTION_MARKERS)):
            violations.append({
                "script": name,
                "issue": "主题列契约破裂",
                "detail": ("以下标方式访问 row['主题']，但未接入主题注入（主题归属表.csv / "
                           "rfn.load_attr_rows）。2026-08-31 重构后文件归属表仅 8 列、已无「主题」列，"
                           "执行将必现 KeyError（2026-09-01 06:00 既发故障）。"),
            })
    return (not violations), {"violations": violations, "checked": CONTRACT_SCRIPTS}


# --------------------------------------------------------------------------- #
# Gate 4 · schema 预检（数据文件结构 vs 权威定义，2026-09-01 新增）
# --------------------------------------------------------------------------- #
# 背景：Gate 3 从「脚本侧」防裸下标访问主题列（2026-09-01 06:00 故障即脚本未同步
# 归属表拆表）。本门禁从「数据侧」兜底——即使脚本全部正确，若数据文件 schema 偏离
# 权威定义（重构回退如恢复主题列、误写列头、清洗改列、数据底座键集漂移、底座缺失），
# 链路仍会崩溃或静默产出错数。两门禁互补，共同防结构性断裂复发。
#
# 权威 schema 事实源（禁止本地重复定义，一律导入）：
#   - 归属表 8 列 / 主题归属表 3 列：rfn.registry.CSV_FIELDS / THEME_FIELDS
#   - 明细表 10 列：scripts/build_detail_tables.FIELDS
#   - 数据底座 40 个（T1–T10 × base/final/matched/citerefs）：
#     结构类型 + 元素键集（超集判定，允许未来加字段；缺字段必报）
#   注：计数校验（T1 339 等）由 scripts/_verify_written.py 门禁负责，本门禁不重复。
SCHEMA_OK = True
try:
    from rfn.registry import CSV_FIELDS, THEME_FIELDS  # noqa: E402
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from build_detail_tables import FIELDS as DETAIL_FIELDS  # noqa: E402
except Exception as _e:  # pragma: no cover
    SCHEMA_OK = False
    _schema_import_err = repr(_e)
    CSV_FIELDS = THEME_FIELDS = DETAIL_FIELDS = []

# 数据底座元素键集（2026-09-01 实测，超集判定）。
# 注意：matched/citerefs 的 value 键集存在多形态——补齐路径（align_artifacts 对归属表驱动
# 记录生成）写「核心键」，扫描路径（scanner 命中五源）额外写富字段 how/url/source_origin。
# 故必选键定义为**核心键**（两种路径均保证），富字段为条件字段不纳入必选，避免误报。
# F-D11 单源化（2026-09-12）：键集唯一事实源 = interfaces/contract.py（原字面量副本改 re-export）
from interfaces.contract import BASE_KEYS as _BASE_KEYS  # noqa: E402
from interfaces.contract import CITEREFS_KEYS as _CITEREFS_KEYS  # noqa: E402
from interfaces.contract import FINAL_KEYS as _FINAL_KEYS  # noqa: E402
from interfaces.contract import MATCHED_KEYS as _MATCHED_KEYS  # noqa: E402

# 富字段（扫描路径条件字段，不纳入必选）：matched: how/url/source_origin；
# citerefs: source_origin/art_refs_str/name_refs_top 之外见上。注：2026-09-01 实测
# T7 matched 33 条为补齐路径生成、缺 how/url/source_origin（见 Gate4 开发注记），
# 属生成方信息未补全，列为数据治理项，不作门禁阻断。
# 数据底座命名：_t{n}_{base|final|matched|citerefs}.json（T1–T10；T0 不生成数据底座）
_BASE_RE = re.compile(r"^_t\d+_(base|final|matched|citerefs)\.json$")


def _csv_header(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def _json_shape(path):
    """返回 (顶层结构, 元素键集)。兼容 list（base/final）与 dict（matched/citerefs，RFN→rec）。"""
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, list):
        return "list", (set(d[0].keys()) if d else set())
    if isinstance(d, dict):
        v0 = next(iter(d.values()), None)
        return "dict", (set(v0.keys()) if isinstance(v0, dict) else set())
    return type(d).__name__, set()


def gate_schema(data_dir=None):
    """Gate 4：校验链路数据文件的 schema 与权威定义一致。

    data_dir：数据目录，默认 CLASSIFIER_DATA；仅供测试注入临时目录使用。
    返回 (passed, detail)。
    """
    base = data_dir or CLASSIFIER_DATA
    detail = {"import_ok": SCHEMA_OK, "problems": [], "checked": {}}
    if not SCHEMA_OK:
        detail["problems"].append("schema 权威常量导入失败（rfn.registry / build_detail_tables）: " + _schema_import_err)
        return False, detail

    def _chk(tag, ok, msg):
        detail["checked"][tag] = {"ok": ok, "msg": msg}
        if not ok:
            detail["problems"].append(f"{tag}: {msg}")

    # 1) 归属表列头（8 列，无「主题」列；2026-08-31 拆表后不应出现主题列）
    attr_path = os.path.join(base, "人身保险公司-文件归属表.csv")
    if not os.path.exists(attr_path):
        _chk("attr_csv", False, "文件缺失: " + attr_path)
    else:
        head = _csv_header(attr_path)
        same = head == CSV_FIELDS
        _chk("attr_csv", same,
             "列头与权威一致" if same else
             f"列头 {head} ≠ 权威 {CSV_FIELDS}（拆表重构后文件归属表仅 8 列、无主题列）")

    # 2) 主题归属表列头（3 列，主题唯一来源）
    theme_path = os.path.join(base, "人身保险公司-主题归属表.csv")
    if not os.path.exists(theme_path):
        _chk("theme_csv", False, "文件缺失: " + theme_path)
    else:
        head = _csv_header(theme_path)
        same = head == THEME_FIELDS
        _chk("theme_csv", same,
             "列头与权威一致" if same else f"列头 {head} ≠ 权威 {THEME_FIELDS}")

    # 3) 明细表列头（11 份：T0_9 + T1–T10，10 列）
    dets = sorted(f for f in os.listdir(base)
                  if re.match(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$", f))
    if len(dets) != 11:
        _chk("detail_tables", False, f"明细表数量 {len(dets)} ≠ 11（T0–T10）: {dets}")
    bad_det = []
    for f in dets:
        head = _csv_header(os.path.join(base, f))
        if head != DETAIL_FIELDS:
            bad_det.append(f"{f}: {head}")
    det_ok = (len(dets) == 11 and not bad_det)
    _chk("detail_tables", det_ok,
         "11 份明细表列头与权威一致" if det_ok
         else f"列头偏离权威 {DETAIL_FIELDS} 的明细表: {bad_det or '无'}")

    # 4) 数据底座 40 个：结构类型 + 元素键集
    base_files = sorted(f for f in os.listdir(base) if _BASE_RE.match(f))
    expect_keys = {"base": _BASE_KEYS, "final": _FINAL_KEYS,
                   "matched": _MATCHED_KEYS, "citerefs": _CITEREFS_KEYS}
    expect_shape = {"base": "list", "final": "list", "matched": "dict", "citerefs": "dict"}
    per_suf = {s: [] for s in expect_keys}
    for f in base_files:
        suf = _BASE_RE.match(f).group(1)
        per_suf[suf].append(f)
    if len(base_files) != 40:
        _chk("base_jsons", False, f"数据底座数量 {len(base_files)} ≠ 40（T1–T10 × 4 类）: 缺失见 per_suf")
    bad_base = []
    for suf, files in per_suf.items():
        if len(files) != 10:
            bad_base.append(f"{suf}: {len(files)} 个（期望 10）")
            continue
        for f in files:
            shape, keys = _json_shape(os.path.join(base, f))
            if shape != expect_shape[suf]:
                bad_base.append(f"{f}: 结构 {shape}（期望 {expect_shape[suf]}）")
            elif not expect_keys[suf].issubset(keys):
                miss = sorted(expect_keys[suf] - keys)
                bad_base.append(f"{f}: 缺键 {miss}")
    _chk("base_jsons", (len(base_files) == 40 and not bad_base),
         "；".join(bad_base) if bad_base else "40 个数据底座结构/键集全部符合权威定义")

    # 5) matched/citerefs 对 base 覆盖率（F-D04）：缺口可见化（>5% 判异常拦截；
    #    缺口 ≤5% 记录 warning——缺口多为五库未收录文件，见 match_theme_docs *_miss.json 清单）。
    base_keys, matched_keys, citerefs_keys = set(), set(), set()
    for f in os.listdir(base):
        fp = os.path.join(base, f)
        if re.match(r"^_t\d+_base\.json$", f):
            try:
                for x in json.load(open(fp, encoding="utf-8")):
                    k = x.get("监管文件编号", "")
                    if k:
                        base_keys.add(k)
            except Exception:  # noqa: BLE001
                pass
        elif re.match(r"^_t\d+_(matched|citerefs)\.json$", f):
            try:
                ks = set(json.load(open(fp, encoding="utf-8")).keys())
            except Exception:  # noqa: BLE001
                ks = set()
            (matched_keys if "matched" in f else citerefs_keys).update(ks)
    gap = sorted(base_keys - matched_keys)
    cov = {
        "base": len(base_keys), "matched": len(matched_keys & base_keys),
        "citerefs": len(citerefs_keys & base_keys),
        "missing": len(gap), "ratio": round(len(gap) / max(len(base_keys), 1), 4),
    }
    detail["matched_coverage"] = cov
    if cov["ratio"] > 0.05:
        detail["problems"].append(
            f"matched_coverage: base {cov['base']} vs matched {cov['matched']}（缺 {cov['missing']} 条，"
            f"{cov['ratio']:.2%} > 5%）——覆盖缺口异常，见 *_matched_miss.json 清单")
    elif gap:
        detail.setdefault("warnings", []).append(
            f"matched 覆盖率缺口 {cov['missing']}/{cov['base']}（{cov['ratio']:.2%}）——"
            "多为五库未收录文件（清单见 *_matched_miss.json，数据归集后重跑 match 可消）")
    detail["checked"]["matched_coverage"] = {
        "ok": cov["ratio"] <= 0.05, "msg": f"{cov['matched']}/{cov['base']}（缺 {cov['missing']}）"}

    return (not detail["problems"]), detail


# --------------------------------------------------------------------------- #
# 重跑阶段执行（subprocess：scanner/build_outputs/report 均在导入时执行，须如此调用）
# --------------------------------------------------------------------------- #
def run_stage(script_name):
    path = os.path.join(RECALL_DIR, script_name)
    if not os.path.exists(path):
        return {"name": script_name, "status": "failed", "returncode": -1,
                "error": "脚本不存在: " + path, "tail": ""}
    try:
        proc = subprocess.run(
            [sys.executable, path], cwd=RECALL_DIR,
            capture_output=True,
            encoding="utf-8", errors="replace", timeout=1200)
    except subprocess.TimeoutExpired:
        return {"name": script_name, "status": "failed", "returncode": -2,
                "error": "超时（>1200s）", "tail": ""}
    except Exception as e:
        return {"name": script_name, "status": "failed", "returncode": -3,
                "error": repr(e), "tail": ""}
    return {
        "name": script_name,
        "status": "success" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "tail": _tail((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")),
    }


PRODUCT_FILES = [
    "scan_records.csv", "scan_hits_attr.jsonl",
    "疑似漏提取文件清单.csv", "边界案例清单.csv",
    "归属表全文提取记录.csv", "归属表无全文清单.csv",
    "关键词库扩充建议.csv", "_stats.json", "召回复核报告.md",
]


def collect_products():
    out = {}
    for name in PRODUCT_FILES:
        p = os.path.join(RECALL_DIR, "output", name)
        if os.path.exists(p):
            out[name] = {"path": p, "size_bytes": os.path.getsize(p)}
        else:
            out[name] = {"path": p, "exists": False}
    return out


def load_stats():
    p = os.path.join(RECALL_DIR, "output", "_stats.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8")).get("stats", {})
        except Exception:
            return {}
    return {}


def _write_report(report):
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REPORT_PATH)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    force = "--force" in sys.argv
    check_only = "--check-only" in sys.argv

    report = {
        "schema_version": "1.0.0",
        "generated_at": _utc8_now(),
        "trigger": "clean_index + verification_state + 链路契约 + schema 预检 四门禁（gate-based，非 cron 轮询语义）",
        "action": None,
        "gates": {},
        "change_detected": None,
        "previous_signature": None,
        "current_signature": None,
        "stages": [],
        "products": {},
        "stats": {},
        "skip_reason": None,
        "warnings": [],
        "summary": "",
    }

    # —— Gate 1 · 清洗成功 ——
    clean_ok, clean_detail = gate_clean()
    report["gates"]["clean"], report["clean_index_rebuilt"] = (
        {"passed": clean_ok, "detail": clean_detail}, bool(clean_detail.get("index_rebuilt")))
    if clean_detail.get("index_rebuilt"):
        report["warnings"].append(
            "clean_index 索引曾陈旧，已在本次执行中自动重建并纳入最新 cleaned 快照；后续重跑将基于最新数据。")
    if not clean_ok:
        report["action"] = "skipped_gate_fail"
        report["skip_reason"] = "gate_clean_fail"
        reasons = []
        if clean_detail.get("validate", {}).get("missing"):
            reasons.append("索引文件缺失")
        if clean_detail.get("validate", {}).get("hash_mismatch"):
            reasons.append("索引哈希失配")
        if clean_detail.get("index_stale"):
            if clean_detail.get("index_rebuilt"):
                reasons.append("索引陈旧，clean_index 自动重建后仍与磁盘实时扫描不一致（疑似重建期间磁盘有并发写入或重建失败）")
            else:
                reasons.append("索引陈旧（clean_index 自动重建未执行或失败）")
        if not clean_detail.get("scanner_aligned"):
            reasons.append("scanner.py 未从 clean_index 派生 latest 或仍存在硬编码快照日期")
        if clean_detail.get("error"):
            reasons.append(clean_detail["error"])
        report["summary"] = "清洗门禁未通过：" + "；".join(reasons) + "。未执行重跑。"
        _write_report(report)
        return report

    # —— Gate 2 · 效力检查成功 ——
    val_ok, val_detail = gate_validity()
    report["gates"]["validity"] = {"passed": val_ok, "detail": val_detail}
    if not val_ok:
        report["action"] = "skipped_gate_fail"
        report["skip_reason"] = "gate_validity_fail"
        reasons = []
        if not val_detail.get("state_file_exists"):
            reasons.append("verification_state.json 缺失或为空")
        if val_detail.get("stale"):
            reasons.append(f"存在 {val_detail['stale']} 条过期/缺失核验（共 {val_detail.get('entries_total',0)} 条）")
        if val_detail.get("error"):
            reasons.append(val_detail["error"])
        report["summary"] = "效力检查门禁未通过：" + "；".join(reasons) + "。未执行重跑。"
        _write_report(report)
        return report

    # —— Gate 3 · 链路契约（脚本 ↔ 数据结构兼容性预检）——
    contract_ok, contract_detail = gate_contract()
    report["gates"]["contract"] = {"passed": contract_ok, "detail": contract_detail}
    if not contract_ok:
        report["action"] = "skipped_gate_fail"
        report["skip_reason"] = "gate_contract_fail"
        report["summary"] = ("链路契约门禁未通过（数据本身无问题，但脚本与表结构不兼容）："
                             + "；".join(f"{v['script']}：{v['issue']}"
                                        for v in contract_detail["violations"])
                             + "。未执行重跑，请先修复脚本后重跑。")
        _write_report(report)
        return report

    # —— Gate 4 · schema 预检（数据文件结构 vs 权威定义，与 Gate 3 互补兜底）——
    schema_ok, schema_detail = gate_schema()
    report["gates"]["schema"] = {"passed": schema_ok, "detail": schema_detail}
    if not schema_ok:
        report["action"] = "skipped_gate_fail"
        report["skip_reason"] = "gate_schema_fail"
        report["summary"] = ("schema 预检门禁未通过（数据文件结构偏离权威定义，链路可能崩溃或静默产出错数）："
                             + "；".join(schema_detail["problems"])
                             + "。未执行重跑，请先修复数据文件后重跑。")
        _write_report(report)
        return report

    # —— 四门禁通过 → 计算签名 + 变更检测 ——
    signature = compute_signature(clean_detail, val_detail)
    report["current_signature"] = signature
    cp = load_checkpoint()
    prev = cp.get("signature")
    report["previous_signature"] = prev
    changed = (signature != prev)
    report["change_detected"] = changed

    if check_only:
        report["action"] = "check_only"
        report["summary"] = "四门禁检查结果：通过。--check-only 模式不执行重跑。"
        _write_report(report)
        return report

    if not changed and not force:
        report["action"] = "skipped_unchanged"
        report["skip_reason"] = "unchanged"
        report["summary"] = "四门禁均通过，但数据签名与上次成功重跑一致，无需重跑（幂等）。"
        _write_report(report)
        return report

    # —— 重跑三步 ——
    stages = []
    for script in ["scanner.py", "build_outputs.py", "report.py"]:
        st = run_stage(script)
        stages.append(st)
        if st["status"] != "success":
            report["stages"] = stages
            report["action"] = "error"
            report["summary"] = f"重跑在 {script} 阶段失败（returncode={st['returncode']}）。"
            _write_report(report)
            return report

    report["stages"] = stages
    report["products"] = collect_products()
    report["stats"] = load_stats()
    report["action"] = "rerun_success"
    report["summary"] = "四门禁通过且检测到数据变化，已重跑 scanner→build_outputs→report，交付物已刷新。"
    save_checkpoint(signature)
    _write_report(report)
    return report


if __name__ == "__main__":
    rep = main()
    print("=" * 64)
    print("检索重跑编排 · 执行摘要")
    print("  action     :", rep.get("action"))
    print("  clean gate :", "PASS" if rep["gates"].get("clean", {}).get("passed") else "FAIL")
    print("  validity   :", "PASS" if rep["gates"].get("validity", {}).get("passed") else "FAIL")
    print("  contract   :", "PASS" if rep["gates"].get("contract", {}).get("passed") else "FAIL")
    print("  schema     :", "PASS" if rep["gates"].get("schema", {}).get("passed") else "FAIL")
    print("  changed    :", rep.get("change_detected"))
    print("  summary    :", rep.get("summary"))
    print("  报告路径   :", REPORT_PATH)
    # F-S01：rc 语义修正——原实现恒 exit 0，四门禁 FAIL 也不可感知。
    # 现：任一门禁 FAIL 或重跑阶段 error → exit 1；否则 exit 0。
    _gates = rep.get("gates") or {}
    _gates_ok = all(bool(g.get("passed")) for g in _gates.values())
    _rc = 0 if (_gates_ok and rep.get("action") != "error") else 1
    print("  exit code  :", _rc)
    print("=" * 64)
    sys.exit(_rc)
