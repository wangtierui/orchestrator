# -*- coding: utf-8 -*-
"""
gates/gate_rfn_sync — RFN 跨文件一致性（由旧 classifier scripts/check_rfn_sync 实装，P4 适配现行结构）

校验"归属表（权威）→ 下游"的 RFN 与发文字号双字段一致性：
  下游 = 数据底座 final.json（T1–T10）× 逐份明细表（T0–T10，11 份）× rfn 索引

适配说明（2026-08-31 重构后结构，gate 按此校验）：
  - RFN = "RFN-<16hex>"（RFN_PAT，rfn 包单源）；无「同文件主编号」列；
  - 归属表仅 8 列，主题唯一来源＝主题归属表（本 gate 经 rfn.get_index 合并「主题」列读取）；
  - final.json 元素含 监管文件编号/doc_no（doc_no 为清洗正文用文号字段，须与归属表发文字号等价）；
  - 明细表 11 份：T0_9…T10（含发文字号列）。

口径：
  - 空值/占位符（''/'—'/'-'/N/A）视作文号等价，避免假阳性；
  - KNOWN_LEGACY_RFNS（历史治理接受）差异仅透明报告，不计失败；
  - 仅校验+报告，不自动写回（回写须经 rfn_api，见 P5）。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys

import paths

_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
_DATA = os.path.join(_MOD_CLASS, "data")
_ATTR = os.path.join(_DATA, "人身保险公司-文件归属表.csv")
_IDX = os.path.join(_MOD_CLASS, "rfn", "监管文件编号索引.csv")
_ORCH_ROOT = paths.ROOT
for _p in (_ORCH_ROOT, _MOD_CLASS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rfn import RFN_PAT, get_index  # noqa: E402

# 历史治理接受白名单（归属表↔明细表/final 发文字号失同步，透明报告不计失败）
KNOWN_LEGACY_RFNS = {
    "RFN-ddf8280065cda015",  # 明细表: 银保监办发〔2020〕（公布编号） vs 归属表空
    "RFN-852c15ce86dd9c4d",  # 明细表: 中国保险行业协会/中国医师协会 20 vs 归属表空
    "RFN-0a0662ee669a6523",  # 明细表: 财会字〔1997〕21号 vs 归属表空
}

_BLANK = {"", "—", "-", "N/A", "n/a"}
_THEME_CODE_RE = re.compile(r"^T(?:10|[0-9])")
_DET_RE = re.compile(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$")


def _norm_doc(d):
    return re.sub(r"[〔\[\]（）()〕\s]", "", d or "").rstrip("号")


def _doc_equal(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if a in _BLANK or b in _BLANK:
        return (a in _BLANK) and (b in _BLANK)
    return _norm_doc(a) == _norm_doc(b)


def _theme_code(row):
    m = _THEME_CODE_RE.match((row.get("主题") or "").strip())
    return m.group(0) if m else None


def run():
    problems, legacy = [], []
    idx = get_index(_ATTR)  # rfn 单例：归属表 + 主题归属表合并
    rows = idx.rows()
    all_rfns = set()
    for r in rows:
        rfn = r.get("监管文件编号", "")
        if rfn in all_rfns:
            problems.append(f"归属表 RFN 重复: {rfn}")
        if rfn and not RFN_PAT.match(rfn):
            problems.append(f"归属表 RFN 格式非法: {rfn!r}")
        all_rfns.add(rfn)

    def _chk(loc, rfn, rec_doc, auth_row):
        if not rfn or rfn not in all_rfns:
            problems.append(f"{loc} {rfn or '<空>'} RFN 不在归属表")
            return
        if not _doc_equal(rec_doc, auth_row.get("发文字号", "")):
            msg = f"{rfn} 文号 {str(rec_doc)[:16]!r} != 归属表 {str(auth_row.get('发文字号',''))[:16]!r}"
            (legacy if rfn in KNOWN_LEGACY_RFNS else problems).append(f"{loc} {msg}")

    authority = {r["监管文件编号"]: r for r in rows}
    # ---- final.json（_t{n}_final.json，list） ----
    for t in range(1, 11):
        fp = os.path.join(_DATA, f"_t{t}_final.json")
        if not os.path.exists(fp):
            problems.append(f"_t{t}_final.json 缺失")
            continue
        try:
            recs = json.load(open(fp, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"_t{t}_final.json 解析失败: {e!r}")
            continue
        for rec in recs:
            rfn = rec.get("监管文件编号", "")
            _chk(f"T{t} final", rfn, rec.get("doc_no", ""), authority.get(rfn, {}))
            if rfn in authority:
                tc = _theme_code(authority[rfn])
                if tc and tc != f"T{t}":
                    problems.append(f"T{t} final {rfn} 归属表主题为 {tc}，不归入 T{t}")

    # ---- 明细表（11 份） ----
    dets = sorted(f for f in os.listdir(_DATA) if _DET_RE.match(f))
    if len(dets) != 11:
        problems.append(f"明细表数量 {len(dets)} ≠ 11: {dets}")
    for f in dets:
        m = re.match(r"^T(\d+)_", f)
        tkey = f"T{m.group(1)}"
        with open(os.path.join(_DATA, f), encoding="utf-8-sig", newline="") as fh:
            drows = list(csv.DictReader(fh))
        for dr in drows:
            rfn = dr.get("监管文件编号", "")
            _chk(f"{tkey} 明细 {f}", rfn, dr.get("发文字号", ""), authority.get(rfn, {}))
            if rfn in authority:
                tc = _theme_code(authority[rfn])
                if tc and tc != tkey:
                    problems.append(f"{f} {rfn} 归属表主题为 {tc}，不归入 {tkey}")

    # ---- 索引 ----
    if os.path.exists(_IDX):
        with open(_IDX, encoding="utf-8-sig", newline="") as fh:
            idx_rows = list(csv.DictReader(fh))
        idx_rfns = {x.get("监管文件编号", "") for x in idx_rows}
        if idx_rfns != all_rfns:
            problems.append("索引 RFN 集与归属表不一致")
    else:
        problems.append(f"索引文件缺失: {_IDX}")

    detail = {
        "attr_rows": len(rows),
        "final_checked": sum(
            len(json.load(open(os.path.join(_DATA, f"_t{t}_final.json"), encoding="utf-8")))
            for t in range(1, 11) if os.path.exists(os.path.join(_DATA, f"_t{t}_final.json"))),
        "detail_tables": len(dets),
        "problems": problems[:50],
        "legacy_transparent": len(legacy),
    }
    return (not problems), detail


if __name__ == "__main__":
    passed, detail = run()
    print("[rfn_sync]", "PASS" if passed else "FAIL", detail)
    raise SystemExit(0 if passed else 1)
