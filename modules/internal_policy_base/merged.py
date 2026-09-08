# -*- coding: utf-8 -*-
"""
internal_policy_base.merged — merged_view 生成（P7 / D-06 schema 冻结 v1.0）

目标：
  把 internal_policy_index（107 制度，IPN）与 regulatory_classifier（监管文件，RFN）打通为
  统一对齐视图 merged_view.json —— 供 internal_policy_drafter 起草/修订时做「制度↔监管依据」
  关联核查（引用门禁数据源）。

方法：
  1) 抽取每份制度正文中的监管引用：
     a. 文号引用：〔YYYY〕N号 / [YYYY]N号 / 令YYYY年第N号 → classifier 发文字号精确命中 → RFN
     b. 标题引用：《标题》→ classifier 标题归一命中 → RFN（仅当标题存在于归属表，排除内部自引）
  2) 按 制度IPN 聚合 → associated_rfns[]（引用到的监管文件）+ themes（P6 align 已算）
  3) 输出 merged_view.json：schema_version="1.0"（D-06 冻结，二期 app 读取前不改字段语义）

  aligned_ratio = 有 RFN 关联的制度数 / 总数（用于 gate_citations 阈值参考）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))       # modules/internal_policy_base
_MODULES = os.path.dirname(_THIS)
_ORCH_ROOT = os.path.dirname(_MODULES)
for _p in (_ORCH_ROOT, os.path.join(_MODULES, "regulatory_classifier")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA = os.path.join(_THIS, "data")
_PROCESSED = os.path.join(_DATA, "processed")
_INDEX_PATH = os.path.join(_DATA, "internal_policy_index.json")
_MERGED_PATH = os.path.join(_DATA, "merged_view.json")
MERGED_VIEW_SCHEMA_VERSION = "1.0"   # D-06 冻结；二期 app 读取前不改字段语义

# 文号引用（外部监管特征：〔YY〕N号 形态，需机关字? 不强制——结合标题命中确认）
_DOCNO_REF = re.compile(r"[〔\[(（]\s*(\d{4})\s*[〕\])）]\s*(\d{1,4})\s*号")
_TITLE_REF = re.compile(r"《([^《》\n]{4,40})》")

_idxfac = None


def _idx():
    global _idxfac
    if _idxfac is None:
        from rfn import get_index  # noqa: PLC0415
        _idxfac = get_index()
    return _idxfac


def _load_records():
    with open(_INDEX_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("records", [])


def _load_text(ipn: str) -> str:
    p = os.path.join(_PROCESSED, ipn + "_fulltext.json")
    if os.path.exists(p):
        try:
            return (json.load(open(p, encoding="utf-8")) or {}).get("text", "")
        except Exception:
            return ""
    return ""


def _norm_docno(s: str) -> str:
    return re.sub(r"[〔\[\]（）()〕\s]", "", s or "").rstrip("号")


def _norm_title(t: str) -> str:
    t = re.sub(r"[（(](已废止|已失效|试行|修订)[）)]\s*$", "", (t or "").strip())
    return re.sub(r'[《》"“”\s]', "", t)


def extract_rfns(text: str) -> list[dict]:
    """从正文抽取监管引用 → 匹配 RFN。返回 [{"rfn","title","docno","matched_by"}] 去重。"""
    idx = _idx()
    rows = idx.rows()
    out, seen = [], set()
    # a. 文号引用：〔YYYY〕N号 签名（年+号）匹配归属表文号尾部
    for y, n in _DOCNO_REF.findall(text or ""):
        sig = "%s%s" % (y, n)
        if len(sig) < 6:
            continue
        for r in rows:
            ds = re.sub(r"\D", "", _norm_docno(r.get("发文字号", "")))
            if ds.endswith(sig) and len(ds) >= len(sig):
                rfn = r.get("监管文件编号", "")
                if rfn not in seen:
                    seen.add(rfn)
                    out.append({"rfn": rfn, "title": r.get("文件名称", ""),
                                "docno": r.get("发文字号", ""), "matched_by": "docno_sig"})
                break
    # b. 标题引用：《标题》在归属表存在才算（排除内部制度自引）
    for t in _TITLE_REF.findall(text or ""):
        nt = _norm_title(t)
        if len(nt) < 6:
            continue
        hit = None
        for r in rows:
            if _norm_title(r.get("文件名称", "")) == nt:
                hit = r
                break
        if hit:
            rfn = hit.get("监管文件编号", "")
            if rfn not in seen:
                seen.add(rfn)
                out.append({"rfn": rfn, "title": hit.get("文件名称", ""),
                            "docno": hit.get("发文字号", ""), "matched_by": "title"})
    return out


def build_merged_view() -> dict:
    """生成 merged_view.json（幂等覆盖）。返回 summary。"""
    records = _load_records()
    merged = []
    n_with_rfn = 0
    for rec in records:
        text = _load_text(rec["ipn"])
        refs = extract_rfns(text)
        if refs:
            n_with_rfn += 1
        merged.append({
            "ipn": rec["ipn"], "title": rec.get("title", ""),
            "docno": rec.get("docno", ""), "extension": rec.get("extension", ""),
            "file_type": rec.get("file_type", ""),
            "primary_theme": rec.get("primary_theme", ""),
            "secondary_themes": rec.get("secondary_themes", []),
            "status": rec.get("status", "active"),
            "associated_rfns": refs,
            "rfn_count": len(refs),
        })
    view = {
        "schema_version": MERGED_VIEW_SCHEMA_VERSION,
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "internal_policy_index.json + rfn(归属表)",
        "records": merged,
        "count": len(merged),
        "stat": {
            "total": len(merged),
            "with_rfn_refs": n_with_rfn,
            "aligned_ratio": round(n_with_rfn / len(merged), 3) if merged else 0,
        },
    }
    os.makedirs(_DATA, exist_ok=True)
    tmp = _MERGED_PATH + ".tmp"
    json.dump(view, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, _MERGED_PATH)
    return view["stat"]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    s = build_merged_view()
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
