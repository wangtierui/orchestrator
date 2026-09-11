# -*- coding: utf-8 -*-
"""base_publish.build_external — 外部底座发布件构建（Base Contract v1 §4.1A）

产出（modules/regulatory_scrapers/published/）：
  external_records.jsonl      主键 record_id(=dedup_key) + rfn；核心字段见 §5 最小集
  external_clauses.jsonl      主键 record_id+article_no；条款级（含 rfn/生效态 join）
  external_attachments.jsonl  主键 record_id+序号；附件元数据（跨源字段归一）
  publish_manifest.json       snapshot_date / 各件 sha256 / 计数 / schema_version

来源：
  - clean_index 五源 latest JSONL（权威轨，含正文与附件）
  - clause_index 五源 latest clauses JSONL（条款）
  - rfn_clean_bridge.csv（dedup_key/source_url → RFN 唯一 join）
  - 归属表/主题归属表（rfn → 时效状态、主题）
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # modules/base_publish
_MODULES = os.path.dirname(_HERE)                            # modules/
_ROOT = os.path.dirname(_MODULES)                            # orchestrator 根
for _p in (_ROOT, _MODULES, os.path.join(_ROOT, "std_lib"),
           os.path.join(_MODULES, "regulatory_scrapers"),
           os.path.join(_MODULES, "regulatory_classifier")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from base_publish import SCHEMA_VERSION  # noqa: E402

from std_lib.common_lib.norm import norm_docno  # noqa: E402

PUBLISH_DIR = os.path.join(_MODULES, "regulatory_scrapers", "published")
CLAUSE_DIR = os.path.join(_MODULES, "regulatory_scrapers", "data", "clauses")
CLASSIFIER_DATA = os.path.join(_MODULES, "regulatory_classifier", "data")
ATTR_CSV = os.path.join(CLASSIFIER_DATA, "人身保险公司-文件归属表.csv")
THEME_CSV = os.path.join(CLASSIFIER_DATA, "人身保险公司-主题归属表.csv")
BRIDGE_CSV = os.path.join(CLASSIFIER_DATA, "rfn_clean_bridge.csv")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_jsonl(path: str, rows) -> int:
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def _load_bridge() -> tuple[dict, dict]:
    """rfn_clean_bridge → ({dedup_key: rfn}, {source_url: rfn})。"""
    by_dedup, by_url = {}, {}
    if not os.path.exists(BRIDGE_CSV):
        return by_dedup, by_url
    with open(BRIDGE_CSV, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rfn = (row.get("监管文件编号") or "").strip()
            if not rfn:
                continue
            dk = (row.get("dedup_key") or "").strip()
            u = (row.get("source_url") or "").strip()
            if dk:
                by_dedup.setdefault(dk, rfn)
            if u:
                by_url.setdefault(u, rfn)
    return by_dedup, by_url


def _load_attr_maps() -> tuple[dict, dict, dict]:
    """归属表/主题归属表 → ({norm_docno: rfn}, {rfn: timeliness}, {rfn: theme})。"""
    by_docno, tl, theme = {}, {}, {}
    if os.path.exists(ATTR_CSV):
        with open(ATTR_CSV, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rfn = (row.get("监管文件编号") or "").strip()
                if not rfn:
                    continue
                nd = norm_docno(row.get("发文字号") or "")
                if nd:
                    by_docno.setdefault(nd, rfn)
                tl[rfn] = (row.get("时效状态") or "").strip()
    if os.path.exists(THEME_CSV):
        with open(THEME_CSV, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rfn = (row.get("监管文件编号") or "").strip()
                if rfn:
                    theme.setdefault(rfn, (row.get("主题") or "").strip())
    return by_docno, tl, theme


def _iter_cleaned_records():
    """遍历五源 latest JSONL（clean_index 唯一入口）。"""
    from clean_index import get_clean_index  # noqa: PLC0415
    idx = get_clean_index()
    for sid in idx.source_ids():
        p = idx.latest_jsonl_path(sid)
        if not p or not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                rec["_source_id"] = sid
                yield rec


def _iter_clause_records():
    """遍历五源最新 clauses JSONL（clause_index 公开接口取 latest，无硬编码快照日期）。"""
    from clause_index import latest_clause_path  # noqa: PLC0415
    for sid in ("gov", "mof", "nfra", "pbc", "supp"):
        cp = latest_clause_path(sid)
        if not cp or not os.path.exists(cp):
            continue
        with open(cp, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    yield sid, json.loads(line)
                except ValueError:
                    continue


def build() -> dict:
    os.makedirs(PUBLISH_DIR, exist_ok=True)
    by_dedup, by_url = _load_bridge()
    by_docno, tl_map, theme_map = _load_attr_maps()

    meta = {}            # record_id → {publish_date, timeliness_status, rfn}
    n_att = 0

    def _records():
        nonlocal n_att
        for rec in _iter_cleaned_records():
            dk = (rec.get("dedup_key") or "").strip()
            rid = dk or (rec.get("source_url") or "").strip()
            if not rid:
                continue
            rfn = (by_dedup.get(dk) or by_url.get((rec.get("source_url") or "").strip())
                   or by_docno.get(norm_docno(rec.get("document_number") or "")) or "")
            atts = rec.get("attachments") or []
            if isinstance(atts, list):
                for a in atts:
                    if isinstance(a, dict):
                        n_att += 1
            yield {
                "record_id": rid, "rfn": rfn,
                "title": (rec.get("title") or "").strip(),
                "document_number": (rec.get("document_number") or "").strip(),
                "issue_organ": (rec.get("issue_organ") or "").strip(),
                "publish_date": (rec.get("publish_date") or "").strip(),
                "effective_date": (rec.get("effective_date") or "").strip(),
                "timeliness_status": (rec.get("timeliness_status") or "").strip(),
                "verification_source": (rec.get("verification_source") or "").strip(),
                "source": (rec.get("source") or rec.get("_source_id") or "").strip(),
                "url": (rec.get("source_url") or "").strip(),
                "body_len": len(rec.get("body_text") or ""),
                "body_text": rec.get("body_text") or "",
                "attachment_count": int(rec.get("attachment_count") or len(atts) or 0),
                "has_table": bool(rec.get("table_structured")),
                "theme": theme_map.get(rfn, ""),
            }

    # 两遍：先收集 records 与 meta（内存 ~4043 条，含正文；分批写盘）
    rows = []
    for row in _records():
        rows.append(row)
        meta[row["record_id"]] = {
            "publish_date": row["publish_date"],
            "timeliness_status": row["timeliness_status"],
            "rfn": row["rfn"],
        }
    n_rec = _write_jsonl(os.path.join(PUBLISH_DIR, "external_records.jsonl"), rows)

    def _clauses():
        for _sid, cl in _iter_clause_records():
            dk = (cl.get("dedup_key") or "").strip()
            m = meta.get(dk) or {}
            rfn = m.get("rfn") or by_dedup.get(dk, "")
            for a in (cl.get("articles") or []):
                num = (a.get("number") or "").strip()
                if not num:
                    continue
                yield {
                    "record_id": dk, "rfn": rfn,
                    "document_number": (cl.get("document_number") or "").strip(),
                    "title": (cl.get("title") or "").strip(),
                    "article_no": num,
                    "article_body": (a.get("body") or "").strip(),
                    "publish_date": m.get("publish_date", ""),
                    "timeliness_status": m.get("timeliness_status", ""),
                }

    n_clause = _write_jsonl(os.path.join(PUBLISH_DIR, "external_clauses.jsonl"), _clauses())

    def _attachments():
        for rec in _iter_cleaned_records():
            dk = (rec.get("dedup_key") or "").strip()
            rid = dk or (rec.get("source_url") or "").strip()
            rfn = (by_dedup.get(dk) or by_url.get((rec.get("source_url") or "").strip()) or "")
            for i, a in enumerate(rec.get("attachments") or []):
                if not isinstance(a, dict):
                    continue
                yield {
                    "record_id": rid, "rfn": rfn, "seq": i + 1,
                    "file_name": (a.get("file_name") or a.get("name")
                                  or a.get("attachment_name") or "").strip(),
                    "kind": (a.get("kind") or a.get("mime") or "").strip(),
                    "local_path": (a.get("local_path") or a.get("content_ref") or "").strip(),
                    "sha256": (a.get("sha256") or "").strip(),
                }

    n_att = _write_jsonl(os.path.join(PUBLISH_DIR, "external_attachments.jsonl"), _attachments())

    # snapshot_date：五源 latest date 最大值
    from clean_index import get_clean_index  # noqa: PLC0415
    idx = get_clean_index()
    snap = max((idx.latest(s) or {}).get("date") or "" for s in idx.source_ids()) or ""
    manifest = {
        "base": "external",
        "snapshot_date": snap,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": {},
    }
    for name in ("external_records.jsonl", "external_clauses.jsonl", "external_attachments.jsonl"):
        p = os.path.join(PUBLISH_DIR, name)
        manifest["files"][name] = {"sha256": _sha256_file(p), "count": sum(1 for _ in open(p, encoding="utf-8"))}
    manifest["counts"] = {"records": n_rec, "clauses": n_clause, "attachments": n_att}
    mp = os.path.join(PUBLISH_DIR, "publish_manifest.json")
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, mp)
    return manifest


def main() -> int:
    m = build()
    print(json.dumps({k: v for k, v in m.items() if k != "files"}, ensure_ascii=False, indent=1))
    print("[base_publish] external 发布件 →", PUBLISH_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
