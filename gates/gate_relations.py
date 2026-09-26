# -*- coding: utf-8 -*-
"""gates/gate_relations — 依据/废止关系产物门禁（R-F01，2026-09-14）

背景
----
依据/废止关系已收敛为**唯一事实源** `modules/regulatory_classifier/data/relations/relations_index.jsonl`
（生成器 `tools/extract_relations.py`，文本抽取唯一实现 `std_lib/common_lib/relations.py`），
并承载**三类关系**（监管依据/废止、内部依据/废止、内部→监管依据）。这些关系将被
classifier 报告、base 关联（merged）、drafter 起草素材共同消费——一旦键集/枚举/引用漂移，
下游会静默产出错误结论（与 P7 引用门禁同类风险）。本门禁使该类漂移不可能静默存在。

判据
----
1. **产物缺失 → FAIL**（对齐「输入缺失不得空跑放行」，不静默通过）；
2. 每行键集 **⊇ `interfaces.contract.RELATION_FIELDS`**（允许加字段，缺必报）；
3. **受控枚举闭包**：`relation/src_kind/dst_kind/basis_type/action/scope/matched_by` ∈ `config.enums`；
4. **强引用可解析**：`dst_ref` 非空时——`RFN-*` 须在归属表索引、`IPN-*` 须在内部主索引；
5. **溯源字段非空**：`source_snippet` / `generated_by` / `generated_at`（关系须可回溯到正文出处）；
6. **源侧可识别**：`src_ref` 或 `src_key` 至少一个非空；
7. **统计一致性**：`relations_stat.json` 的 `total` 与 `by_kind` 计数须与实际行数一致；
9. **`relation_id` 唯一性（2026-09-20 追加）**：事实源主键语义成立（行数 == 不同 id 数）。
   动因：旧 id 派生式遗漏判别字段（article/action/scope/reason…）→ 同一 id 命中多行；
   extractor 1.1 起纳入全部判别字段并做确定性唯一化，本判据防其再次静默复发。
8. **产物新鲜度（2026-09-14 追加）**：关系产物**不得早于其数据面输入**
   （五源 cleaned 最新 JSONL / `internal_policy_index.json` / `processed/*_fulltext.json` / 归属表 CSV）。
   **动因**：消费面（merged 引用原语、drafter 关系素材、交付库 2.1.2.4·2.1.2.5 关系报告）
   会随关系产物**静默反映旧数据**；此前只查结构一致性 → 陈旧产物可全额通过、无人察觉。
   **处置**：`python cli.py relations gen`（生产刷新链**阶段 2.6 已自动接入**，正常运营无需手工）。

处置入口
--------
    python cli.py relations gen        # 重新抽取与落盘（含三类视图）
    python cli.py relations status     # 查看生成元信息与解析率
"""

from __future__ import annotations

import json
import os

import paths
from config.enums import (
    BASIS_TYPE,
    RELATION_DOC_KIND,
    RELATION_KIND,
    RELATION_MATCH_METHOD,
    RELATION_TARGET_CLASS,
    REPEAL_ACTION,
    REPEAL_SCOPE,
)

_REL_DIR = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data", "relations")
_INDEX = os.path.join(_REL_DIR, "relations_index.jsonl")
_STAT = os.path.join(_REL_DIR, "relations_stat.json")

_RESERVED_KEYS = {"relation_id"}
_NON_EMPTY = ("source_snippet", "generated_by", "generated_at")


def _load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _enum_ok(v: str, allowed, *, empty_ok: bool) -> bool:
    if v == "" and empty_ok:
        return True
    return v in allowed


