# -*- coding: utf-8 -*-
"""ingest_corpus.py — 外部语料归集（F-L05：EAST2.0 / 部门制度 / 保单登记等"未归集数据域"）

归集纪律（架构方案 §3.5）：
  ① 先建 manifest（源路径 + 每文件 sha256/大小 + 目标域 + 时间）；
  ② 物理归集（数据不入 git；目标 `modules/regulatory_scrapers/data/corpus/<domain>/`）；
  ③ 幂等增量（已存在且 sha256 相同则跳过）。

用法：
  python tools/ingest_corpus.py --src <源目录> --domain <域标识> [--exclude <相对路径片段>]...
  # 示例（源目录经参数传入，严禁在源码/文档写盘符字面量——gate_hardcoded_paths R4）：
  #   --src "<EAST2.0 语料根>" --domain east2_m20
  #   --src "<公司各部门制度根>" --domain dept_policies
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

CORPUS_DIR = os.path.join(_ROOT, "modules", "regulatory_scrapers", "data", "corpus")
# 归集清单落 reports/corpus/（reports 入库；语料本体 data/ 不入库——清单为唯一可审计入口）
MANIFEST_DIR = os.path.join(_ROOT, "reports", "corpus")


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(src: str, excludes: list[str], exclude_tops: list[str] | None = None):
    """遍历源目录。

    excludes：**相对路径片段**匹配（历史语义，慎用——片段会误伤深层同名子目录，
      如"需求文档"会命中 `00.关于修订…/2.0需求文档/`，2026-09-12 实证）；
    exclude_tops：**顶层目录名精确**匹配（预期用法：源根一级目录整棵排除）。
    """
    tops = {t for t in (exclude_tops or []) if t}
    for dirpath, dirnames, filenames in os.walk(src):
        rel_dir = os.path.relpath(dirpath, src)
        if rel_dir == "." and tops:
            dirnames[:] = [d for d in dirnames if d not in tops]   # 顶层剪枝（整棵跳过）
        dirnames[:] = [d for d in dirnames
                       if not any(e and e in os.path.join(rel_dir, d) for e in excludes)]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src)
            if tops and rel.split(os.sep)[0] in tops:
                continue
            if any(e and e in rel for e in excludes):
                continue
            yield full, rel


def _update_index(domain: str, manifest: dict) -> None:
    """同步 reports/corpus/_index.json 的域条目与总计（2026-09-12 落地；原 _index 无生成器）。"""
    idx_path = os.path.join(MANIFEST_DIR, "_index.json")
    idx: dict = {"schema_version": "1.0",
                 "policy": "语料本体 data/corpus/（不入库）；清单 reports/corpus/（入库，唯一可审计入口）",
                 "domains": []}
    if os.path.exists(idx_path):
        try:
            idx = json.load(open(idx_path, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    domains = [d for d in (idx.get("domains") or []) if d.get("domain") != domain]
    domains.append({
        "domain": domain,
        "source_dir": manifest.get("source_dir", ""),
        "file_count": manifest.get("file_count", 0),
        "total_bytes": manifest.get("total_bytes", 0),
        "ingested_at": manifest.get("ingested_at", ""),
        "excludes": manifest.get("excludes", []),
        "exclude_tops": manifest.get("exclude_tops", []),
        "manifest": f"reports/corpus/{domain}.manifest.json",
    })
    idx["domains"] = sorted(domains, key=lambda d: d.get("domain", ""))
    idx["total_files"] = sum(int(d.get("file_count") or 0) for d in idx["domains"])
    idx["total_bytes"] = sum(int(d.get("total_bytes") or 0) for d in idx["domains"])
    idx["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = idx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, idx_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="外部语料归集（manifest + 幂等物理归集）")
    ap.add_argument("--src", required=True, help="源目录")
    ap.add_argument("--domain", required=True, help="域标识（目标子目录名）")
    ap.add_argument("--exclude", action="append", default=[],
                    help="排除的相对路径片段（可多次；慎用——片段会误伤深层同名子目录）")
    ap.add_argument("--exclude-top", action="append", default=[], dest="exclude_tops",
                    help="排除的**顶层目录名**（精确；源根一级整棵排除；可多次）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-hash", action="store_true", help="跳过 sha256（大目录快速模式，manifest 记 size）")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print(f"[ingest] 源目录不存在: {src}")
        return 1
    dst_root = os.path.join(CORPUS_DIR, args.domain)
    os.makedirs(dst_root, exist_ok=True)

    files, copied, skipped, nbytes = [], 0, 0, 0
    for full, rel in _iter_files(src, args.exclude, args.exclude_tops):
        st = os.stat(full)
        nbytes += st.st_size
        sha = "" if args.no_hash else _sha256(full)
        target = os.path.join(dst_root, rel)
        rec = {"rel": rel.replace("\\", "/"), "bytes": st.st_size,
               "sha256": sha, "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
        files.append(rec)
        if args.dry_run:
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target) and (args.no_hash or _sha256(target) == sha):
            skipped += 1
            continue
        shutil.copy2(full, target)
        copied += 1

    manifest = {
        "domain": args.domain,
        "source_dir": src.replace("\\", "/"),
        "excludes": args.exclude,
        "exclude_tops": args.exclude_tops,
        "ingested_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "total_bytes": nbytes,
        "copied": copied,
        "skipped_existing": skipped,
        "files": files,
    }
    if not args.dry_run:
        os.makedirs(MANIFEST_DIR, exist_ok=True)
        for mp in (os.path.join(dst_root, "_ingest_manifest.json"),
                   os.path.join(MANIFEST_DIR, f"{args.domain}.manifest.json")):
            tmp = mp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, mp)
        _update_index(args.domain, manifest)   # 2026-09-12：_index.json 同步（原无生成器）
    print(f"[ingest] {args.domain}: {len(files)} 文件 / {nbytes / 1048576:.1f} MB"
          f"（复制 {copied} / 已存在 {skipped}）" + ("［dry-run］" if args.dry_run else f" → {dst_root}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
