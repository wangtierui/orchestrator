# -*- coding: utf-8 -*-
"""
collectors/supp/ingest_batch.py —— supplementary 批量摄取入口（五源缺失文件补全）

用途：将"五爬虫产物缺失、且已取得官方全文并经核验"的补充法规记录，合并进
      data/raw/supplementary_regulations.json（保留既有 F9~F17 记录，按
      发文字号/标题去重），再调用统一清洗管道生成 data/cleaned/ 双轨输出。

与 collectors/supp/ingest.py 的关系：
  collectors/supp/ingest.py 负责"手写 4 份"（保险销售行为主题）；
  本脚本负责"批量补全"——消费一份 backlog JSON（每条须含已核验的 body_text）。
  二者写入同一 raw JSON，管道统一清洗，互不覆盖。

严禁：body_text 为空或未经官网/北大法宝核验的记录不得入库（禁止臆造正文）。

用法：
  python collectors/supp/ingest_batch.py --backlog <backlog.json> [--merge] [--dry-run]
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

from std_lib.scraper_std.pipeline import (  # 批 49/T1：raw 统一 JSONL 读写助手
    read_raw_records,
    resolve_raw_path,
    write_raw_jsonl,
)

HERE = os.path.dirname(os.path.abspath(__file__))            # …/collectors/supp

# ⚠️ 2026-10-20 修（第一百零四批，**同一根因的第 6 处**）：原
# `SCRAPERS_ROOT = os.path.dirname(HERE)` 只上溯**一级** ⇒ 得到 `.../collectors`
# （注释却写 "modules/regulatory_scrapers"）。它被用于三处，**三处全错**：
#   ① `RAW_LOGICAL_PATH` ⇒ 指向 `collectors/data/raw/…`（不存在）⇒ 读不到主库；
#   ② `os.path.join(SCRAPERS_ROOT, "clean", "run_clean_pipeline.py")` ⇒
#      `collectors/clean/` **不存在** ⇒ `--run-clean` 必然失败；
#   ③ `subprocess.run(..., cwd=SCRAPERS_ROOT)` ⇒ 工作目录也错。
#
# 修法：**统一到单一事实源** `supp_constants`（supp 目录下所有脚本共用一套路径）。
# 双形态导入（生产调度以脚本形态直接跑本文件）。
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from .supp_constants import RAW_OUT, SCRAPERS_ROOT
except ImportError:  # pragma: no cover  脚本形态
    from supp_constants import RAW_OUT, SCRAPERS_ROOT

if SCRAPERS_ROOT not in sys.path:
    sys.path.insert(0, SCRAPERS_ROOT)

# W0-4（2026-10-13，C8）：原常量字面量为 `supplementary_regulations.json`，而批 49 起实际
# 落盘是 `.jsonl`——「常量说 .json、实际写 .jsonl」的迁移遗留漂移（易误导读者按字面量拼路径）。
# 保留历史名作**逻辑路径**（resolve_raw_path/read_raw_records/write_raw_jsonl 三助手都接受
# 历史名并解析到实际文件，双向兼容是既定设计），但**改名明示角色**：一律经助手访问，
# 禁止按字面量 open()。
# 2026-10-20：改为**直接复用** `supp_constants.RAW_OUT`（单一事实源）。
# 二者字面量本就相同（`…/data/raw/supplementary_regulations.json`），此前是
# **两处独立派生**——一旦某处修了上溯层级、另一处没修就会静默分叉（本批正是
# 从「`SCRAPERS_ROOT` 少上溯一级」这个分叉里发现的）。保留 `RAW_LOGICAL_PATH`
# 这个名字是因为本模块全程按「逻辑路径 + 助手解析」访问（见上条注释）。
RAW_LOGICAL_PATH = RAW_OUT

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
    # 批 49/T1：raw 已统一 JSONL —— `read_raw_records` 双格式（.jsonl 逐行 / 旧 .json 逐条）
    if os.path.exists(resolve_raw_path(RAW_LOGICAL_PATH)):
        try:
            data = read_raw_records(RAW_LOGICAL_PATH)
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001  采集容错（字段/附件缺失不阻断采集）
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

    os.makedirs(os.path.dirname(RAW_LOGICAL_PATH), exist_ok=True)
    # 批 49/T1：raw 统一 JSONL（流式 + 首行 `_meta`；原为**裸数组**整表 json.dump）
    _raw_written = write_raw_jsonl(RAW_LOGICAL_PATH, merged, source="supp")
    print(f"[batch] 已写 raw: {_raw_written} ({len(merged)} 条)")

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