# ---------------------------------------------------------------- 新鲜度（判据 8）
# 范围纪律（2026-09-14）：只纳入**由阶段 0~2 或链条外操作**推进的**数据面输入** ——
#   ① 五源 cleaned 最新 JSONL（scrape/clean/时效回写）② `internal_policy_index.json`
#   ③ `processed/*_fulltext.json`（internal index / reocr）④ 归属表 CSV（rfn 登记 / 时效同步）。
# 链条阶段 3~6 的产物（drift / recall / merged_view / reports / published / 交付库）**不写这些文件**，
# 故本断言不会因"自己刚跑完后续阶段"而自造 FAIL。
# 已知取舍：归属表仅"时效状态"列变化也会触发（该列参与实体索引 extra），属**偏严**；
# 代价为一次 `relations gen`（~32s，已入刷新链阶段 2.6），换取"登记后未重抽取"这一真实缺口不被静默放过。
_INPUT_TOLERANCE_S = 2.0  # 容忍秒级写盘先后差（防毫秒抖动误报）


def _data_inputs() -> list[tuple[float, str]]:
    """关系抽取的数据面输入 → [(mtime, 标签)]（不存在的输入自动跳过）。"""
    import glob  # noqa: PLC0415

    scrapers = os.path.join(paths.MODULES_DIR, "regulatory_scrapers")
    ipb = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data")
    cls_data = os.path.join(paths.MODULES_DIR, "regulatory_classifier", "data")

    paths_and_labels: list[tuple[str, str]] = []
    for p in glob.glob(os.path.join(scrapers, "data", "cleaned", "*_cleaned_*.jsonl")):
        paths_and_labels.append((p, os.path.basename(p)))
    paths_and_labels.append(
        (os.path.join(ipb, "internal_policy_index.json"), "internal_policy_index.json")
    )
    for p in glob.glob(os.path.join(ipb, "processed", "*_fulltext.json")):
        paths_and_labels.append((p, "processed/" + os.path.basename(p)))
    paths_and_labels.append(
        (os.path.join(cls_data, "人身保险公司-文件归属表.csv"), "人身保险公司-文件归属表.csv")
    )

    out: list[tuple[float, str]] = []
    for p, label in paths_and_labels:
        try:
            out.append((os.path.getmtime(p), label))
        except OSError:
            continue
    return out


def _stale_inputs(prod_mtime: float) -> list[str]:
    """比产物更新的输入标签（按 mtime 降序）——**阶段 4 起降为交叉校验**。"""
    stale = [(t, lbl) for t, lbl in _data_inputs() if t > prod_mtime + _INPUT_TOLERANCE_S]
    return [lbl for _t, lbl in sorted(stale, key=lambda kv: -kv[0])]


def _wm_status(key: str):
    """产物水位状态（阶段 4：判据切换的共用入口 `interfaces.governance_api.wm_status`）。"""
    try:
        from interfaces.governance_api import wm_status  # noqa: PLC0415

        return wm_status(key)
    except Exception as e:  # noqa: BLE001  水位不可用 → unknown（调用方退回 mtime 判据）
        return "unknown", {"reason": f"{type(e).__name__}: {e}"}


