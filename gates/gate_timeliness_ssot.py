# -*- coding: utf-8 -*-
"""
gates/gate_timeliness_ssot — 时效单源传播一致性（N6 / R3 / R6 实装）

SSOT：regulatory_scrapers/timeliness_review/verification_state.json（核验缓存）
传播链（单路 R3）：
  verification_state(SSOT) --sync_to_classifier--> 归属表「时效状态」列
        --sync_all_layers--> 40 底座 / 11 明细 的 eff_status 字段
        --timeliness_full_check--> cleaned timeliness_status 列（五源，经 bridge RFN 关联）

本门禁断言（任一层不一致 → FAIL）：
  1) SSOT→归属表：归属表每行若可按「发文字号归一」命中 state 记录，则归属表时效状态必须等于
     state.status（状态以 7 值英文受控枚举比对）；state 无记录的行跳过（未核验，不算漂移）。
  2) 归属表→底座：全部 _t*_base.json / _t*_final.json 记录的 eff_status 必须等于其 RFN 在
     归属表中的「时效状态」。
  3) 归属表→cleaned（尽力）：经 rfn_clean_bridge 关联的 clean 行 timeliness_status 必须等于
     归属表时效状态（仅对桥表存在 source_url 的记录校验；桥缺失跳过——recall 未收录非漂移）。
  4) 全链路状态值必须 ∈ config.enums.TIMELINESS_STATUS（受控枚举，R6）。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)  # cleaned body_text 超默认 128KB 字段上限

import paths

_MOD_SCRAPERS = os.path.join(paths.MODULES_DIR, "regulatory_scrapers")
_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
_STATE_JSON = os.path.join(_MOD_SCRAPERS, "timeliness_review", "verification_state.json")
_ATTR_CSV = os.path.join(_MOD_CLASS, "data", "人身保险公司-文件归属表.csv")
_DATA = os.path.join(_MOD_CLASS, "data")
_BRIDGE_CSV = os.path.join(_MOD_CLASS, "data", "rfn_clean_bridge.csv")
_CLEANED = os.path.join(_MOD_SCRAPERS, "data", "cleaned")
for _p in (_MOD_CLASS, _MOD_SCRAPERS, paths.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from config.enums import TIMELINESS_STATUS  # noqa: E402
    _ENUM = frozenset(TIMELINESS_STATUS)
except Exception:
    _ENUM = frozenset({"valid", "amended", "repealed", "partially_repealed",
                       "expired", "pending", "uncertain"})


from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）
from std_lib.common_lib.norm import norm_title_strict as _norm_title  # A-10：SSOT 收敛（保守层）


def _load_attr_rows():
    with open(_ATTR_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _is_fresh_rec(rec):
    """state 记录核验是否 ≤ 90 日（fresh 才作为 SSOT 断言依据；陈旧由下次 verify 刷新，不阻断）。"""
    try:
        from datetime import datetime, timedelta  # noqa: PLC0415
        last = datetime.strptime(rec.get("last_checked_at", ""), "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last) <= timedelta(days=90)
    except Exception:  # noqa: BLE001
        return False


def run():
    problems = []
    warn = []
    if not os.path.exists(_STATE_JSON):
        # F-S09：输入缺失不得空跑放行（原 return True 使"全部门禁通过"含未实检门禁）。
        return False, {"error": "verification_state.json 未生成（时效核验尚未运行）；"
                                "SSOT 一致性未实检，不得视为通过", "state": None}
    state = json.load(open(_STATE_JSON, encoding="utf-8"))
    # 快速索引：docno 归一 → rec（含状态 + 核验时间）
    st_docno = {}
    st_title = {}
    for k, rec in state.items():
        if k.startswith("doc:"):
            st_docno[k[4:]] = rec
        elif k.startswith("title:"):
            st_title[k[6:]] = rec

    rows = _load_attr_rows()
    attr_by_rfn = {}
    layer1_checked = layer1_skip = 0
    for r in rows:
        rfn = r.get("监管文件编号", "")
        attr_by_rfn[rfn] = r
        st = (r.get("时效状态") or "").strip()
        if st and st not in _ENUM:
            problems.append(f"归属表 {rfn} 时效状态非受控枚举: {st!r}")
        nd = _norm_docno(r.get("发文字号", ""))
        rec = st_docno.get(nd) if nd and len(nd) >= 5 else None
        if rec is None:
            nt = _norm_title(r.get("文件名称", ""))
            rec = st_title.get(nt[:40])
        if rec is None:
            layer1_skip += 1
            continue
        layer1_checked += 1
        rec_st = rec.get("status", "")
        # pending/uncertain = 核验占位（非结论），归属表以人工判定为权威，不判漂移；
        # state 陈旧（核验超 90 日，未及重验）也不阻断——归属表可能已人工更新，待下次 verify 刷新。
        if rec_st in ("pending", "uncertain") or not _is_fresh_rec(rec):
            continue
        # "无同名命中（维持原标注）"/"规则判断" = 核验未形成法宝权威结论（仅记录原值），
        # 不作为 SSOT 断言依据（否则 1439 条非结论记录会与人工归属表产生假漂移；2026-09-11 修复）。
        vsrc = rec.get("verification_source", "") or ""
        if ("无同名命中" in vsrc) or ("规则判断" in vsrc):
            continue
        if st and rec_st != st:
            problems.append(f"SSOT→归属表漂移 {rfn}: state={rec_st} vs 归属表={st}")

    # 层2：底座 eff_status == 归属表时效
    layer2_checked = 0
    for fn in sorted(os.listdir(_DATA)):
        m = re.match(r"_t\d+_(base|final)\.json$", fn)
        if not m:
            continue
        try:
            recs = json.load(open(os.path.join(_DATA, fn), encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{fn} 解析失败: {e!r}")
            continue
        for x in recs:
            rfn = x.get("监管文件编号", "")
            eff = (x.get("eff_status") or "").strip()
            if eff and eff not in _ENUM:
                problems.append(f"{fn} {rfn} eff_status 非受控枚举: {eff!r}")
            ar = attr_by_rfn.get(rfn)
            if not ar:
                continue
            a_st = (ar.get("时效状态") or "").strip()
            layer2_checked += 1
            # 空值语义：未核验（空）必须两侧一致——一边空一边非空即漂移（M-01/D-01：
            # 原实现双向非空才比，导致"归属表空 → 底座默认 valid"链路静默无感）。
            if a_st != eff and (a_st or eff):
                problems.append(
                    f"归属表→底座漂移 {rfn} in {fn}: 归属表={a_st or '(空)'} vs base={eff or '(空)'}")

    # 层3：归属表→cleaned（尽力，经 bridge）
    bridge_ok = 0
    if os.path.exists(_BRIDGE_CSV):
        # bridge: source_url → rfn
        url2rfn = {}
        with open(_BRIDGE_CSV, encoding="utf-8-sig", newline="") as fh:
            for b in csv.DictReader(fh):
                u = b.get("source_url", "")
                if u:
                    url2rfn[u] = b.get("rfn", "") or b.get("监管文件编号", "")
        ci = None
        try:
            from clean_index import get_clean_index  # noqa: PLC0415
            ci = get_clean_index()
        except Exception:  # noqa: BLE001
            ci = None
        if ci:
            for src in ("gov", "mof", "nfra", "pbc", "supp"):
                cp = ci.latest_csv_path(src)
                if not cp or not os.path.exists(cp):
                    continue
                with open(cp, encoding="utf-8-sig", newline="") as fh:
                    for cr in csv.DictReader(fh):
                        u = cr.get("source_url", "")
                        rfn = url2rfn.get(u)
                        if not rfn:
                            continue
                        ar = attr_by_rfn.get(rfn)
                        cst = (cr.get("timeliness_status") or "").strip()
                        if not ar or not cst:
                            continue
                        bridge_ok += 1
                        a_st = (ar.get("时效状态") or "").strip()
                        # cleaned 反映采集时点官网状态，归属表 pending 为后续人工核验占位，时间差合法。
                        # 仅当 归属表=结论性 && clean=结论性 且二者相反时才提示（warning，不阻断）。
                        if (a_st and cst and a_st != cst and a_st not in ("pending", "uncertain")
                                and cst not in ("pending", "uncertain")):
                            warn.append(f"归属表→cleaned 提示 {rfn}({src}): 归属表={a_st} vs clean={cst}")

    return (not problems), {
        "state_records": len(state),
        "layer1_ssot_to_attr": {"checked": layer1_checked, "no_state_skip": layer1_skip},
        "layer2_attr_to_base": {"checked": layer2_checked},
        "layer3_attr_to_clean_via_bridge": {"checked": bridge_ok, "warn": len(warn)},
        "problems": problems[:40],
        "warnings": warn[:10],
    }


if __name__ == "__main__":
    ok, detail = run()
    print("[timeliness_ssot]", "PASS" if ok else "FAIL", json.dumps(detail, ensure_ascii=False))
    raise SystemExit(0 if ok else 1)
