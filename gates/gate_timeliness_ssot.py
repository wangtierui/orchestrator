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
    from config.enums import TIMELINESS_STATUS

    _ENUM = frozenset(TIMELINESS_STATUS)
    _ENUM_ERR = ""
except Exception as e:  # noqa: BLE001
    # N-77（2026-09-28）：**删除静默兜底字面量**。原实现对导入失败退回一份**私有 7 值副本** →
    # 门禁会用一个"可能已漂移的副本"去判定全仓 SSOT 一致性（判据本身失真，且失败无从发现）。
    # 现改为 `run()` 中直接 FAIL（口径：判据不可执行 ≠ 判据通过）。
    _ENUM = frozenset()
    _ENUM_ERR = f"{type(e).__name__}: {e}"


from config.enums import SOURCE_ORDER

# 层1 的匹配/权威性判据**唯一实现**（2026-10-22，TD-21）：
# 原实现在本文件内联，而"回填归属表"的命令需要同一套判据 —— 两份必然分叉。
# 共享后语义不变，但"门禁认为算漂移"与"命令会去改"永远指向同一批行。
from std_lib.common_lib.timeliness_ssot import drift_pairs


def _load_attr_rows():
    with open(_ATTR_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# 说明（2026-10-22，TD-21）：原 `_is_fresh_rec()` 已随层1 判据下沉到
# `std_lib.common_lib.timeliness_ssot._is_fresh()` —— 那里是唯一实现，
# **不要在本文件重建副本**（那正是本次要消除的"两份判据分叉"）。


def run():
    problems = []
    warn = []
    if _ENUM_ERR:
        # N-77（2026-09-28）：受控值 SSOT 不可加载 → **判据不可执行**（原实现退私有字面量副本
        # 继续判定 = 用可能已漂移的副本当尺子量 SSOT，属"失真放行"）。此处直接 FAIL。
        return False, {
            "error": f"无法加载受控值 SSOT config.enums.TIMELINESS_STATUS：{_ENUM_ERR}",
            "note": "SSOT 一致性未实检，不得视为通过",
        }
    if not os.path.exists(_STATE_JSON):
        # F-S09：输入缺失不得空跑放行（原 return True 使"全部门禁通过"含未实检门禁）。
        return False, {
            "error": "verification_state.json 未生成（时效核验尚未运行）；"
            "SSOT 一致性未实检，不得视为通过",
            "state": None,
        }
    state = json.load(open(_STATE_JSON, encoding="utf-8"))

    rows = _load_attr_rows()
    attr_by_rfn = {r.get("监管文件编号", ""): r for r in rows}

    # ── 层1：SSOT→归属表 ──
    # 受控枚举校验仍在此做（它属于"归属表字段合法性"，不是漂移判定）。
    for r in rows:
        st_r = (r.get("时效状态") or "").strip()
        if st_r and st_r not in _ENUM:
            problems.append(
                f"归属表 {r.get('监管文件编号', '')} 时效状态非受控枚举: {st_r!r}")
    # 漂移判定走共享实现（见文件头 import 的说明）。语义与原内联实现逐字一致：
    #   · 匹配：文号归一（≥5 字）优先、标题归一前 40 字兜底；都不中 ⇒ 跳过（未核验）
    #   · 权威性：非 pending/uncertain、未超 90 日、来源不含"无同名命中/规则判断"
    #   · 漂移：归属表非空且与 SSOT 结论不同
    _rep = drift_pairs(rows, state)
    layer1_checked = _rep["checked"]
    layer1_skip = _rep["no_state"]
    for x in _rep["pairs"]:
        problems.append(
            f"SSOT→归属表漂移 {x['rfn']}: state={x['state']} vs 归属表={x['attr']}")

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
                    f"归属表→底座漂移 {rfn} in {fn}: 归属表={a_st or '(空)'} vs base={eff or '(空)'}"
                )

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
            from clean_index import get_clean_index

            ci = get_clean_index()
        except Exception:  # noqa: BLE001
            ci = None
        if ci:
            for src in SOURCE_ORDER:
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
                        if (
                            a_st
                            and cst
                            and a_st != cst
                            and a_st not in ("pending", "uncertain")
                            and cst not in ("pending", "uncertain")
                        ):
                            warn.append(
                                f"归属表→cleaned 提示 {rfn}({src}): 归属表={a_st} vs clean={cst}"
                            )

    # ⚠️ 2026-10-22（TD-21 排查中的实测缺陷）：原实现返回 `problems[:40]`，
    # 而**不返回总条数** —— 于是"到底漂移多少条"无从得知：TD-21 登记为"40 条"
    # 正是这个切片的产物（实测真实层1 漂移是 **44** 条，被切掉了 4 条）。
    # 一个门禁在报告层静默截断，等于让"严重程度"不可观测 —— 修法：
    # ① 显式给出 `problem_count`（**全量**计数，不受切片影响）；
    # ② 切片上限提高到 200（仍保留上限，避免极端情况把 payload 撑爆）；
    # ③ 增加 `problems_truncated`，让"看到的不是全部"这件事本身可辨。
    _MAX_LISTED = 200
    return (not problems), {
        "state_records": len(state),
        "layer1_ssot_to_attr": {"checked": layer1_checked, "no_state_skip": layer1_skip},
        "layer2_attr_to_base": {"checked": layer2_checked},
        "layer3_attr_to_clean_via_bridge": {"checked": bridge_ok, "warn": len(warn)},
        "problem_count": len(problems),
        "problems": problems[:_MAX_LISTED],
        "problems_truncated": len(problems) > _MAX_LISTED,
        "warning_count": len(warn),
        "warnings": warn[:10],
        "warnings_truncated": len(warn) > 10,
    }


if __name__ == "__main__":
    # N-172：单跑入口**统一实现**（原 8 处各自复制同一段）
    from gates import run_cli

    raise SystemExit(run_cli("timeliness_ssot", run))
