# -*- coding: utf-8 -*-
"""
reconcile_clean_drift.py — RFN↔clean 漂移核验与桥表维护（专项评估 v2 §2.3 / Q1 / R7 / R14）

目的：
    五源重清洗可能导致 clean 内某监管文件的 title/document_number 变化。RFN 由
    「文号|标题」派生，若误当新文件注册会产生双 RFN / 错配底座。本脚本建立
    RFN↔clean **溯源桥**（source_url/dedup_key 稳定锚），并在快照推进后检测漂移。

判定规则（以 文号 为主锚，防同名异版误配）：
  - 文号归一一致（同源）→ **同实体**：标题展示差异 = C1（--apply 自动刷新归属表标题，
    RFN 不变，Q2=C1）；标题一致 = ok。
  - 文号不一致但 **既有桥锚**（source_url/dedup_key）在最新 clean 中命中同一条：
    → 跨快照实体疑似变化 = C2（人工确认 supersede，本脚本只列出清单，不自动处理）。
  - 文号不一致、仅冷启动标题命中 → legacy_mismatch（历史遗留同名异版，仅清单提示，不建桥、
    不阻断——首轮基线即存在大量此类）。
  - 完全无法锚定 → absent（外部/行业自律文件不在五源，正常）。

gate_rfn_drift 语义（读 data/rfn_drift_state.json）：
  - state 缺失 → PASS + 提示先跑 reconcile（首次基线）。
  - state.clean_snapshots != 当前 clean_index latest 日期 → FAIL（快照推进未核验，
    阻断下游底座重建，防双 RFN/错配静默入库）。
  - 日期一致 → PASS（reconcile 已尽核验职责；C1/C2 处置是持续治理项，不阻塞）。

产物：
    data/rfn_clean_bridge.csv        桥表（唯一写者=本脚本，R7）
    data/rfn_drift_state.json        最近一次核验状态（供 gate_rfn_drift）
    reports/drift_清单_<date>.csv    C1/C2/legacy/absent 明细（人工复核用）

用法：
    python reconcile_clean_drift.py                # 核验+写桥（不改归属表）
    python reconcile_clean_drift.py --apply        # 自动执行 C1 标题刷新（RFN 不变）
    python reconcile_clean_drift.py --source nfra  # 仅单源核验
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys
from datetime import datetime

csv.field_size_limit(sys.maxsize)  # cleaned body_text 超默认 128KB 字段上限

# ---- 同仓引导（R4：无盘符；模块位于 modules/regulatory_classifier/scripts） ----
_THIS = os.path.dirname(os.path.abspath(__file__))            # modules/regulatory_classifier/scripts
_MOD_CLASS = os.path.dirname(_THIS)                            # modules/regulatory_classifier
_SCRAPERS_MOD = os.path.join(os.path.dirname(_MOD_CLASS), "regulatory_scrapers")
_ORCH_ROOT = os.path.dirname(os.path.dirname(_MOD_CLASS))
for _p in (_MOD_CLASS, _SCRAPERS_MOD, _ORCH_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rfn.bridge import load_bridge, upsert  # noqa: E402

import paths  # noqa: E402

_clean_index = None


def _ci():
    global _clean_index
    if _clean_index is None:
        from clean_index import get_clean_index  # noqa: PLC0415
        _clean_index = get_clean_index()
    return _clean_index


# ---------- 私有归一（语义独立：去全角括号/空白/尾注，供保守比对） ----------
def _norm_docno(d):
    # 2026-09-09 reconcile 漂移治理：书写差异同化后比较骨架「代字+年份+序号」——
    # 「〔2013〕2号」≡「2013年第2号」≡「2013年2号」（去〔〕[]()/空白/第/年/号）。
    # 归属表(如 保监会令〔2013〕2号)与 clean(如 保监会令2013年第2号)因此可正确对齐，
    # 消除大批假 legacy/C2（实测 163 legacy 中多数属同文号异写）。
    return re.sub(r"[第年号]", "", re.sub(r"[〔\[\]（）()〕\s]", "", d or ""))


def _norm_title(t):
    t = re.sub(r"[（(](已废止|已失效|试行|修订)[）)]\s*$", "", (t or "").strip())
    return re.sub(r'[《》"“”\s]', "", t)


# 发文机关称谓同义集（官网标题常带机关前缀，归属表多为精炼名；判核心标题时先剥离前缀）
_ORG_WORDS = ("国家金融监督管理总局", "中国银行保险监督管理委员会", "中国保险监督管理委员会",
              "中国银保监会", "中国保监会", "银保监会", "保监会", "金融监管总局", "人民银行",
              "中国人民银行", "人力资源社会保障部", "财政部", "国家税务总局", "中国证监会",
              "市场监督管理总局", "民政部", "国家发展改革委", "公安部")


def _title_core(t):
    """标题核心词：去书名号/空白/括注/发文机关称谓前缀后的小写串。

    用于「展示差异」判定：同文号下若 core 相等，仅剩机构称谓/括注差异 → C1 可自动
    以官网标题（更完整权威）刷新归属表标题；core 不同 → 核心标题确变 → C2 人工。
    """
    s = (t or "").strip()
    # 去尾部括注（文号式/状态式，如 （已废止）/（…令2018年第2号）/（2024年修订））
    s = re.sub(r"[（(][^（）()]{1,30}(?:令|号|年修订|已废止|已失效|试行|暂行|修订)[^（）()]{0,12}[）)]\s*$", "", s)
    # 去除开头机构称谓
    for w in _ORG_WORDS:
        if s.startswith(w):
            s = s[len(w):]
            break
    s = re.sub(r'[《》"“”\s、，。：:；;,.]', "", s)
    return s.lower()


# ---------- 路径 ----------
_DATA = os.path.join(_MOD_CLASS, "data")
_ATTR = os.path.join(_DATA, "人身保险公司-文件归属表.csv")
_STATE_PATH = os.path.join(_DATA, "rfn_drift_state.json")
_NEEDS_REBUILD = os.path.join(_DATA, "rfn_needs_rebuild.json")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _load_attr_rows():
    with open(_ATTR, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_clean_snapshot(source=None):
    """读五源 latest cleaned（仅取匹配列）→ {source: {doc/title/url 索引}}。"""
    ci = _ci()
    srcs = [source] if source else ("gov", "mof", "nfra", "pbc", "supp")
    snap = {"sources": {}, "dates": {}}
    for s in srcs:
        p = ci.latest_csv_path(s)
        if not p or not os.path.exists(p):
            continue
        m = re.search(r"cleaned_(\d{8})\.csv$", p)
        snap["dates"][s] = m.group(1) if m else ""
        doc_idx = collections.defaultdict(list)
        title_idx = collections.defaultdict(list)
        url_idx = {}
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                row = {"source": s, "title": r.get("title", "") or "",
                       "docno": r.get("document_number", "") or "",
                       "source_url": r.get("source_url", "") or "",
                       "dedup_key": r.get("dedup_key", "") or ""}
                nd, nt = _norm_docno(row["docno"]), _norm_title(row["title"])
                if nd and len(nd) >= 5:
                    doc_idx[nd].append(row)
                if nt:
                    title_idx[nt].append(row)
                if row["source_url"]:
                    url_idx.setdefault(row["source_url"], row)
        snap["sources"][s] = {"doc": doc_idx, "title": title_idx, "url": url_idx}
    return snap


def _match_clean(row, snap, bridge_rows_by_rfn):
    """返回 (clean_row|None, matched_by)。matched_by ∈ {bridge_url,bridge_dedup,docno,title,absent}。"""
    src = row.get("文件来源", "") or ""
    rfn = row.get("监管文件编号", "")
    # L0：既有桥锚（跨快照稳定）
    for br in bridge_rows_by_rfn.get(rfn, []):
        u = br.get("source_url", "")
        dk = br.get("dedup_key", "")
        pool = snap["sources"].get(br.get("文件来源", "") or src) or snap["sources"].get(src)
        if pool is None and snap["sources"]:
            pool = next(iter(snap["sources"].values()))
        if not pool:
            continue
        if u and u in pool["url"]:
            return pool["url"][u], "bridge_url"
        if dk:
            for rows in pool["doc"].values():
                for rr in rows:
                    if rr.get("dedup_key") == dk:
                        return rr, "bridge_dedup"
    # 冷启动：文号优先（同源）
    nd = _norm_docno(row.get("发文字号", ""))
    nt = _norm_title(row.get("文件名称", ""))
    pool = snap["sources"].get(src)
    if pool is None and snap["sources"]:
        pool = next(iter(snap["sources"].values()))
    if not pool:
        return None, "no_snapshot"
    if nd and len(nd) >= 5:
        cands = pool["doc"].get(nd, [])
        if len(cands) == 1:
            return cands[0], "docno"
        if len(cands) > 1 and nt:
            for c in cands:
                if _norm_title(c["title"]) == nt:
                    return c, "docno"
    if nt:
        cands = pool["title"].get(nt, [])
        if len(cands) == 1:
            return cands[0], "title"
    return None, "absent"


def _write_attr_rows(rows):
    fields = list(rows[0].keys())
    tmp = _ATTR + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, _ATTR)


def reconcile(source=None, apply_c1=False, dry_run=False, force_clean_title=False):
    """主流程：写桥 + 漂移判定 +（--apply）C1 核对/刷新。返回 summary dict。"""
    rows = _load_attr_rows()
    bridge = load_bridge()
    by_rfn = collections.defaultdict(list)
    for br in bridge:
        by_rfn[br["监管文件编号"]].append(br)
    snap = load_clean_snapshot(source)

    today = _today()
    stats = collections.Counter()
    c1_list, c2_list, legacy_list, absent_list = [], [], [], []

    for row in rows:
        rfn = row["监管文件编号"]
        cr, matched = _match_clean(row, snap, by_rfn)
        src = row.get("文件来源", "") or (cr or {}).get("source", "")
        if cr is None:
            stats["absent"] += 1
            absent_list.append({"监管文件编号": rfn, "文件名称": row.get("文件名称", ""),
                                "发文字号": row.get("发文字号", ""), "matched_by": matched})
            continue
        sd = _norm_docno(row.get("发文字号", "")) == _norm_docno(cr["docno"])
        core_eq = _title_core(row.get("文件名称", "")) == _title_core(cr["title"])
        full_eq = _norm_title(row.get("文件名称", "")) == _norm_title(cr["title"])
        if sd:
            # 文号一致 = 同实体
            if full_eq:
                stats["ok"] += 1
                if not dry_run:
                    upsert({"监管文件编号": rfn, "文件来源": src, "source_url": cr["source_url"],
                            "dedup_key": cr["dedup_key"], "登记时标题": row.get("文件名称", ""),
                            "登记时文号": row.get("发文字号", ""), "最近确认日期": today,
                            "最近状态": "ok", "relation": "self"})
            elif core_eq:
                # 核心标题一致，仅机构称谓/括注展示差异 → C1（默认核对式：归属表精炼展示
                # 视为权威，经 --apply 核对后 relation=refresh 抑制重复 pending；Q2=C1）
                prev_refreshed = any(b.get("relation") == "refresh" for b in by_rfn.get(rfn, []))
                if prev_refreshed:
                    stats["c1_already_checked"] += 1
                else:
                    stats["C1"] += 1
                    c1_list.append({"监管文件编号": rfn, "文件来源": src, "kind": "C1",
                                    "归属表标题": row.get("文件名称", ""),
                                    "clean标题": cr["title"], "matched_by": matched,
                                    "建议": "C1自动刷新标题（核心标题一致，仅称谓/括注差异）"})
                if not dry_run:
                    upsert({"监管文件编号": rfn, "文件来源": src, "source_url": cr["source_url"],
                            "dedup_key": cr["dedup_key"], "登记时标题": row.get("文件名称", ""),
                            "登记时文号": row.get("发文字号", ""), "最近确认日期": today,
                            "最近状态": "ok", "relation": "refresh"})
            else:
                # 文号同但核心标题不同 → 歧义（同文号多文件/登记错误）→ C2 人工
                stats["C2"] += 1
                c2_list.append({"监管文件编号": rfn, "文件来源": src, "kind": "C2",
                                "归属表标题": row.get("文件名称", ""),
                                "归属表文号": row.get("发文字号", ""),
                                "clean标题": cr["title"], "clean文号": cr["docno"],
                                "matched_by": matched,
                                "建议": "C2人工确认（同文号核心标题不同）"})
            continue
        # 文号不一致：
        if matched in ("bridge_url", "bridge_dedup"):
            # 桥锚命中同一条但文号变化 → 实体换代/URL 复用 → C2 人工
            stats["C2"] += 1
            c2_list.append({"监管文件编号": rfn, "文件来源": src, "kind": "C2",
                            "归属表标题": row.get("文件名称", ""),
                            "归属表文号": row.get("发文字号", ""),
                            "clean标题": cr["title"], "clean文号": cr["docno"],
                            "matched_by": matched,
                            "建议": "C2人工确认（supersede / URL 复用）"})
            if not dry_run:
                upsert({"监管文件编号": rfn, "文件来源": src, "source_url": cr["source_url"],
                        "dedup_key": cr["dedup_key"], "登记时标题": row.get("文件名称", ""),
                        "登记时文号": row.get("发文字号", ""), "最近确认日期": today,
                        "最近状态": "drift_c2", "relation": "supersede"})
        elif matched == "title":
            stats["legacy_mismatch"] += 1
            legacy_list.append({"监管文件编号": rfn, "文件来源": src, "kind": "legacy",
                                "归属表标题": row.get("文件名称", ""),
                                "归属表文号": row.get("发文字号", ""),
                                "clean标题": cr["title"], "clean文号": cr["docno"],
                                "matched_by": matched,
                                "建议": "同名异版，人工复核是否登记新版"})
        else:
            stats["absent"] += 1
            absent_list.append({"监管文件编号": rfn, "文件名称": row.get("文件名称", ""),
                                "发文字号": row.get("发文字号", ""), "matched_by": matched})

    # ---- C1 --apply：处理 C1（默认「核对即完成」：归属表精炼展示为权威，保留；
    #       --force-clean-title 时以官网标题覆盖归属表）。RFN 一律不变（Q2=C1）。 ----
    if apply_c1 and not dry_run:
        if force_clean_title:
            changed = 0
            for d in c1_list:
                for row in rows:
                    if row["监管文件编号"] != d["监管文件编号"]:
                        continue
                    row["文件名称"] = d["clean标题"]
                    row["编号备注"] = (row.get("编号备注", "") or "") + "|C1刷新" + today
                    changed += 1
                    break
            if changed:
                _write_attr_rows(rows)
                try:
                    from rfn.registry import rebuild_index  # noqa: PLC0415
                    rebuild_index()
                except Exception as e:  # noqa: BLE001
                    print(f"[reconcile] WARN 索引重建失败: {e!r}")
            stats["c1_applied_title"] = changed
        else:
            # 核对式：C1 项归属表展示保留（精炼名视为规范表达），仅将桥最近状态回置 ok
            for d in c1_list:
                upsert({"监管文件编号": d["监管文件编号"], "文件来源": d["文件来源"],
                        "最近确认日期": today, "最近状态": "ok", "relation": "refresh"})
            stats["c1_acknowledged"] = len(c1_list)

    # ---- 汇总 + state ----
    summary = {
        "checked_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "attr_total": len(rows),
        "clean_snapshots": snap["dates"],
        "stats": dict(stats),
        "c1_pending": len(c1_list),
        "c2_pending": len(c2_list),
        "legacy_mismatch": len(legacy_list),
        "applied_c1": bool(apply_c1 and not dry_run),
    }
    _save_json(_STATE_PATH, summary)
    all_rows = c1_list + c2_list + legacy_list + absent_list
    cols = sorted({k for r in all_rows for k in r})
    if not dry_run:
        # drift 唯一基准 ledger（批次2/2026-09-08）：data/rfn_drift_ledger.csv 恒为最近一次
        # reconcile 的全量漂移明细（无漂移亦刷新表头），供审计/门禁读唯一态；reports 按日清单为报告留痕。
        data_dir = os.path.dirname(_ATTR)
        led = os.path.join(data_dir, "rfn_drift_ledger.csv")
        os.makedirs(data_dir, exist_ok=True)
        with open(led, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_rows)
        summary["drift_ledger"] = led
        if all_rows:
            out = os.path.join(paths.REPORTS_DIR, f"drift_清单_{today}.csv")
            os.makedirs(paths.REPORTS_DIR, exist_ok=True)
            with open(out, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(all_rows)
            summary["drift_csv"] = out
    return summary


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="RFN↔clean 漂移核验与桥表维护")
    ap.add_argument("--apply", action="store_true", help="自动执行 C1 标题刷新（RFN 不变）")
    ap.add_argument("--source", default=None, help="仅核验单源")
    ap.add_argument("--dry-run", action="store_true", help="演练：不改归属表/桥/清单（仅写 state）")
    ap.add_argument("--force-clean-title", action="store_true",
                    help="以官网标题覆盖归属表标题（默认核对式：保留归属表精炼展示）")
    args = ap.parse_args()
    s = reconcile(source=args.source, apply_c1=args.apply, dry_run=args.dry_run,
                  force_clean_title=args.force_clean_title)
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
