# -*- coding: utf-8 -*-
"""
tools/build_migration_manifest.py — 生成 data_migration_manifest.json（P0 / R22）

作用：扫描四个旧仓（只读）的**现行活跃数据**，输出 {源绝对路径, 目标相对路径, size, sha256}
清单文件到 orchestrator 根 data_migration_manifest.json（提交入库，供 P1-P4 复制与对照基线）。

范围（仅活跃数据，不含历史归档/backups/cache）：
  scrapers: data/cleaned 每源最新1版 csv+jsonl、data/raw 定长主库 json、clean_index/index.json、
            timeliness_review/verification_state.json + 最新时效性标注结果清单_全量 {csv,jsonl}
  classifier: data/*.csv(归属表/主题归属表)、data/_t*.json(40)、data/T*明细表*.csv(11)、
              data/_upper_laws.json、rfn/*.csv、rfn/sync_status.json、
              recall_audit/verification_state.mirror.json、docs/**(报告)
  drafter: docs/**（六件套等权威交付物）
用法：python tools/build_migration_manifest.py [--root D:/WorkBuddy] [--out ...]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re

OLD_DIRS = {
    "scrapers": "regulatory_scrapers",
    "classifier": "regulatory_classifier",
    "drafter": "internal_policy_drafter",
    "base": "internal_policy_base",
}

# 每个 (source_root_key, 相对 glob, 目标相对路径模板, 说明)
# 用 {date_latest} 占位不支持——改为手工挑选最新（按文件名日期后缀排序取最大）。
CLEAN_RE = re.compile(r"^(gov|mof|nfra|pbc|supp)_cleaned_(\d{8})\.(csv|jsonl)$")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _latest_cleaned(scrapers_root: str) -> list[dict]:
    """data/cleaned 下每源最新 1 版 csv+jsonl（按日期取最大）。"""
    cleaned_dir = os.path.join(scrapers_root, "data", "cleaned")
    out = []
    if not os.path.isdir(cleaned_dir):
        return out
    by_src: dict[str, dict[str, str]] = {}
    for name in os.listdir(cleaned_dir):
        m = CLEAN_RE.match(name)
        if not m:
            continue
        src, date, ext = m.group(1), m.group(2), m.group(3)
        cur = by_src.setdefault(src, {})
        if date > cur.get("date", ""):
            cur.update(date=date, **{ext: os.path.join(cleaned_dir, name)})
    for src, hit in sorted(by_src.items()):
        for ext in ("csv", "jsonl"):
            p = hit.get(ext)
            if p and os.path.exists(p):
                out.append({"src": f"scrapers/data/cleaned/{os.path.basename(p)}",
                            "path": p})
    return out


def _latest_raw_master(scrapers_root: str) -> list[dict]:
    """data/raw 定长主库 json（gov_laws/mof_laws/nfra_regulations/pbc_laws/supplementary_regulations.json）。"""
    raw_dir = os.path.join(scrapers_root, "data", "raw")
    names = ["gov_laws.json", "mof_laws.json", "nfra_regulations.json",
             "pbc_laws.json", "supplementary_regulations.json"]
    out = []
    for n in names:
        p = os.path.join(raw_dir, n)
        if os.path.exists(p):
            out.append({"src": f"scrapers/data/raw/{n}", "path": p})
    return out


def _latest_timeliness(scrapers_root: str) -> list[dict]:
    out = []
    state = os.path.join(scrapers_root, "timeliness_review", "verification_state.json")
    if os.path.exists(state):
        out.append({"src": "scrapers/timeliness_review/verification_state.json", "path": state})
    full_glob = os.path.join(scrapers_root, "timeliness_review",
                             "时效性标注结果清单_全量_*.csv")
    cands = sorted(glob.glob(full_glob))
    if cands:
        p = cands[-1]
        out.append({"src": f"scrapers/timeliness_review/{os.path.basename(p)}", "path": p})
    return out


def _classifier_data(classifier_root: str) -> list[dict]:
    out = []
    data_dir = os.path.join(classifier_root, "data")
    if not os.path.isdir(data_dir):
        return out
    for name in os.listdir(data_dir):
        if name.endswith(".csv") and ("归属表" in name or "主题归属表" in name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif re.match(r"^_t\d+_(base|final|matched|citerefs)\.json$", name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif re.match(r"^T\d+_\d+逐份条款引用与上位法依据明细表\.csv$", name):
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
        elif name == "_upper_laws.json":
            out.append({"src": f"classifier/data/{name}", "path": os.path.join(data_dir, name)})
    # rfn 索引/指纹/sync
    rfn_dir = os.path.join(classifier_root, "rfn")
    for name in ("监管文件编号索引.csv", "文件指纹.csv", "sync_status.json"):
        p = os.path.join(rfn_dir, name)
        if os.path.exists(p):
            out.append({"src": f"classifier/rfn/{name}", "path": p})
    # mirror
    mp = os.path.join(classifier_root, "recall_audit", "verification_state.mirror.json")
    if os.path.exists(mp):
        out.append({"src": "classifier/recall_audit/verification_state.mirror.json", "path": mp})
    return out


def _docs_md(root: str, prefix: str) -> list[dict]:
    out = []
    docs_dir = os.path.join(root, "docs")
    if not os.path.isdir(docs_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(docs_dir):
        for fn in filenames:
            if fn.endswith(".md"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                out.append({"src": f"{prefix}/{rel}", "path": full})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="D:/WorkBuddy")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = args.root
    items: list[dict] = []
    scrapers_root = os.path.join(root, OLD_DIRS["scrapers"])
    classifier_root = os.path.join(root, OLD_DIRS["classifier"])
    drafter_root = os.path.join(root, OLD_DIRS["drafter"])
    items += _latest_cleaned(scrapers_root)
    items += _latest_raw_master(scrapers_root)
    items += _latest_timeliness(scrapers_root)
    items += _classifier_data(classifier_root)
    items += _docs_md(drafter_root, "drafter")

    manifest = []
    for it in items:
        try:
            manifest.append({
                "src": it["src"],
                "path": it["path"],
                "size_bytes": os.path.getsize(it["path"]),
                "sha256": _sha(it["path"]),
            })
        except OSError as e:
            print(f"[warn] 跳过 {it['src']}: {e}")
    out_path = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "data_migration_manifest.json")
    manifest.sort(key=lambda x: x["src"])
    payload = {
        "schema_version": "1.0",
        "generated_at": None,
        "source_roots": {k: os.path.join(root, v) for k, v in OLD_DIRS.items()},
        "note": "现行活跃数据复制清单（P0）。P1-P4 按条目复制到新仓对应位置并校验 sha256。",
        "items": manifest,
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    print(f"[manifest] {len(manifest)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
