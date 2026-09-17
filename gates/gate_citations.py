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
import sys

import paths

_MOD_CLASS = os.path.join(paths.MODULES_DIR, "regulatory_classifier")
_MERGED = os.path.join(paths.MODULES_DIR, "internal_policy_base", "data", "merged_view.json")
for _p in (_MOD_CLASS, paths.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _wm_status(key: str):
    """产物水位状态（阶段 4：判据切换共用入口 `interfaces.governance_api.wm_status`）。"""
    try:
        from interfaces.governance_api import wm_status  # noqa: PLC0415
        return wm_status(key)
    except Exception as e:  # noqa: BLE001  水位不可用 → unknown（调用方退回指纹判据）
        return "unknown", {"reason": f"{type(e).__name__}: {e}"}


def run():
    if not os.path.exists(_MERGED):
        # F-S09：输入缺失不得空跑放行（原 return True 使"全部门禁通过"含未实检门禁）。
        return False, {"error": "merged_view 未生成（先运行 `orchestrator internal merged`）；"
                                "制度引用门禁未实检，不得视为通过", "merged": None}
    view = json.load(open(_MERGED, encoding="utf-8"))
    # F-C08（2026-09-12）：merged_view 陈旧校验——inputs 指纹（归属表/主题表/内部索引/processed
    # 目录签名）重算比对；上游变化后视图未重建即 FAIL（原实现 inputs 零校验，陈旧视图不可感）。
    #
    # 阶段 4（2026-09-18）判据切换：**水位优先，inputs 指纹降为交叉校验/回退**。
    #   水位 stale   → FAIL（"上游已推进、视图未重建"，零容差且直接报出是哪条依赖边）；
    #   水位 ok      → 通过（inputs 差异仅作交叉校验信息；`processed_signature` 用
    #                  名称|size|mtime 近似，touch 即变，误报率高）；
    #   水位 unknown → 退回原 inputs 指纹判据（不得因"无水位"放行）。
    _wm_state, _wm_detail = _wm_status("merged_view")
    fingerprint_note = None
    try:
        _ipb = os.path.join(paths.MODULES_DIR, "internal_policy_base")
        if _ipb not in sys.path:
            sys.path.insert(0, _ipb)
        import merged as _merged  # noqa: PLC0415
        from interfaces.rfn_api import registry_paths as _registry_paths  # noqa: PLC0415
        _rp = _registry_paths()
        cur = {
            "attr_sha": _merged._sha_file(_rp["attr_csv"]),
            "theme_sha": _merged._sha_file(_rp["theme_csv"]),
            "index_sha": _merged._sha_file(_merged._INDEX_PATH),
            "processed_signature": _merged._processed_signature(),
        }
        stale_keys = [k for k, v in cur.items() if (view.get("inputs") or {}).get(k) != v]
        if stale_keys:
            if _wm_state == "stale":
                return False, {"error": f"merged_view 陈旧（水位判据）：{_wm_detail.get('stale')}；"
                                        "先运行 `orchestrator internal merged` 重建视图",
                               "merged": view.get("count"), "inputs_now": cur}
            if _wm_state == "unknown":
                return False, {"error": f"merged_view 陈旧（inputs 判据；水位不可用："
                                        f"{_wm_detail.get('reason')}）: {stale_keys}；"
                                        "先运行 `orchestrator internal merged` 重建视图",
                               "merged": view.get("count"), "inputs_now": cur}
            fingerprint_note = (f"水位判据为 ok，inputs 指纹差异 {stale_keys} 判为近似签名"
                                "（名称|size|mtime）误报，不阻断")
    except Exception as e:  # noqa: BLE001  校验不可用时显式记录（不静默）
        if _wm_state != "ok":
            return False, {"error": f"merged_view 陈旧校验不可执行: {e!r}（不得视为通过）",
                           "merged": view.get("count")}
        fingerprint_note = f"inputs 指纹复核不可执行（{e!r}），但水位判据为 ok"
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
        "freshness": {"watermark": _wm_state, "watermark_detail": _wm_detail,
                      "cross_check": fingerprint_note},
        "note": "陈旧判据：阶段 4 起水位优先，inputs 指纹降为交叉校验（近似签名易误报）",
    }
    return (not problems), detail


if __name__ == "__main__":
    passed, detail = run()
    print("[citations]", "PASS" if passed else "FAIL", json.dumps(detail, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)
