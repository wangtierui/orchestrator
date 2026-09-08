# -*- coding: utf-8 -*-
"""
internal_policy_base.indexer — 内部制度摄取索引（P6 indexer）

流程（幂等，可重跑）：
  1) scan_directory(源)  → 文件元数据（IPN/docno/title/sha256）
  2) 已摄入且 sha256 未变 → 跳过（增量）
  3) 新/变 → 复制原文件入仓 data/originals/<rel> + 抽取正文 → data/processed/<ipn>.json
  4) 汇总写 data/internal_policy_index.json（主索引）+ 侧写 data/_ingest_state.json

产物（数据随仓，D-05）：
  modules/internal_policy_base/data/
    originals/<rel>           原始件副本
    processed/<ipn>.json      单文件结构化（元数据 + 规范化正文 + 抽取状态）
    internal_policy_index.json  主索引（版本链/状态/主题对齐位预留）
    _ingest_state.json          摄取游标（已处理 sha256，幂等）
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

# 同仓引导：使 `import paths`（orchestrator 根）与本包可导入（独立脚本运行支持）
_THIS = os.path.dirname(os.path.abspath(__file__))              # modules/internal_policy_base
_MODULES = os.path.dirname(_THIS)                                # modules/
_ORCH_ROOT = os.path.dirname(_MODULES)
for _p in (_ORCH_ROOT, _MODULES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from internal_policy_base.extract import copy_original, extract_file  # noqa: E402
from internal_policy_base.scan import scan_directory  # noqa: E402

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_ORIGINALS = os.path.join(_DATA, "originals")
_PROCESSED = os.path.join(_DATA, "processed")
_INDEX_PATH = os.path.join(_DATA, "internal_policy_index.json")
_STATE_PATH = os.path.join(_DATA, "_ingest_state.json")

# processed json 字段（稳定 schema）
PROCESSED_FIELDS = [
    "ipn", "file_name", "relative_path", "extension", "docno", "title",
    "size_bytes", "sha256", "file_type", "status", "extract_status",
    "text_chars", "needs_ocr", "original_path", "extracted_at",
]


def _load_state() -> dict:
    if os.path.exists(_STATE_PATH):
        try:
            return json.load(open(_STATE_PATH, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    os.makedirs(_DATA, exist_ok=True)
    tmp = _STATE_PATH + ".tmp"
    json.dump(state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_PATH)


def classify_file_type(title: str, extension: str) -> str:
    """按标题/扩展名粗分类（INTERNAL_FILE_TYPE 5 值；可后续人工精修）。"""
    t = title or ""
    if re.search(r"手册", t):
        return "manual"
    if re.search(r"办法|管理规定|制度|暂行规定|实施办法", t):
        return "policy"
    if re.search(r"细则|指引|规范|操作规程|注意事项", t):
        return "guideline"
    if re.search(r"流程|作业指导|SOP|操作流程", t):
        return "process"
    return "other"


def ingest(source_root: str, *, enable_ocr: bool = False, dry_run: bool = False) -> dict:
    """主流程。dry_run 只报告将摄取项。返回 summary。"""
    files = scan_directory(source_root)
    state = _load_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_items, changed_items, skipped = [], [], []
    for f in files:
        key = f["sha256"]
        prev = state.get(key)
        if prev and prev.get("ipn") == f["ipn"]:
            skipped.append(f["file_name"])
            continue
        if prev:
            changed_items.append(f["file_name"])
        else:
            new_items.append(f["file_name"])
        if dry_run:
            continue
        # 摄入：复制 + 抽取 + 写 processed
        orig = copy_original(f["source_path"], _ORIGINALS, f["relative_path"])
        res = extract_file(f["source_path"], name=f["file_name"], enable_ocr=enable_ocr)
        text = res.get("text") or ""
        rec = {
            "ipn": f["ipn"], "file_name": f["file_name"],
            "relative_path": f["relative_path"], "extension": f["extension"],
            "docno": f["docno"], "title": f["title"],
            "size_bytes": f["size_bytes"], "sha256": key,
            "file_type": classify_file_type(f["title"], f["extension"]),
            "status": "active", "extract_status": res.get("extract_status", ""),
            "text_chars": len(text), "needs_ocr": bool(res.get("needs_ocr")),
            "original_path": os.path.relpath(orig, _DATA), "extracted_at": now,
        }
        os.makedirs(_PROCESSED, exist_ok=True)
        tmp = os.path.join(_PROCESSED, f["ipn"] + ".json.tmp")
        json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(_PROCESSED, f["ipn"] + ".json"))
        # 正文单独存（避免 processed 内嵌大 text 混入 schema；_fulltext.json 便于下游）
        json.dump({"ipn": f["ipn"], "text": text},
                  open(os.path.join(_PROCESSED, f["ipn"] + "_fulltext.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        state[key] = {"ipn": f["ipn"], "sha256": key, "ingested_at": now}

    if not dry_run:
        _save_state(state)

    # 主索引汇总
    records = []
    for key, rec in sorted(state.items()):
        if key == "meta":
            continue
        r = _load_processed(rec["ipn"])
        if r:
            records.append({k: r.get(k, "") for k in PROCESSED_FIELDS})
    index = {
        "schema_version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_root": source_root,
        "records": records,
        "count": len(records),
    }
    os.makedirs(_DATA, exist_ok=True)
    json.dump(index, open(_INDEX_PATH + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(_INDEX_PATH + ".tmp", _INDEX_PATH)

    return {
        "scanned": len(files), "new": len(new_items), "changed": len(changed_items),
        "skipped": len(skipped), "indexed": len(records), "dry_run": dry_run,
        "index_path": _INDEX_PATH,
    }


def _load_processed(ipn: str) -> dict | None:
    p = os.path.join(_PROCESSED, ipn + ".json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="内部制度摄取索引")
    ap.add_argument("--source-dir", default=os.environ.get("INTERNAL_POLICY_ROOT", ""),
                    help="制度源目录（默认 INTERNAL_POLICY_ROOT env）")
    ap.add_argument("--enable-ocr", action="store_true", help="扫描件 PDF 启用 OCR")
    ap.add_argument("--dry-run", action="store_true", help="演练：仅报告将摄取项")
    args = ap.parse_args()
    if not args.source_dir:
        print("需提供 --source-dir 或设置 INTERNAL_POLICY_ROOT 环境变量")
        return 1
    import json as _j
    s = ingest(args.source_dir, enable_ocr=args.enable_ocr, dry_run=args.dry_run)
    print(_j.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
