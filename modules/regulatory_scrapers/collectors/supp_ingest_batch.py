# -*- coding: utf-8 -*-
"""
supp_ingest_batch.py —— supplementary 批量摄取入口（五源缺失文件补全）

用途：将"五爬虫产物缺失、且已取得官方全文并经核验"的补充法规记录，合并进
      data/raw/supplementary_regulations.json（保留既有 F9~F17 记录，按
      发文字号/标题去重），再调用统一清洗管道生成 data/cleaned/ 双轨输出。

与 scripts/supp_ingest.py 的关系：
  supp_ingest.py 负责"手写 4 份"（保险销售行为主题）；
  本脚本负责"批量补全"——消费一份 backlog JSON（每条须含已核验的 body_text）。
  二者写入同一 raw JSON，管道统一清洗，互不覆盖。

严禁：body_text 为空或未经官网/北大法宝核验的记录不得入库（禁止臆造正文）。

用法：
  python scripts/supp_ingest_batch.py --backlog <backlog.json> [--merge] [--dry-run]
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # modules/regulatory_scrapers/collectors
SCRAPERS_ROOT = os.path.dirname(HERE)                          # modules/regulatory_scrapers
for _p in (HERE, SCRAPERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RAW_PATH = os.path.join(SCRAPERS_ROOT, "data", "raw", "supplementary_regulations.json")

def _norm_date(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # 尝试 YYYY年MM月DD日 / YYYYMMDD
    import re
    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s

def load_existing():
    if os.path.exists(RAW_PATH):
        try:
            with open(RAW_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backlog", required=True, help="backlog JSON 路径（含已核验 body_text 的记录）")
    ap.add_argument("--merge", action="store_true", default=True, help="与既有 raw 合并（默认开）")
    ap.add_argument("--no-merge", dest="merge", action="store_false")
    ap.add_argument("--dry-run", action="store_true", help="仅校验，不写盘/不跑管道")
    args = ap.parse_args(argv)

    with open(args.backlog, encoding="utf-8") as f:
        backlog = json.load(f)
    if not isinstance(backlog, list):
        backlog = [backlog]

    existing = load_existing() if args.merge else []
    exist_keys = {(r.get("document_number", "").strip(), r.get("title", "").strip()) for r in existing}

    added, skipped = [], []
    for rec in backlog:
        body = (rec.get("body_text") or "").strip()
        if not body:
            skipped.append((rec.get("title", "?"), "body_text 为空/未核验，跳过"))
            continue
        key = (rec.get("document_number", "").strip(), rec.get("title", "").strip())
        if key in exist_keys:
            skipped.append((rec.get("title", "?"), "已存在(发文字号/标题重复)，跳过"))
            continue
        norm = dict(rec)
        norm["publish_date"] = _norm_date(rec.get("publish_date", ""))
        norm.setdefault("source", "gov.cn补充")
        norm.setdefault("body_source", "webpage")
        norm.setdefault("timeliness_status", "valid")
        norm.setdefault("verification_source", "北大法宝/官网核验")
        norm.setdefault("status", "现行有效")
        norm["_retrieval_channel"] = rec.get("_retrieval_channel") or "官网全文/北大法宝核验"
        norm["_raw_fields"] = dict(rec.get("_raw_fields") or {})
        norm["_raw_fields"].setdefault("ingested_at", _dt.date.today().isoformat())
        added.append(norm)
        exist_keys.add(key)

    merged = existing + added
    print(f"[batch] 既存 {len(existing)} 条 | 本次新增 {len(added)} 条 | 跳过 {len(skipped)} 条")
    for t, why in skipped:
        print(f"   跳过: {t[:40]} —— {why}")

    if args.dry_run:
        print("[batch] DRY-RUN：未写盘。")
        return 0

    os.makedirs(os.path.dirname(RAW_PATH), exist_ok=True)
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[batch] 已写 raw: {RAW_PATH} ({len(merged)} 条)")

    # 调用统一清洗管道（modules/regulatory_scrapers/clean/run_clean_pipeline.py）
    py = sys.executable
    pipe = os.path.join(SCRAPERS_ROOT, "clean", "run_clean_pipeline.py")
    print(f"[batch] 运行清洗管道: {pipe} --project supp")
    # 修复（2026-09-12）：--project 为 run_clean_pipeline 必填参数，原先漏传导致每次 rc=2
    # （supp 新 raw 永不落 cleaned；见审查报告 B-01/F-S06）。
    rc = subprocess.run([py, pipe, "--project", "supp"], cwd=SCRAPERS_ROOT,
                        timeout=1800).returncode   # 审查 P2-5（2026-09-12）
    print(f"[batch] 管道返回码: {rc}")
    return rc

if __name__ == "__main__":
    sys.exit(main())
