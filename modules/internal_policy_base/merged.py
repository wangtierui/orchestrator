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

# 正文引用抽取原语已上收 `std_lib.common_lib.relations`（R-F01，2026-09-14）：
#   - 文号签名：`iter_docno_signatures`（原 `_DOCNO_REF`，语义=裸括号式年+序号）
#   - 书名号标题：`iter_quote_titles`（原 `_TITLE_REF`，长度 4-40）
# 本模块不再保留本地正则实现（唯一事实源纪律）。

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


from std_lib.common_lib.norm import norm_docno as _norm_docno  # A-10：SSOT 收敛（标准层）
from std_lib.common_lib.norm import norm_title_strict as _norm_title  # A-10：SSOT 收敛（保守层）

# R-F01（2026-09-14）：正文引用抽取原语上收 `std_lib.common_lib.relations`（唯一实现）——
# 原 `_DOCNO_REF` / `_TITLE_REF` 两条本地正则已删除，改调共享函数（语义保持：
# 签名长度 ≥6、书名号标题长度 4-40；参数即该语义的显式表达）。
from std_lib.common_lib.relations import iter_docno_signatures as _iter_docno_sigs
from std_lib.common_lib.relations import iter_quote_titles as _iter_quote_titles

_TITLE_REF_MIN, _TITLE_REF_MAX = 4, 40   # 与收敛前 `《([^《》\n]{4,40})》` 等价


def extract_rfns(text: str) -> list[dict]:
    """从正文抽取监管引用 → 匹配 RFN。返回 [{"rfn","title","docno","matched_by"}] 去重。"""
    idx = _idx()
    rows = idx.rows()
    out, seen = [], set()
    # a. 文号引用：文号签名（年+号）匹配归属表文号数字尾（原语 = relations.iter_docno_signatures）
    for sig in _iter_docno_sigs(text or ""):
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
    for t in _iter_quote_titles(text or "", min_len=_TITLE_REF_MIN, max_len=_TITLE_REF_MAX):
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


def _sha_file(p: str) -> str:
    import hashlib  # noqa: PLC0415
    h = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def _processed_signature() -> str:
    """processed 目录内容指纹（名称+大小+mtime），快速近似全文 sha（R5 断点语义）。"""
    import hashlib  # noqa: PLC0415
    h = hashlib.sha256()
    if os.path.isdir(_PROCESSED):
        for fn in sorted(os.listdir(_PROCESSED)):
            p = os.path.join(_PROCESSED, fn)
            try:
                st = os.stat(p)
                h.update(f"{fn}|{st.st_size}|{int(st.st_mtime)};".encode())
            except OSError:
                pass
    return h.hexdigest()[:16]


def build_merged_view() -> dict:
    """生成 merged_view.json（幂等覆盖）。返回 summary。

    R5 断点（2026-09-08）：view.inputs 记录输入指纹（归属表/主题归属表 sha + 内部索引 sha
    + processed 目录签名），供审计/门禁判断"上游单源变化后本视图是否陈旧需重建"。
    """
    records = _load_records()
    # 同 IPN 多 sha（同名同文号同介质的不同内容版本）→ 同制度多份：保留 extracted_at 最新一条
    # （2026-09-12 修复：原直接 append 使 merged_view/policies 出现重复 IPN，发布件 UNIQUE 冲突）
    merged_map: dict = {}
    ext_map: dict = {}
    for rec in records:
        text = _load_text(rec["ipn"])
        refs = extract_rfns(text)
        item = {
            "ipn": rec["ipn"], "title": rec.get("title", ""),
            "docno": rec.get("docno", ""), "extension": rec.get("extension", ""),
            "file_type": rec.get("file_type", ""),
            "primary_theme": rec.get("primary_theme", ""),
            "secondary_themes": rec.get("secondary_themes", []),
            "status": rec.get("status", "active"),
            "associated_rfns": refs,
            "rfn_count": len(refs),
        }
        ipn = rec["ipn"]
        if ipn not in merged_map or (rec.get("extracted_at") or "") >= ext_map.get(ipn, ""):
            merged_map[ipn] = item
            ext_map[ipn] = rec.get("extracted_at") or ""
    merged = list(merged_map.values())
    n_with_rfn = sum(1 for m in merged if m.get("associated_rfns"))
    cl_data = os.path.join(_MODULES, "regulatory_classifier", "data")
    inputs = {
        "attr_sha": _sha_file(os.path.join(cl_data, "人身保险公司-文件归属表.csv")),
        "theme_sha": _sha_file(os.path.join(cl_data, "人身保险公司-主题归属表.csv")),
        "index_sha": _sha_file(_INDEX_PATH),
        "processed_signature": _processed_signature(),
    }
    view = {
        "schema_version": MERGED_VIEW_SCHEMA_VERSION,
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "internal_policy_index.json + rfn(归属表)",
        "inputs": inputs,
        "records": merged,
        "count": len(merged),
        "stat": {
            "total": len(merged),
            "deduped_duplicates": len(records) - len(merged),   # 同 IPN 多内容版本去重数（透明）
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
