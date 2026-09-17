# -*- coding: utf-8 -*-
"""base_publish.build_external — 外部底座发布件构建（Base Contract v1 §4.1A）

产出（modules/regulatory_scrapers/published/）：
  external_records.jsonl      主键 record_id(=dedup_key) + rfn；核心字段见 §5 最小集
  external_clauses.jsonl      主键 record_id+article_no；条款级（rfn/时效/日期透传自 clause 行，缺值回退）
  external_attachments.jsonl  主键 record_id+序号；附件元数据（跨源字段归一）
  publish_manifest.json       snapshot_date / 各件 sha256 / 计数 / schema_version

来源（2026-09-13 rfn 透传收敛后）：
  - clean_index 五源 latest JSONL（权威轨，含正文与附件）
  - clause_index 五源 latest clauses JSONL（条款 + 内联 rfn/时效/日期——F-D10 投影，base 直接透传）
  - rfn_clean_bridge.csv（dedup_key/source_url → RFN；clause 行缺值时的回退，经 _rfn_of 单入口）
  - 归属表/主题归属表（norm_docno → rfn 回退、rfn → 主题）
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
for _p in (_ROOT, _MODULES, os.path.join(_ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# 阶段 3（2026-09-18）：发布层读取双底座产物一律经 interfaces（原插入兄弟模块
# 目录以 import clean_index/clause_index 的引导已移除）。

from base_publish import SCHEMA_VERSION  # noqa: E402

from interfaces.contract import attachment_view  # noqa: E402  (F-D07 附件字段契约归一)
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
            rfn = (row.get("rfn") or row.get("监管文件编号") or "").strip()
            if not rfn:
                continue
            dk = (row.get("dedup_key") or "").strip()
            u = (row.get("source_url") or "").strip()
            if dk:
                by_dedup.setdefault(dk, rfn)
            if u:
                by_url.setdefault(u, rfn)
    return by_dedup, by_url


def _load_attr_maps() -> tuple[dict, dict]:
    """归属表/主题归属表 → ({norm_docno: rfn}, {rfn: theme})。

    注（2026-09-13 rfn 透传收敛）：归属表"时效状态"列与 cleaned 同源（核验回写链），
    发布件时效以 cleaned 快照为准（与 snapshot_date 对齐）；原 tl 映射未被消费（死代码）已移除。
    """
    by_docno, theme = {}, {}
    if os.path.exists(ATTR_CSV):
        with open(ATTR_CSV, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rfn = (row.get("监管文件编号") or "").strip()
                if not rfn:
                    continue
                nd = norm_docno(row.get("发文字号") or "")
                if nd:
                    by_docno.setdefault(nd, rfn)
    if os.path.exists(THEME_CSV):
        with open(THEME_CSV, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rfn = (row.get("监管文件编号") or "").strip()
                if rfn:
                    theme.setdefault(rfn, (row.get("主题") or "").strip())
    return by_docno, theme


def _iter_cleaned_records():
    """遍历五源 latest JSONL（clean_index 唯一入口）。"""
    from interfaces.clean_index_api import get_clean_index  # noqa: PLC0415
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
    from interfaces.clause_index_api import latest_clause_path  # noqa: PLC0415
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
    by_docno, theme_map = _load_attr_maps()

    def _rfn_of(dk: str, url: str, docno: str = "") -> str:
        """RFN 解析单入口（2026-09-13 rfn 透传收敛）：桥表 dedup_key → source_url → 发文字号（norm_docno）。

        records/clauses/attachments 三视图共用（原三处内联 fallback 重复收敛为单点）。
        """
        return (by_dedup.get(dk) or by_url.get(url)
                or by_docno.get(norm_docno(docno)) or "")

    meta = {}            # record_id → {publish_date, timeliness_status, rfn}
    n_att = 0
    n_dup = 0            # 同一 record_id 被跳过的重复条数（见 _records 内注释）

    def _records():
        nonlocal n_att, n_dup
        seen_rid: set = set()
        for rec in _iter_cleaned_records():
            dk = (rec.get("dedup_key") or "").strip()
            rid = dk or (rec.get("source_url") or "").strip()
            if not rid:
                continue
            # ⚠️ record_id 是 records 表 PRIMARY KEY（= dedup_key）。同一 dedup_key 的多条
            # cleaned 记录会使 INSERT 违反 UNIQUE、令**整个 base publish 失败**
            # （实测：gov 源引入 zhengceku 全量后出现 20 组「同文号同标题、不同 URL」的
            # gov.cn 站内重复发布，报 `UNIQUE constraint failed: records.record_id`）。
            # dedup_key 的语义本就是"去重键"，故在此按 record_id 去重、保留首条；
            # 被跳过的条数计入 n_dup 以便发布清单如实披露。
            if rid in seen_rid:
                n_dup += 1
                continue
            seen_rid.add(rid)
            rfn = _rfn_of(dk, (rec.get("source_url") or "").strip(), rec.get("document_number") or "")
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

    # 两遍：先收集 records 与 meta（全量含正文常驻内存；2026-09-17 gov 引入 zhengceku 全量后
    # 五源合计约 1.66 万条、正文量级显著上升，原注释的「~4043 条」已失效）
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
            # F-D10 透传收敛（2026-09-13）：优先 clause 行内联维度（clause_index 已投影 SSOT——
            # rfn=桥表投影 / 时效、日期=cleaned 直取），行缺值时回退 records 视图 → 桥表；
            # base 侧不再重复 join/投影（原 m→bridge 二次查表已收敛）。
            rfn = (cl.get("rfn") or "").strip() or m.get("rfn") or by_dedup.get(dk, "")
            pub = (cl.get("publish_date") or "").strip() or m.get("publish_date", "")
            tl = (cl.get("timeliness_status") or "").strip() or m.get("timeliness_status", "")
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
                    "publish_date": pub,
                    "timeliness_status": tl,
                }

    n_clause = _write_jsonl(os.path.join(PUBLISH_DIR, "external_clauses.jsonl"), _clauses())

    def _attachments():
        # F-D07：附件对象经契约归一（interfaces/contract.attachment_view，五源 5 套字段收敛）
        # 去重口径与 _records() 一致（同一 record_id 只取首条的附件），避免同一文件重复发布
        # 时附件在发布件里出现两份。
        seen_att: set = set()
        for rec in _iter_cleaned_records():
            dk = (rec.get("dedup_key") or "").strip()
            rid = dk or (rec.get("source_url") or "").strip()
            if rid and rid in seen_att:
                continue
            if rid:
                seen_att.add(rid)
            rfn = _rfn_of(dk, (rec.get("source_url") or "").strip(), rec.get("document_number") or "")
            for i, a in enumerate(rec.get("attachments") or []):
                if not isinstance(a, dict):
                    continue
                v = attachment_view(a)
                yield {
                    "record_id": rid, "rfn": rfn, "seq": i + 1,
                    "file_name": str(v.get("file_name") or "").strip(),
                    "kind": str(v.get("kind") or "").strip(),
                    "local_path": str(v.get("local_path") or "").strip(),
                    "sha256": str(v.get("sha256") or "").strip(),
                    "bytes": v.get("bytes") or "",
                    "text_len": v.get("text_len") or "",
                    "url": str(v.get("url") or "").strip(),
                }

    n_att = _write_jsonl(os.path.join(PUBLISH_DIR, "external_attachments.jsonl"), _attachments())

    def _relations():
        # F-L04（2026-09-12）：条款级/书名号引用关系（clause_graph 10 主题 edges 汇聚）——
        # 关系图/影响面分析的发布数据面（原 clause_graph 产物无统一消费入口）。
        import glob as _glob  # noqa: PLC0415
        for p in sorted(_glob.glob(os.path.join(CLASSIFIER_DATA, "_t*_clause_graph.json"))):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except ValueError:
                continue
            theme = d.get("theme", "")
            for e in d.get("edges") or []:
                yield {
                    "theme": theme,
                    "src_rfn": e.get("src_rfn", ""), "src_title": e.get("src_title", ""),
                    "dst_rfn": e.get("dst_rfn", ""), "dst_title": e.get("dst_title", ""),
                    "dst_theme": e.get("dst_theme", ""),
                    "kind": e.get("kind", ""), "count": int(e.get("count") or 1),
                }

    n_rel = _write_jsonl(os.path.join(PUBLISH_DIR, "external_relations.jsonl"), _relations())

    # snapshot_date：五源 latest date 最大值
    from interfaces.clean_index_api import get_clean_index  # noqa: PLC0415
    idx = get_clean_index()
    snap = max((idx.latest(s) or {}).get("date") or "" for s in idx.source_ids()) or ""
    manifest = {
        "base": "external",
        "snapshot_date": snap,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": {},
    }
    for name in ("external_records.jsonl", "external_clauses.jsonl", "external_attachments.jsonl",
                 "external_relations.jsonl"):
        p = os.path.join(PUBLISH_DIR, name)
        manifest["files"][name] = {"sha256": _sha256_file(p), "count": sum(1 for _ in open(p, encoding="utf-8"))}
    manifest["counts"] = {"records": n_rec, "clauses": n_clause, "attachments": n_att,
                          "relations": n_rel,
                          # 同一 record_id（=dedup_key）被跳过的重复条数：如实披露去重规模，
                          # 便于核验「同文号同标题、不同 URL」类站内重复发布的实际数量。
                          "records_deduped": n_dup}
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
