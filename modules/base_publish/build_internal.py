# -*- coding: utf-8 -*-
"""base_publish.build_internal — 内部底座发布件构建（Base Contract v1 §4.1B）

产出（modules/internal_policy_base/published/）：
  internal_policies.jsonl     主键 ipn；含 primary_theme/secondary_themes/associated_rfns
  internal_clauses.jsonl      主键 ipn+article_no；rfns[]（条文级引用，当前=制度级兜底）
  publish_manifest.json       sha256 / 计数 / schema_version

来源：internal_policy_base data（index + align_result + merged_view + processed clauses）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # modules/base_publish
_MODULES = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_MODULES)
for _p in (_ROOT, _MODULES, os.path.join(_ROOT, "std_lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from base_publish import SCHEMA_VERSION  # noqa: E402

# v2 §3.1.3 I-3（2026-09-26）：内部制度数据/发布件目录改经 interfaces 访问器（判据 D）
from interfaces.internal_policy_api import data_dir as _ipb_data_dir  # noqa: E402
from interfaces.internal_policy_api import published_dir as _ipb_published_dir  # noqa: E402

IPB_DATA = _ipb_data_dir()
PUBLISH_DIR = _ipb_published_dir()


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


def _load(name: str, default):
    p = os.path.join(IPB_DATA, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding="utf-8"))
    except ValueError:
        return default


def build() -> dict:
    os.makedirs(PUBLISH_DIR, exist_ok=True)
    idx = _load("internal_policy_index.json", {})
    align = _load("align_result.json", {})
    merged = _load("merged_view.json", {})

    by_ipn_idx = {r.get("ipn", ""): r for r in (idx.get("records") or []) if r.get("ipn")}
    by_ipn_align = {r.get("ipn", ""): r for r in (align.get("records") or []) if r.get("ipn")}
    merged_recs = merged.get("records") or []

    def _policies():
        seen = set()   # 防御（merged 已去重；此处再保证发布件 ipn 唯一，防 UNIQUE 冲突）
        for r in merged_recs:
            ipn = r.get("ipn", "")
            if not ipn or ipn in seen:
                continue
            seen.add(ipn)
            ix = by_ipn_idx.get(ipn, {})
            al = by_ipn_align.get(ipn, {})
            rfns = [x.get("rfn", "") for x in (r.get("associated_rfns") or []) if x.get("rfn")]
            yield {
                "ipn": ipn,
                "title": (r.get("title") or al.get("title") or ix.get("file_name") or "").strip(),
                "docno": (r.get("docno") or al.get("docno") or ix.get("docno") or "").strip(),
                "eff_status": "",      # 内部制度时效未建（登记为契约空列，待 IPB 时效链补齐）
                "primary_theme": (al.get("primary_theme") or r.get("primary_theme") or "").strip(),
                "secondary_themes": al.get("secondary_themes") or r.get("secondary_themes") or [],
                "associated_rfns": rfns,
                "file_type": ix.get("file_type", ""),
                "extension": (ix.get("extension") or r.get("extension") or "").strip(),
                "sha256": ix.get("sha256", ""),
                "article_count": int(ix.get("article_count") or 0),
            }

    policies = list(_policies())
    n_pol = _write_jsonl(os.path.join(PUBLISH_DIR, "internal_policies.jsonl"), policies)

    pol_rfns = {p["ipn"]: p["associated_rfns"] for p in policies}
    processed = os.path.join(IPB_DATA, "processed")

    def _clauses():
        if not os.path.isdir(processed):
            return
        for fn in sorted(os.listdir(processed)):
            if not fn.endswith("_clauses.json"):
                continue
            ipn = fn[: -len("_clauses.json")]
            try:
                cl = json.load(open(os.path.join(processed, fn), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rfns = pol_rfns.get(ipn, [])       # 条文级引用待建（E-04 缺口）；当前=制度级兜底
            for a in (cl.get("articles") or []):
                num = (a.get("number") or "").strip()
                if not num:
                    continue
                yield {
                    "ipn": ipn, "article_no": num,
                    "article_body": (a.get("body") or "").strip(),
                    "rfns": rfns,
                }

    n_cl = _write_jsonl(os.path.join(PUBLISH_DIR, "internal_clauses.jsonl"), _clauses())

    manifest = {
        "base": "internal",
        "snapshot_date": datetime.datetime.now().strftime("%Y%m%d"),
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": {},
        "counts": {"policies": n_pol, "clauses": n_cl},
    }
    for name in ("internal_policies.jsonl", "internal_clauses.jsonl"):
        p = os.path.join(PUBLISH_DIR, name)
        manifest["files"][name] = {"sha256": _sha256_file(p),
                                   "count": sum(1 for _ in open(p, encoding="utf-8"))}
    mp = os.path.join(PUBLISH_DIR, "publish_manifest.json")
    tmp = mp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, mp)
    return manifest


def main() -> int:
    m = build()
    print(json.dumps(m, ensure_ascii=False, indent=1))
    print("[base_publish] internal 发布件 →", PUBLISH_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
