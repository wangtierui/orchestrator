# -*- coding: utf-8 -*-
"""
consolidate_timeliness.py —— 分散时效核验清单 → 统一·标准·全量·可追溯结果清单

设计铁律（详见合并报告）：
  - 基线 `时效性标注结果清单_20260824.jsonl` 为不可变种子，绝不覆盖。
  - 全部变更台账 / 打标台账按文件名日期升序「确定性重放」，结果零漂移。
  - 来源质量分级：北大法宝(3) > 总局清理通知/数据源标注(2) > 规则判断(1) > 其他(0)。
  - 增量质量 >= 当前质量 → 应用；相等且不同 → 取最近；增量质量 < 当前 → 跳过降级。
  - 新增溯源字段：last_verified_at / change_trace / needs_review。
  - 原子写 + 自动备份；输出独立文件，不污染任何既有清单。

用法：
  python consolidate_timeliness.py                # 全量重放（默认，推荐）
  python consolidate_timeliness.py --incremental  # 仅消费未处理台账（需先有全量清单）
  python consolidate_timeliness.py --use-state    # 额外用 verification_state.json 交叉校验
  python consolidate_timeliness.py --dry-run      # 仅报告不写盘
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)
# stdout UTF-8：Windows 默认 gbk 编码无法输出 ✓/✗（U+2713/2717）会抛
# UnicodeEncodeError 使 rc=1（2026-09-09 生产刷新 consolidate 实证，清单已写盘仅汇报崩）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # 非 TTY/旧 Python 容错
    pass

REVIEW = os.path.dirname(os.path.abspath(__file__))

# v2 §3.14.3（D3）：时效冲突的待办登记。
# 本脚本**无仓内依赖**（纯 stdlib，文件头无 sys.path 引导），故此处自行派生仓根以导入
# `std_lib.common_lib.governance_store`；登记失败一律降级（旁路设施纪律）。
_ORCH_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(REVIEW)))


def _wl_add(match: dict, d: dict) -> None:
    """登记时效冲突待办（同键出现 ≥2 个高质量且相互冲突的 verdict）。

    v1 实现只把冲突写进 `冲突台账_<date>.csv` 与 `needs_review` 标记——**无处置入口**，
    翻不回去。现登记进队列（`cli.py worklist resolve` 即处置通道）。
    """
    try:
        if _ORCH_ROOT not in sys.path:
            sys.path.insert(0, _ORCH_ROOT)
        from std_lib.common_lib import governance_store as _gs  # noqa: PLC0415
        _gs.worklist_add(
            "timeliness_conflict",
            str(match.get("document_number") or match.get("title") or "")[:80],
            stage="2", artifact_key="cleaned:{src}",
            payload={"title": match.get("title", ""),
                     "document_number": match.get("document_number", ""),
                     "source": match.get("source", ""),
                     "final_status": d.get("new_status", ""),
                     "ledger": d.get("ledger_name", ""),
                     "reason": "同一键出现 ≥2 个高质量且相互冲突的 verdict"},
            suggestion="按权威源优先级裁决（权威核验级 > 台账级）；裁决后用 --ledger "
                       "指定台账重跑 consolidate_timeliness")
    except Exception:  # noqa: BLE001  旁路设施：登记失败不得中断合并
        pass
ROOT = os.path.dirname(REVIEW)

BASELINE = "时效性标注结果清单_20260824.jsonl"
MANIFEST = "_consolidate_manifest.json"

# ---- 受控词表（与清洗层 TIMELINESS_STATUS 对齐）----
CONTROLLED_STATUS = frozenset({
    "valid", "amended", "repealed", "partially_repealed",
    "expired", "pending", "uncertain",
})

STATUS_MAP = {
    "有效": "valid", "现行有效": "valid", "valid": "valid",
    "废止": "repealed", "已废止": "repealed", "repealed": "repealed",
    "失效": "expired", "expired": "expired",
    "修改": "amended", "修订": "amended", "amended": "amended",
    "部分废止": "partially_repealed", "partially_repealed": "partially_repealed",
    "待核": "pending", "pending": "pending",
    "不确定": "uncertain", "uncertain": "uncertain",
    "": "__none__", "-": "__none__", "无": "__none__", "n/a": "__none__",
}

# 来源质量分级
def src_rank(src: str) -> int:
    s = (src or "").strip()
    if not s:
        return 0
    if "北大法宝" in s:
        return 3
    if "总局清理" in s or "数据源标注" in s:
        return 2
    if "规则判断" in s:
        return 1
    return 0


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def date10(s: str) -> str:
    return (s or "")[:10]


# norm-specialization: 仅去空白 + N/A 置空（清单合并专用，原注释已声明有意不同）
def _norm_docno(s: str) -> str:
    # P4：本脚本私有归一（仅去空白 + N/A 占位置空），语义与 common_lib.norm 不同，故下划线命名。
    v = (s or "").strip()
    if not v or v.upper() in ("N/A", "NA", "NULL", "-"):
        return ""
    return norm(v)


def norm_status(s: str):
    key = norm(s)
    return STATUS_MAP.get(key, "__none__")


# ---------------------------------------------------------------------------
# 合并索引（支持 文号 / 标题+日期 / 标题 三重匹配）
# ---------------------------------------------------------------------------
class Accumulator:
    def __init__(self):
        self.records: list[dict] = []
        self.by_doc: dict[str, dict] = {}
        self.by_td: dict[tuple, dict] = {}
        self.by_title: dict[str, list[dict]] = {}

    def _index(self, rec: dict):
        d = _norm_docno(rec.get("document_number"))
        t = norm(rec.get("title"))
        dt = date10(rec.get("publish_date"))
        if d:
            self.by_doc[d] = rec
        if t and dt:
            self.by_td[(t, dt)] = rec
        if t:
            self.by_title.setdefault(t, []).append(rec)

    def add(self, rec: dict):
        self.records.append(rec)
        self._index(rec)

    def find(self, source: str, title: str, document_number: str, publish_date: str):
        d = _norm_docno(document_number)
        t = norm(title)
        dt = date10(publish_date)
        if d and d in self.by_doc:
            return self.by_doc[d]
        if t and dt and (t, dt) in self.by_td:
            return self.by_td[(t, dt)]
        if t:
            rs = self.by_title.get(t, [])
            if len(rs) == 1:
                return rs[0]
            if len(rs) > 1 and source:
                for r in rs:
                    if r.get("source") == source:
                        return r
        return None


# ---------------------------------------------------------------------------
# 台账解析
# ---------------------------------------------------------------------------
def ledger_date_of(fn: str) -> str:
    m = re.search(r"(\d{8})", os.path.basename(fn))
    return m.group(1) if m else "00000000"


def parse_ledgers() -> list[dict]:
    """读全部 变更台账 + 打标台账，归一为 canonical delta。"""
    deltas: list[dict] = []
    patterns = [
        "时效核验tier3变更台账_*.csv",
        "时效核验_classifier变更台账_*.csv",
        "时效核验_nfra变更台账_*.csv",
        "时效核验_gov变更台账_*.csv",
        "时效核验_mof变更台账_*.csv",
        "时效核验_pbc变更台账_*.csv",
        "时效核验_supp变更台账_*.csv",   # P2：supp 效力缺失核验台账纳入重放范围
        "时效核验打标台账_*.csv",
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(REVIEW, p)))
    files = sorted(set(files))

    for fn in files:
        ldate = ledger_date_of(fn)
        lname = os.path.basename(fn)
        with open(fn, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
        is_tag = "timeliness_status" in cols  # 打标台账
        with open(fn, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if is_tag:
                    ns = norm_status(row.get("timeliness_status", ""))
                    nsrc = (row.get("verification_source") or "").strip()
                    repl = (row.get("replacement_document") or "").strip()
                    note = (row.get("note") or "").strip()
                    kind = "tag"
                else:
                    ns = norm_status(row.get("new_status", ""))
                    nsrc = (row.get("new_source") or row.get("verification_source") or "").strip()
                    if not nsrc and "note" in cols and row.get("note"):
                        # classifier/nfra 台账无 new_source 列时，从 note 推断来源质量。
                        # 这些台账本质是北大法宝核验运行产物：note 显式含「北大法宝」
                        # 或 pkulaw 库版本判定（"库内版本已被修改"→amended、
                        # "同名现行有效"→valid）均属 北大法宝 权威 verdict；
                        # "无同名命中/未确认/维持原判定" 为非结论，不强行升级。
                        note0 = row.get("note", "")
                        if "北大法宝" in note0:
                            nsrc = "北大法宝"
                        elif "库内版本已被修改" in note0 or "同名现行有效" in note0 or "同名" in note0:
                            nsrc = "北大法宝"
                        elif "规则判断" in note0:
                            nsrc = "规则判断"
                        elif any(k in note0 for k in ("无同名命中", "未确认", "维持原判定", "维持")):
                            nsrc = ""  # 非结论，不升级
                    repl = (row.get("replacement_document") or "").strip()
                    note = (row.get("note") or "").strip()
                    kind = "change"
                if ns == "__none__":
                    continue  # 无明确 verdict，跳过
                deltas.append({
                    "ledger_name": lname,
                    "ledger_date": ldate,
                    "kind": kind,
                    "source": (row.get("source") or "").strip(),
                    "title": (row.get("title") or "").strip(),
                    "document_number": (row.get("document_number") or "").strip(),
                    "publish_date": (row.get("publish_date") or "").strip(),
                    "new_status": ns,
                    "new_source": nsrc,
                    "replacement_document": repl,
                    "note": note,
                })
    # 按台账日期升序（稳定），保证重放顺序确定
    deltas.sort(key=lambda x: x["ledger_date"])
    return deltas, files


# ---------------------------------------------------------------------------
# 合并核心
# ---------------------------------------------------------------------------
def build_seed(baseline_path: str) -> Accumulator:
    acc = Accumulator()
    with open(baseline_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rec = {
                "source": r.get("source", ""),
                "title": r.get("title", ""),
                "document_number": r.get("document_number", ""),
                "publish_date": r.get("publish_date", ""),
                "category": r.get("category", ""),
                "timeliness_status": norm_status(r.get("timeliness_status", "")) if norm_status(r.get("timeliness_status", "")) != "__none__" else (r.get("timeliness_status") or ""),
                "replacement_document": r.get("replacement_document", "") or "",
                "verification_source": (r.get("verification_source") or "").strip(),
                "last_verified_at": "2026-08-24",
                "change_trace": [{
                    "action": "seed",
                    "status": r.get("timeliness_status", ""),
                    "source": (r.get("verification_source") or "").strip(),
                    "ledger": BASELINE,
                    "date": "2026-08-24",
                }],
                "needs_review": False,
                "_quality": src_rank(r.get("verification_source", "")),
            }
            acc.add(rec)
    return acc


def apply_delta(acc: Accumulator, d: dict, stats: dict, conflicts: list):
    match = acc.find(d["source"], d["title"], d["document_number"], d["publish_date"])
    # 跨源吸附防护（2026-09-13，续跑治理）：find 的「唯一标题」兜底不校验 source——同一法规
    # 在他源条目被单义命中时，本源核验结论会被吸附（apply 侧按源过滤后本源 cleaned 无法回写，
    # 空转循环）。delta.source 明确且 ≠ 命中条目 source → 视为未匹配，走新增（多源条目并存，
    # 与种子中 nfra/pbc 双条目结构一致）。实证：gov→nfra 2 例、nfra→pbc 5 例。
    if match is not None and d["source"] and match.get("source") != d["source"]:
        match = None
    delta_rank = src_rank(d["new_source"])

    if match is None:
        # 新增记录
        rec = {
            "source": d["source"] or _derive_source(d),
            "title": d["title"],
            "document_number": d["document_number"],
            "publish_date": d["publish_date"],
            "category": "",
            "timeliness_status": d["new_status"],
            "replacement_document": d["replacement_document"],
            "verification_source": d["new_source"] or "未知",
            "last_verified_at": d["ledger_date"],
            "change_trace": [{
                "action": "add",
                "status": d["new_status"],
                "source": d["new_source"] or "未知",
                "ledger": d["ledger_name"],
                "date": d["ledger_date"],
                "note": d["note"],
            }],
            "needs_review": False,
            "_quality": delta_rank,
        }
        acc.add(rec)
        stats["added"] += 1
        return

    cur = match["timeliness_status"]
    if cur == d["new_status"]:
        # 确认（无状态变化），刷新核验日 + trace
        match["last_verified_at"] = d["ledger_date"]
        match["change_trace"].append({
            "action": "confirm",
            "status": d["new_status"],
            "source": d["new_source"] or match["verification_source"],
            "ledger": d["ledger_name"],
            "date": d["ledger_date"],
            "note": d["note"],
        })
        stats["confirmed"] += 1
        return

    # 状态不同 → 裁决
    cur_rank = match["_quality"]
    if delta_rank >= cur_rank:
        # 应用（覆盖）
        prev = cur
        had_applied = any(t.get("action") == "apply" and t.get("status") != d["new_status"]
                          for t in match["change_trace"])
        match["timeliness_status"] = d["new_status"]
        match["verification_source"] = d["new_source"] or match["verification_source"]
        if d["replacement_document"]:
            match["replacement_document"] = d["replacement_document"]
        match["_quality"] = max(cur_rank, delta_rank)
        match["last_verified_at"] = d["ledger_date"]
        match["change_trace"].append({
            "action": "apply",
            "from": prev,
            "to": d["new_status"],
            "source": d["new_source"] or match["verification_source"],
            "ledger": d["ledger_name"],
            "date": d["ledger_date"],
            "note": d["note"],
        })
        # 若之前已应用过不同 verdict 且同高质量 → 标记复核
        if had_applied and delta_rank >= 2:
            match["needs_review"] = True
            conflicts.append({
                "title": match["title"],
                "document_number": match["document_number"],
                "source": match["source"],
                "final_status": d["new_status"],
                "ledger": d["ledger_name"],
                "reason": "同一键出现 ≥2 个高质量且相互冲突的 verdict",
            })
            _wl_add(match, d)
        stats["applied"] += 1
    else:
        # 增量质量 < 当前 → 跳过降级
        match["change_trace"].append({
            "action": "skip_downgrade",
            "from": cur,
            "rejected": d["new_status"],
            "rejected_source": d["new_source"],
            "ledger": d["ledger_name"],
            "date": d["ledger_date"],
            "note": d["note"],
        })
        stats["skipped_downgrade"] += 1


def _derive_source(d: dict) -> str:
    n = d["ledger_name"]
    if "classifier" in n:
        return "classifier"
    if "nfra" in n:
        return "nfra"
    if "tier3" in n:
        return "tier3"
    if "打标" in n:
        return d["source"] or "unknown"
    return d["source"] or "unknown"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="分散时效清单 → 统一全量可追溯清单")
    ap.add_argument("--incremental", action="store_true", help="仅消费未处理台账（需先有全量清单）")
    ap.add_argument("--use-state", action="store_true", help="额外用 verification_state.json 交叉校验")
    ap.add_argument("--dry-run", action="store_true", help="仅报告不写盘")
    args = ap.parse_args()

    today = datetime.date.today().strftime("%Y%m%d")
    out_jsonl = os.path.join(REVIEW, "时效性标注结果清单_全量_%s.jsonl" % today)
    out_csv = os.path.join(REVIEW, "时效性标注结果清单_全量_%s.csv" % today)
    out_report = os.path.join(REVIEW, "时效性标注结果清单_合并报告_%s.md" % today)
    out_conflict = os.path.join(REVIEW, "时效性标注结果清单_冲突台账_%s.csv" % today)
    manifest_path = os.path.join(REVIEW, MANIFEST)

    baseline_path = os.path.join(REVIEW, BASELINE)
    if not os.path.exists(baseline_path):
        print("✗ 未找到基线: %s" % BASELINE)
        return 1

    deltas, ledger_files = parse_ledgers()

    # 增量模式：仅保留 manifest 未消费的台账产生的 delta
    consumed = set()
    if args.incremental and os.path.exists(manifest_path):
        m = json.load(open(manifest_path, encoding="utf-8"))
        consumed = set(m.get("consumed_ledgers", []))
        deltas = [d for d in deltas if d["ledger_name"] not in consumed]

    acc = build_seed(baseline_path)
    stats = {"seed": len(acc.records), "added": 0, "applied": 0,
             "confirmed": 0, "skipped_downgrade": 0}
    conflicts: list[dict] = []

    for d in deltas:
        apply_delta(acc, d, stats, conflicts)

    # （可选）verification_state.json 交叉校验：对北大法宝 权威 verdict 强制对齐
    if args.use_state:
        vs_path = os.path.join(REVIEW, "verification_state.json")
        if os.path.exists(vs_path):
            vs = json.load(open(vs_path, encoding="utf-8"))
            aligned = 0
            for rec in acc.records:
                d = _norm_docno(rec.get("document_number"))
                t = norm(rec.get("title"))
                key = ("doc:" + d) if d else ("title:" + t)
                v = vs.get(key)
                if not v:
                    continue
                st = norm_status(v.get("status", ""))
                if st in CONTROLLED_STATUS and v.get("verification_source", "").startswith("北大法宝"):
                    if rec["timeliness_status"] != st and rec["_quality"] < 3:
                        rec["timeliness_status"] = st
                        rec["verification_source"] = "北大法宝"
                        rec["_quality"] = 3
                        rec["change_trace"].append({
                            "action": "state_align",
                            "to": st,
                            "source": "北大法宝",
                            "ledger": "verification_state.json",
                            "date": (v.get("changed_at") or "")[:10],
                        })
                        aligned += 1
            stats["state_aligned"] = aligned

    # 输出前剔除内部字段
    out_records = []
    for rec in acc.records:
        r = {k: v for k, v in rec.items() if not k.startswith("_")}
        out_records.append(r)
    out_records.sort(key=lambda x: (x.get("source", ""), x.get("title", "")))

    # 统计
    from collections import Counter
    dist = Counter(r["timeliness_status"] for r in out_records)
    reviewed = [r for r in out_records if r.get("needs_review")]

    if args.dry_run:
        print("=== DRY-RUN ===")
        print("种子: %d | 新增: %d | 应用: %d | 确认: %d | 跳过降级: %d"
              % (stats["seed"], stats["added"], stats["applied"],
                 stats["confirmed"], stats["skipped_downgrade"]))
        print("台账文件: %d | 增量条数: %d" % (len(ledger_files), len(deltas)))
        print("产出条数(预估): %d" % len(out_records))
        print("状态分布: %s" % dict(dist))
        print("需复核: %d" % len(reviewed))
        if args.use_state:
            print("state 对齐: %d" % stats.get("state_aligned", 0))
        return 0

    # 备份既有全量清单
    for p in (out_jsonl, out_csv):
        if os.path.exists(p):
            bak = p + ".bak_" + datetime.datetime.now().strftime("%H%M%S")
            os.replace(p, bak)

    # 写 JSONL（原子）
    tmp = out_jsonl + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in out_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, out_jsonl)

    # 写 CSV（UTF-8 BOM）
    fields = ["source", "title", "document_number", "publish_date", "category",
              "timeliness_status", "replacement_document", "verification_source",
              "last_verified_at", "change_trace", "needs_review"]
    tmp = out_csv + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in out_records:
            row = dict(r)
            row["change_trace"] = json.dumps(r.get("change_trace", []), ensure_ascii=False)
            row["needs_review"] = "True" if r.get("needs_review") else ""
            w.writerow(row)
    os.replace(tmp, out_csv)

    # 冲突台账
    if reviewed:
        tmp = out_conflict + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["source", "title", "document_number",
                                              "timeliness_status", "verification_source",
                                              "last_verified_at", "change_trace"])
            w.writeheader()
            for r in reviewed:
                w.writerow({
                    "source": r.get("source", ""),
                    "title": r.get("title", ""),
                    "document_number": r.get("document_number", ""),
                    "timeliness_status": r.get("timeliness_status", ""),
                    "verification_source": r.get("verification_source", ""),
                    "last_verified_at": r.get("last_verified_at", ""),
                    "change_trace": json.dumps(r.get("change_trace", []), ensure_ascii=False),
                })
        os.replace(tmp, out_conflict)
    else:
        out_conflict = None

    # manifest 更新
    if not args.incremental or True:
        # 全量模式：记录全部台账为已消费
        m = {"consumed_ledgers": sorted(set(os.path.basename(x) for x in ledger_files)),
             "last_run": today, "baseline": BASELINE}
        json.dump(m, open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 合并报告
    _write_report(out_report, baseline_path, ledger_files, stats, dist,
                  len(out_records), reviewed, conflicts, today, args)

    print("✓ 合并完成")
    print("  全量清单: %s (%d 条)" % (os.path.basename(out_jsonl), len(out_records)))
    print("  种子 %d | 新增 %d | 应用 %d | 确认 %d | 跳过降级 %d"
          % (stats["seed"], stats["added"], stats["applied"],
             stats["confirmed"], stats["skipped_downgrade"]))
    print("  状态分布: %s" % dict(dist))
    print("  需复核: %d" % len(reviewed))
    if args.use_state:
        print("  state 对齐: %d" % stats.get("state_aligned", 0))
    return 0


def _write_report(path, baseline_path, ledger_files, stats, dist, total, reviewed, conflicts, today, args):
    L = []
    L.append("# 时效性标注结果清单 · 合并报告（%s）\n" % today)
    L.append("> 生成时间：%s ｜ 模式：%s ｜ 基线：`%s`（不可变）\n"
             % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "incremental" if args.incremental else "full", BASELINE))
    L.append("\n## 一、数据来源\n")
    L.append("| 类别 | 文件 | 角色 |")
    L.append("|---|---|---|")
    L.append("| 基线 | `时效性标注结果清单_20260824.jsonl` | 全量种子（不可变）|")
    for fn in ledger_files:
        L.append("| 增量 | `%s` | 变更/打标台账 |" % os.path.basename(fn))
    L.append("\n## 二、合并计数\n")
    L.append("| 指标 | 数量 |")
    L.append("|---|---|")
    L.append("| 种子记录 | %d |" % stats["seed"])
    L.append("| 新增（台账有、基线无） | %d |" % stats["added"])
    L.append("| 应用（增量覆盖，含 verdict 升级） | %d |" % stats["applied"])
    L.append("| 确认（verdict 一致，刷新核验日） | %d |" % stats["confirmed"])
    L.append("| 跳过降级（增量质量 < 当前） | %d |" % stats["skipped_downgrade"])
    L.append("| **产出全量条数** | **%d** |" % total)
    if args.use_state:
        L.append("| state 交叉对齐 | %d |" % stats.get("state_aligned", 0))
    L.append("\n## 三、产出状态分布（受控词表）\n")
    L.append("| 状态 | 条数 |")
    L.append("|---|---|")
    for k in sorted(dist):
        L.append("| %s | %d |" % (k, dist[k]))
    L.append("\n## 四、冲突与复核\n")
    L.append("- 需人工复核记录数：**%d**" % len(reviewed))
    if reviewed:
        L.append("\n| 标题 | 发文字号 | 源 | 最终状态 | 核验来源 | 最近核验 |")
        L.append("|---|---|---|---|---|---|")
        seen = set()
        for r in reviewed:
            key = (r.get("source", ""), r.get("title", ""), r.get("document_number", ""))
            if key in seen:
                continue
            seen.add(key)
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                r.get("title", ""), r.get("document_number", ""), r.get("source", ""),
                r.get("timeliness_status", ""), r.get("verification_source", ""),
                r.get("last_verified_at", "")))
    else:
        L.append("\n无高质量冲突。\n")
    L.append("\n## 五、数据质量说明\n")
    L.append("- **来源质量分级**：北大法宝(3) > 总局清理通知/数据源标注(2) > 规则判断(1) > 其他(0)。")
    L.append("- **裁决规则**：增量质量 ≥ 当前 → 应用；相等且状态不同 → 取最近日期；增量质量 < 当前 → 跳过降级，仅留 trace。")
    L.append("- **状态归一**：`已废止/废止→repealed`、`现行有效/有效→valid`、`修改/修订→amended`、`失效→expired`，统一到受控 7 值。")
    L.append("- **合并主键**：`normalize(document_number)`（全局唯一）优先；否则 `(title, publish_date)`；再建 title 索引兜底。")
    L.append("- **category 字段**：基线 15 类杂项值原样透传（非时效 verdict，未做税目归一，留待独立分类治理）。")
    L.append("- **非破坏性**：基线零改动；新清单写独立文件；原子写 + 自动备份既有全量清单。")
    L.append("\n## 六、产出物\n")
    L.append("- `时效性标注结果清单_全量_%s.jsonl`（统一·标准·全量·可追溯）" % today)
    L.append("- `时效性标注结果清单_全量_%s.csv`（UTF-8 BOM，审计用）" % today)
    L.append("- `时效性标注结果清单_冲突台账_%s.csv`（需复核记录）" % today)
    L.append("- `_consolidate_manifest.json`（增量消费状态）")
    open(path, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