def run():
    if not os.path.exists(_INDEX):
        return False, {
            "error": f"关系事实源不存在：{_INDEX}"
            "；门禁未实检，不得视为通过（先运行 `cli.py relations gen`）"
        }
    try:
        rows = _load_jsonl(_INDEX)
        stat = json.load(open(_STAT, encoding="utf-8")) if os.path.exists(_STAT) else {}
    except (OSError, ValueError) as e:  # noqa: BLE001
        return False, {"error": f"产物不可读：{e!r}"}

    from interfaces.contract import RELATION_FIELDS  # noqa: PLC0415

    required = set(RELATION_FIELDS) - _RESERVED_KEYS
    problems: list[str] = []
    missing_keys: dict[str, int] = {}
    enum_bad: dict[str, int] = {}
    trace_missing = 0
    src_unidentified = 0
    refs: list[tuple[str, str]] = []

    for r in rows:
        miss = required - set(r)
        for k in miss:
            missing_keys[k] = missing_keys.get(k, 0) + 1
        checks = (
            ("relation", r.get("relation", ""), RELATION_KIND, False),
            ("src_kind", r.get("src_kind", ""), RELATION_DOC_KIND, False),
            ("dst_kind", r.get("dst_kind", ""), RELATION_DOC_KIND, False),
            ("basis_type", r.get("basis_type", ""), BASIS_TYPE, True),
            ("action", r.get("action", ""), REPEAL_ACTION, True),
            ("scope", r.get("scope", ""), REPEAL_SCOPE, True),
            ("matched_by", r.get("matched_by", ""), RELATION_MATCH_METHOD, False),
            # 目标性质分类（2026-09-14）：不得缺失/越界——下游据它算"文件级解析率"
            ("dst_class", r.get("dst_class", ""), RELATION_TARGET_CLASS, False),
        )
        for name, val, allowed, empty_ok in checks:
            if not _enum_ok(val, allowed, empty_ok=empty_ok):
                enum_bad[f"{name}={val!r}"] = enum_bad.get(f"{name}={val!r}", 0) + 1
        if any(not (r.get(k) or "").strip() for k in _NON_EMPTY):
            trace_missing += 1
        if not (r.get("src_ref") or r.get("src_key")):
            src_unidentified += 1
        if r.get("dst_ref"):
            refs.append((r["dst_kind"], r["dst_ref"]))

    # 强引用可解析性（RFN↔归属表 / IPN↔内部主索引）
    unresolvable: list[str] = []
    if refs:
        rfns, ipns = set(), set()
        try:
            import sys  # noqa: PLC0415

            for p in (paths.MODULES_DIR, os.path.join(paths.MODULES_DIR, "regulatory_classifier")):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from rfn import get_index  # noqa: PLC0415

            rfns = set(get_index().all_rfns())
        except Exception as e:  # noqa: BLE001
            problems.append(f"归属表索引不可读，强引用无法校验：{e!r}")
        ipb = os.path.join(
            paths.MODULES_DIR, "internal_policy_base", "data", "internal_policy_index.json"
        )
        if os.path.exists(ipb):
            ipns = {x.get("ipn") for x in json.load(open(ipb, encoding="utf-8")).get("records", [])}
        for kind, ref in refs:
            if kind == "regulatory" and rfns and ref not in rfns:
                unresolvable.append(f"{ref}(regulatory)")
            elif kind == "internal" and ipns and ref not in ipns:
                unresolvable.append(f"{ref}(internal)")

    # 统计一致性
    by_kind_actual: dict[str, int] = {}
    for r in rows:
        by_kind_actual[r.get("relation", "")] = by_kind_actual.get(r.get("relation", ""), 0) + 1
    stat_total = (stat.get("relations") or {}).get("total")
    stat_kind = (stat.get("relations") or {}).get("by_kind") or {}
    stat_ok = (stat_total == len(rows)) and all(
        stat_kind.get(k, 0) == v for k, v in by_kind_actual.items()
    )

    if missing_keys:
        problems.append(f"键集缺字段：{dict(sorted(missing_keys.items()))}")
    if enum_bad:
        problems.append(
            f"受控枚举越界：{dict(sorted(enum_bad.items(), key=lambda kv: -kv[1])[:8])}"
        )
    if trace_missing:
        problems.append(
            f"溯源字段为空的关系 {trace_missing} 条（source_snippet/generated_* 须非空）"
        )
    if src_unidentified:
        problems.append(f"源侧不可识别的行 {src_unidentified} 条（src_ref 与 src_key 皆空）")
    # 判据 9（2026-09-20 追加）：**relation_id 唯一性** —— 事实源键语义必须成立。
    # 动因：旧派生式（5 元组）遗漏 article/action/scope/reason 等判别字段 → 5266 行仅 4859 个 id，
    # 迫使治理库改用合成 row_key 主键（行数保全但 id 失真）。extractor 1.1 起已纳入并做确定性
    # 唯一化；本判据使该缺陷**不可能再次静默出现**。
    rid_seen: dict[str, int] = {}
    for r in rows:
        rid = r.get("relation_id") or ""
        rid_seen[rid] = rid_seen.get(rid, 0) + 1
    dup_ids = sorted(x for x, v in rid_seen.items() if v > 1)
    if dup_ids:
        problems.append(
            f"relation_id 不唯一：{len(dup_ids)} 个 id 命中多行"
            f"（示例 {dup_ids[:3]}）—— 关系 id 派生须纳入全部判别字段"
        )
    if unresolvable:
        problems.append(f"强引用不可解析 {len(unresolvable)} 条（示例 {unresolvable[:5]}）")
    if not stat_ok:
        problems.append(
            f"统计不一致：stat.total={stat_total} vs 实际 {len(rows)}，"
            f"by_kind {stat_kind} vs {by_kind_actual}"
        )

    # 判据 8：产物新鲜度（防"数据更新后未重抽取"的静默陈旧，2026-09-14）
    # 动因：消费面（merged 引用原语 / drafter 关系素材 / 交付库 2.1.2.4·2.1.2.5 关系报告）
    # 会随关系产物**静默反映旧数据**；此前本门禁只查结构一致性，陈旧产物可全额通过。
    #
    # 阶段 4（2026-09-18）判据切换：**水位优先，mtime 降为交叉校验**。
    #   ① 水位判 stale → FAIL（零容差、可解释：直接报出是哪条依赖边）；
    #   ② 水位 ok 而 mtime 报 stale → **仅告警**（mtime 受 touch/copy/copy2 干扰，误报率高）；
    #   ③ 水位 unknown（治理库未启用/未登记）→ 退回 mtime 判据并 FAIL（不得因"无水位"放行）。
    stale = _stale_inputs(os.path.getmtime(_INDEX))
    wm_state, wm_detail = _wm_status("relations_index")
    cross_check = {"mtime_stale_inputs": stale[:5], "mtime_stale_count": len(stale)}
    if wm_state == "stale":
        problems.append(
            f"关系产物陈旧（水位判据）：{wm_detail.get('stale')} —— 上游已推进但未重抽取；"
            f"先运行 `python cli.py relations gen`（生产刷新链阶段 2.6 已自动接入）"
        )
    elif wm_state == "unknown":
        if stale:
            problems.append(
                f"关系产物陈旧（mtime 判据；水位不可用：{wm_detail.get('reason')}）："
                f"{len(stale)} 项数据面输入比产物更新（如 {stale[:3]}）—— "
                f"先运行 `python cli.py relations gen`"
            )
        else:
            cross_check["note"] = f"水位不可用（{wm_detail.get('reason')}），已退回 mtime 判据"
    elif stale:
        cross_check["note"] = (
            "水位判据为 ok，mtime 报陈旧 —— 判为 **touch/copy 误报**，"
            "不阻断（阶段 4 起 mtime 仅作交叉校验）"
        )

    detail = {
        "rows": len(rows),
        "contract_fields": len(RELATION_FIELDS),
        "by_kind": by_kind_actual,
        "dst_ref_rows": len(refs),
        "unresolvable_refs": len(unresolvable),
        "stat_consistent": stat_ok,
        # 判据 9：relation_id 唯一性（2026-09-20）
        "relation_id_distinct": len(rid_seen),
        "relation_id_dups": len(dup_ids),
        "extractor_version": stat.get("extractor_version"),
        "resolved_to_entity_ratio": stat.get("resolved_to_entity_ratio"),
        "located_in_corpus_ratio": stat.get("located_in_corpus_ratio"),
        "problems": problems,
        "data_inputs": len(_data_inputs()),
        "stale_inputs": stale,
        "freshness": {
            "watermark": wm_state,
            "watermark_detail": wm_detail,
            "cross_check": cross_check,
        },
        "note": "判据=事实源存在 + 键集⊇契约 + 受控枚举闭包 + 强引用可解析 + 溯源非空 + 统计一致"
        " + relation_id 唯一（2026-09-20）"
        " + 产物新鲜度（阶段 4：水位优先，mtime 降为交叉校验）",
    }
    return (not problems), detail
