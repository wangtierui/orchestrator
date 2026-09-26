# -*- coding: utf-8 -*-
"""tools/governance_sync — 事实源 → 治理库「元数据投影器」（阶段 2，2026-09-18）

定位（`reports/数据流转与存储交互优化方案_20260917.md` §6 阶段 2）
----------------------------------------------------------------
把四类**元数据**从各自的事实源文件事务化投影进 `data/governance.db`，
进入「**双写期**」：

    事实源文件（权威，读方不变）  ──投影──▶  治理库四表（事务化 / 可 SQL 查 / 带历史）

双写期纪律（本工具即其实现）
--------------------------
1. **文件仍是唯一事实源**，读方继续读文件；本工具**只写库**，绝不回写文件；
2. **比对断言**：每次投影后（或 `--check` 单独执行）以 `SYNC_TABLES` 声明的**断言键**
   重算「库内摘要 vs 文件摘要」，不一致即判分叉（`verify` 退出码 1）；
3. 四表中三张**全量替换**（不留孤儿行）、`timeliness_history` **追加去重**（保留历史观测）；
4. 文件缺失 → **跳过该表并报告**（不写空表，避免"库把缺失当成空"）。

投影映射
--------
| 表 | 事实源文件 | 断言键 |
|---|---|---|
| `document`(regulatory) | classifier `data/人身保险公司-文件归属表.csv` | doc_ref/kind/title/docno/timeliness_status/source/theme |
| `document`(internal) | ipb `data/internal_policy_index.json` | 同上 |
| `theme_assign` | classifier `data/人身保险公司-主题归属表.csv` | doc_ref/theme |
| `relation` | classifier `data/relations/relations_index.jsonl` | relation_id/relation/…/matched_by |
| `timeliness_history` | scrapers `timeliness_review/verification_state.json` | state_key/status/checked_at |

用法
----
    python tools/governance_sync.py --check      # 只比对不写库（退出码 0/1）
    python tools/governance_sync.py --apply      # 投影（写库）+ 比对
    python tools/governance_sync.py --apply --export   # 另导出 exports/ 文本快照
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_THIS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config.exitcodes import ExitCode  # noqa: E402
from std_lib.common_lib import governance_store as gs  # noqa: E402

# 归一化 SSOT（gate_no_duplicate_libs：业务仓禁止本地 def 归一化）
from std_lib.common_lib.norm import norm_docno  # noqa: E402

CLS_DATA = os.path.join(ROOT, "modules", "regulatory_classifier", "data")
IPB_DATA = os.path.join(ROOT, "modules", "internal_policy_base", "data")
REVIEW = os.path.join(ROOT, "modules", "regulatory_scrapers", "timeliness_review")

ATTR_CSV = os.path.join(CLS_DATA, "人身保险公司-文件归属表.csv")
THEME_CSV = os.path.join(CLS_DATA, "人身保险公司-主题归属表.csv")
IPB_INDEX = os.path.join(IPB_DATA, "internal_policy_index.json")
REL_INDEX = os.path.join(CLS_DATA, "relations", "relations_index.jsonl")
TL_STATE = os.path.join(REVIEW, "verification_state.json")

# 归属表列名（中文列，契约见 interfaces/contract.REGISTRY_CSV_FIELDS）
_C_REF, _C_TITLE, _C_DOCNO = "监管文件编号", "文件名称", "发文字号"
_C_PUB, _C_SRC, _C_TL = "发布日期", "文件来源", "时效状态"
_C_NOTE = "编号备注"


# v2 §3.6 X3（P1-5，2026-09-26）：状态行改经统一日志设施（原 print）。
# 规则：**状态/进度/告警行 → LOG**；**机器可读载荷（json.dumps）/多列表格行 → 保留 print**
# （后者是 stdout 契约，加日志前缀会破坏编排器 tail 与下游解析）。
# 判据：`gate_runtime_hygiene` 判据④断言本文件的 LOG 使用下限与 print 上限。
from std_lib.common_lib.logging import get_logger  # noqa: E402

LOG = get_logger("governance_sync")


def _rows_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _rows_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def _nd(v: str) -> str:
    """归一化文号（经 std_lib.common_lib.norm 唯一实现；本模块不重复实现归一化）。"""
    try:
        return norm_docno(v or "")
    except Exception:  # noqa: BLE001
        return (v or "").strip()


# --------------------------------------------------------------------------- #
# 源侧装载（返回 (rows, notes)；文件缺失 → 空表 + note）
# --------------------------------------------------------------------------- #
def load_documents() -> tuple[list[dict], list[str]]:
    """监管（归属表） + 内部（主索引）→ document 行。"""
    notes, out = [], []
    attr = _rows_csv(ATTR_CSV)
    if not attr:
        notes.append(f"跳过 document(regulatory)：{os.path.relpath(ATTR_CSV, ROOT)} 缺失")
    for r in attr:
        ref = (r.get(_C_REF) or "").strip()
        if not ref:
            continue
        docno = (r.get(_C_DOCNO) or "").strip()
        out.append(
            {
                "doc_ref": ref,
                "kind": "regulatory",
                "title": (r.get(_C_TITLE) or "").strip(),
                "docno": docno,
                "docno_norm": _nd(docno),
                "source": (r.get(_C_SRC) or "").strip(),
                "publish_date": (r.get(_C_PUB) or "").strip(),
                "timeliness_status": (r.get(_C_TL) or "").strip(),
                "row_json": json.dumps(r, ensure_ascii=False),
            }
        )
    idx = {}
    if os.path.exists(IPB_INDEX):
        try:
            idx = json.load(open(IPB_INDEX, encoding="utf-8"))
        except (OSError, ValueError) as e:
            notes.append(f"跳过 document(internal)：主索引解析失败 {e!r}")
    else:
        notes.append(f"跳过 document(internal)：{os.path.relpath(IPB_INDEX, ROOT)} 缺失")
    recs = idx.get("records") if isinstance(idx, dict) else None
    for rec in recs or []:
        ipn = (rec.get("ipn") or "").strip()
        if not ipn:
            continue
        out.append(
            {
                "doc_ref": ipn,
                "kind": "internal",
                "title": (rec.get("title") or "").strip(),
                "docno": (rec.get("docno") or "").strip(),
                "docno_norm": _nd(rec.get("docno") or ""),
                "source": "internal",
                "publish_date": (rec.get("issue_date") or "").strip(),
                "timeliness_status": (rec.get("status") or "").strip(),
                "theme": (rec.get("primary_theme") or "").strip(),
                "file_type": (rec.get("file_type") or "").strip(),
                "extension": (rec.get("extension") or "").strip(),
                "origin_path": (rec.get("relative_path") or "").strip(),
                "article_count": rec.get("article_count"),
                "row_json": json.dumps(rec, ensure_ascii=False),
            }
        )
    return out, notes


def load_theme_assigns() -> tuple[list[dict], list[str]]:
    rows = _rows_csv(THEME_CSV)
    if not rows:
        return [], [f"跳过 theme_assign：{os.path.relpath(THEME_CSV, ROOT)} 缺失"]
    out = []
    for r in rows:
        ref = (r.get(_C_REF) or "").strip()
        if not ref:
            continue
        out.append(
            {
                "doc_ref": ref,
                "theme": (r.get("主题") or "").strip(),
                "basis": (r.get("判定依据") or "").strip(),
                "decided_at": "",
            }
        )
    return out, []


def load_relations() -> tuple[list[dict], list[str]]:
    rows = _rows_jsonl(REL_INDEX)
    if not rows:
        return [], [f"跳过 relation：{os.path.relpath(REL_INDEX, ROOT)} 缺失"]
    out = []
    dup_ids = set()
    seen_ids = set()
    occ: dict[str, int] = {}
    exact_dup = 0
    for r in rows:
        rid = (r.get("relation_id") or "").strip()
        if not rid:
            continue
        if rid in seen_ids:
            dup_ids.add(rid)
        seen_ids.add(rid)
        rj = json.dumps(r, ensure_ascii=False, sort_keys=True)
        # 合成行键：以 `sha256(relation_id|row_json|序次)[:16]` 作主键 —— **确定性且保全全部行**。
        # 历史动因（2026-09-18）：`relation_id` 当时**不唯一**（同 id 多行、内容互不相同），
        # 以其作主键会静默丢行。2026-09-20 抽取侧已修（extractor 1.1：id 纳入全部判别字段
        # + 写入前确定性唯一化，`gate_relations` 判据 9 断言），此处**保留合成键作纵深防御**
        # （兼防逐字节重复行互相覆盖，保证「库行数 == 事实源行数」）。
        base = f"{rid}|{rj}"
        n = occ.get(base, 0)
        occ[base] = n + 1
        if n:
            exact_dup += 1
        row_key = hashlib.sha256(f"{base}|{n}".encode()).hexdigest()[:16]
        conf = r.get("confidence")
        try:
            conf = float(conf) if conf not in (None, "") else None
        except (TypeError, ValueError):
            conf = None
        out.append(
            {
                "row_key": row_key,
                "relation_id": rid,
                "relation": (r.get("relation") or ""),
                "src_kind": r.get("src_kind") or "",
                "src_ref": r.get("src_ref") or "",
                "src_key": r.get("src_key") or "",
                "src_name": r.get("src_name") or "",
                "src_docno": r.get("src_docno") or "",
                "src_source": r.get("src_source") or "",
                "dst_kind": r.get("dst_kind") or "",
                "dst_ref": r.get("dst_ref") or "",
                "dst_key": r.get("dst_key") or "",
                "dst_class": r.get("dst_class") or "",
                "dst_name": r.get("dst_name") or "",
                "basis_type": r.get("basis_type") or "",
                "action": r.get("action") or "",
                "scope": r.get("scope") or "",
                "matched_by": r.get("matched_by") or "",
                "confidence": conf,
                "generated_at": r.get("generated_at") or "",
                "row_json": json.dumps(r, ensure_ascii=False),
            }
        )
    if dup_ids or exact_dup:
        LOG.info(
            f"[sync] ⚠ relations_index.jsonl 去重键异常：{len(rows)} 行 / "
            f"{len(seen_ids)} 个不同 relation_id；其中 {len(dup_ids)} 个 id 重复、"
            f"{exact_dup} 行为逐字节重复。已按合成 row_key **保全全部 {len(out)} 行**"
            "（库行数与事实源一致）。id 重复在 extractor 1.1 后不应出现 → "
            "请运行 `python cli.py relations gen` 重抽取（gate_relations 判据 9 亦会阻断）"
        )
    return out, []


def load_timeliness() -> tuple[list[dict], list[str]]:
    if not os.path.exists(TL_STATE):
        return [], [f"跳过 timeliness_history：{os.path.relpath(TL_STATE, ROOT)} 缺失"]
    try:
        data = json.load(open(TL_STATE, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [f"跳过 timeliness_history：状态文件解析失败 {e!r}"]
    # 实测（2026-09-18）：verification_state.json 的**顶层即状态字典**（键为
    # `doc:<归一化文号>` / `title:<标题前40字>`），并带 `_meta` 等元键。
    # 故此处不能按 {"states": {...}} 嵌套形态读取；`_` 前缀键一律跳过（非状态记录）。
    states = data.get("states") if isinstance(data, dict) else None
    rows = states if isinstance(states, dict) else data
    out = []
    for key, rec in (rows or {}).items():
        if str(key).startswith("_") or not isinstance(rec, dict):
            continue
        out.append(
            {
                "state_key": key,
                "status": rec.get("status") or "",
                "prev_status": rec.get("prev_status") or "",
                "replacement": rec.get("replacement_document") or rec.get("replacement") or "",
                "verification_source": rec.get("verification_source") or "",
                "last_checked_at": rec.get("last_checked_at") or "",
                "changed_at": rec.get("changed_at") or "",
            }
        )
    return out, []


def collect() -> tuple[dict, list[str]]:
    """装载全部四表源数据。返回 (payload, notes)。"""
    docs, n1 = load_documents()
    themes, n2 = load_theme_assigns()
    rels, n3 = load_relations()
    tls, n4 = load_timeliness()
    return (
        {"documents": docs, "theme_assigns": themes, "relations": rels, "timeliness_rows": tls},
        n1 + n2 + n3 + n4,
    )


def _print_result(result: dict) -> int:
    bad = 0
    print(f"{'table':<20}{'mode':<9}{'db':>7}{'src':>7}  {'db_sha16':<18}{'src_sha16':<18}status")
    for t, d in result.items():
        flag = (
            "OK"
            if d["ok"]
            else (
                f"MISMATCH(missing={d.get('missing')})" if d.get("mode") == "append" else "MISMATCH"
            )
        )
        if not d["ok"]:
            bad += 1
        print(
            f"{t:<20}{d.get('mode', ''):<9}{d['db_rows']:>7}{d['src_rows']:>7}  "
            f"{d['db'][:16]:<18}{d['src'][:16]:<18}{flag}"
        )
    return bad


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="事实源 → 治理库元数据投影器（阶段 2）")
    ap.add_argument("--apply", action="store_true", help="投影写库（默认仅比对）")
    ap.add_argument("--check", action="store_true", help="比对断言（--apply 时自动附带）")
    ap.add_argument("--export", action="store_true", help="另导出 exports/ 文本快照")
    ap.add_argument(
        "--require-db", action="store_true", help="治理库不存在时报错退出（默认自动建库）"
    )
    args = ap.parse_args(argv)

    if not gs.enabled() and args.require_db:
        LOG.info("[sync] 治理库不存在（先 `python cli.py governance init`）")
        return ExitCode.DATA
    gs.init_db()

    payload, notes = collect()
    for n in notes:
        LOG.info(f"[sync] {n}")
    LOG.info("[sync] 源侧装载：" + " / ".join(f"{k}={len(v)}" for k, v in payload.items()))

    if args.apply:
        counts = gs.project_metadata(
            documents=payload["documents"],
            theme_assigns=payload["theme_assigns"],
            relations=payload["relations"],
            timeliness_rows=payload["timeliness_rows"],
        )
        LOG.info("[sync] 已投影：" + " / ".join(f"{k}={v}" for k, v in counts.items()))

    if not gs.enabled():
        LOG.info("[sync] 治理库不可用，跳过比对断言")
        return ExitCode.DATA
    result = gs.verify_projection(**payload)
    bad = _print_result(result)
    if args.export:
        out = gs.export_snapshot(os.path.join(ROOT, "exports"))
        LOG.info(
            f"[sync] 已导出快照：{out.get('out_dir')}"
            f"（{len(out.get('items') or [])} 张表；relation 不导出——已有事实源文件）"
        )
    LOG.info("[sync] 比对断言：" + ("全部一致" if bad == 0 else f"{bad} 张表分叉"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
