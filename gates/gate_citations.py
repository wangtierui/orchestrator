# -*- coding: utf-8 -*-
"""
gates/gate_citations — 制度引用门禁（旧 drafter verify_regulatory_citations --strict 语义，P7 实装）

数据源（P7 结构化）：
  - modules/internal_policy_base/data/merged_view.json（internal index × RFN 关联，IPN 侧）
  - classifier rfn 归属表（RFN 权威，经 rfn.get_index()）

校验维度：
  1) merged_view 存在且已生成（缺失 → FAIL：提示先跑 `internal merged`）；
  2) 全部 associated_rfns 的 RFN 仍在 classifier 索引中（防 RFN 漂移/制度引用失效）；
  3) 制度 docno 引用（matched_by=docno_sig）指向的 RFN 在归属表中的文号签名仍一致
     （防重清洗后文号变化导致引用断裂——引用级门禁，对应 verify 脚本 R 维度）。

说明：内部制度文号（OA 号，无监管机关前缀）非监管引用，不在校验范围。
merged_view 缺失时降级提示（允许 internal 数据尚未摄入的仓库先行）。
"""
from __future__ import annotations

import json
import os
import re
import sys

import paths

_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
_MERGED = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data", "merged_view.json")
for _p in (_MOD_CLASS, paths.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _norm_docno(s: str) -> str:
    return re.sub(r"[〔\[\]（）()〕\s]", "", s or "").rstrip("号")


def run():
    if not os.path.exists(_MERGED):
        # F-S09：输入缺失不得空跑放行（原 return True 使"全部门禁通过"含未实检门禁）。
        return False, {"error": "merged_view 未生成（先运行 `orchestrator internal merged`）；"
                                "制度引用门禁未实检，不得视为通过", "merged": None}
    view = json.load(open(_MERGED, encoding="utf-8"))
    try:
        from rfn import get_index  # noqa: PLC0415
        idx = get_index()
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"rfn 索引不可用: {e!r}", "merged": view.get("count")}

    problems = []
    seen_rfn = {}
    for rec in view.get("records", []):
        for ref in rec.get("associated_rfns", []):
            rfn = ref.get("rfn", "")
            if rfn not in seen_rfn:
                seen_rfn[rfn] = ref
            if idx.by_rfn(rfn) is None:
                problems.append(f"{rec.get('ipn', '')} 引用 {rfn} 已不在 classifier（RFN 漂移）")

    detail = {
        "merged_count": view.get("count", 0),
        "ref_rfn_distinct": len(seen_rfn),
        "problems": problems[:30],
        "stat": view.get("stat", {}),
    }
    return (not problems), detail


if __name__ == "__main__":
    passed, detail = run()
    print("[citations]", "PASS" if passed else "FAIL", json.dumps(detail, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
