# -*- coding: utf-8 -*-
"""sync_wiki_sources.py — 发布件 → llm_wiki 监控源同步（知识层接入 §6.2，2026-09-12）

职责：把双底座发布件（Base Contract v1）导出为 **llm_wiki 可监控/可摄取**的 Markdown 文件集：
  - 命名 `<rfn>__<title>.md`（保 RFN 溯源；rf n 缺失用 record_id/ipn）
  - YAML frontmatter（sources 溯源：rfn/docno/publish_date/timeliness_status/url）
  - 正文截断（--max-chars，控 token；默认 20000）
  - **SHA256 增量**：`.wiki_sync_manifest.json` 记录已同步指纹，仅复制变化文件
    （与 llm_wiki 侧 SHA256 增量缓存呼应，双端都只处理变化集）

用法：
  python tools/sync_wiki_sources.py --out <llm_wiki 监控文件夹> [--scope external|internal|all]
                                    [--max-chars 20000] [--limit N] [--dry-run]
（llm_wiki 未安装/未建项目时本脚本独立可用；安装后把 --out 指向其监控源文件夹即可。）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "modules"), os.path.join(_ROOT, "std_lib"),
           os.path.join(_ROOT, "interfaces")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_OUT = os.path.join(_ROOT, "reports", "_wiki_sources")
MANIFEST_NAME = ".wiki_sync_manifest.json"


def _slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "", (s or "").strip())
    return s[:n] or "untitled"


def _sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v in (None, "", []):
            continue
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def _export_external(out_dir: str, max_chars: int, limit: int, manifest: dict, dry: bool) -> tuple[int, int]:
    from base_api import query_external
    rows = query_external(limit=limit, with_body=True)
    added = changed = 0
    for r in rows:
        key = r.get("rfn") or r.get("record_id", "")
        fname = f"{_slug(key, 40)}__{_slug(r.get('title', ''))}.md"
        body = (r.get("body_text") or "")[:max_chars]
        content = "\n\n".join([
            _frontmatter({
                "rfn": r.get("rfn", ""), "title": r.get("title", ""),
                "document_number": r.get("document_number", ""),
                "publish_date": r.get("publish_date", ""),
                "timeliness_status": r.get("timeliness_status", ""),
                "verification_source": r.get("verification_source", ""),
                "source": r.get("source", ""), "url": r.get("url", ""),
                "theme": r.get("theme", ""), "record_id": r.get("record_id", ""),
                # F-L08：截断声明（导出正文被 max-chars 截断时显式标注，防下游误当全文）
                "body_len_full": len(r.get("body_text") or ""),
                "body_truncated": len(r.get("body_text") or "") > max_chars,
            }),
            f"# {r.get('title', '')}",
            f"> 文号：{r.get('document_number') or '（无）'}｜发布：{r.get('publish_date') or '—'}"
            f"｜时效：{r.get('timeliness_status') or '未核验'}｜来源：{r.get('source', '')}",
            body,
        ])
        sha = _sha_text(content)
        old = manifest.get(fname)
        if old == sha:
            continue
        if not dry:
            p = os.path.join(out_dir, fname)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, p)
            manifest[fname] = sha
        if old is None:
            added += 1
        else:
            changed += 1
    return added, changed


def _export_internal(out_dir: str, max_chars: int, limit: int, manifest: dict, dry: bool) -> tuple[int, int]:
    from base_api import query_internal, search_internal
    rows = query_internal(limit=limit)
    added = changed = 0
    for p in rows:
        ipn = p.get("ipn", "")
        fname = f"{_slug(ipn, 40)}__{_slug(p.get('title', ''))}.md"
        cl = search_internal(p.get("title", "")[:30] or "制度", limit=200, kind="clauses")
        arts = [c for c in cl if c.get("ipn") == ipn]
        body = "\n\n".join(f"{c.get('article_no', '')} {c.get('article_body', '')}"
                           for c in arts) or "（条文未抽取）"
        content = "\n\n".join([
            _frontmatter({
                "ipn": ipn, "title": p.get("title", ""), "docno": p.get("docno", ""),
                "primary_theme": p.get("primary_theme", ""),
                "associated_rfns": p.get("associated_rfns") or [],
                "file_type": p.get("file_type", ""),
            }),
            f"# {p.get('title', '')}",
            f"> 文号：{p.get('docno') or '（无）'}｜主题：{p.get('primary_theme') or '—'}"
            f"｜关联 RFN：{len(p.get('associated_rfns') or [])} 项",
            body[:max_chars],
        ])
        sha = _sha_text(content)
        old = manifest.get(fname)
        if old == sha:
            continue
        if not dry:
            fp = os.path.join(out_dir, fname)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, fp)
            manifest[fname] = sha
        if old is None:
            added += 1
        else:
            changed += 1
    return added, changed


def main() -> int:
    ap = argparse.ArgumentParser(description="发布件 → llm_wiki 监控源同步（SHA256 增量）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="llm_wiki 监控源文件夹（默认 reports/_wiki_sources）")
    ap.add_argument("--scope", default="external", choices=["external", "internal", "all"])
    ap.add_argument("--max-chars", type=int, default=20000, help="单文件正文截断（控 token）")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    mpath = os.path.join(args.out, MANIFEST_NAME)
    manifest = {}
    if os.path.exists(mpath):
        try:
            manifest = json.load(open(mpath, encoding="utf-8"))
        except ValueError:
            manifest = {}

    added = changed = 0
    if args.scope in ("external", "all"):
        a, c = _export_external(args.out, args.max_chars, args.limit, manifest, args.dry_run)
        added += a
        changed += c
    if args.scope in ("internal", "all"):
        a, c = _export_internal(args.out, args.max_chars, args.limit, manifest, args.dry_run)
        added += a
        changed += c

    if not args.dry_run:
        tmp = mpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, mpath)
    print(f"[wiki-sync] 新增 {added} / 更新 {changed} / 累计 {len(manifest)}"
          + ("［dry-run］" if args.dry_run else f" → {args.out}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
