# -*- coding: utf-8 -*-
"""
merge_scrape.py —— 安全合并 scraper 暂存输出到主 pbc_laws.json

为什么需要它：pbc_law_scraper.py 的 --out 会把「本次抓取到的条目」整体覆写 pbc_laws.json，
且其断点续跑只保留 fetch_status=='ok' 的条目，会丢弃已回填的 fetched/skipped_existing 正文，
--max-items 更会直接截断文件。直接用 scraper 输出覆盖主库 = 灾难性数据丢失。

本脚本：把 scraper 写到 scrape_staging/ 的结果，按 detail_url 与主库做并集合并：
  - 主库已有且含正文的条目：正文/摘要/附件路径/状态一律保留（绝不降级）；
  - 暂存里的新条目（主库没有的 detail_url）：追加；
  - 两边都有的条目：元数据以暂存为准更新，但正文仅在「主库缺失、暂存有」时采用；
  - 主库有、暂存没有的条目（站点已下架/分页未达）：原样保留，不丢历史。
合并前自动备份主库为 pbc_laws.bak_<时间戳>.json。
"""

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE)  # regulatory_scrapers（统一数据根）
STAGE = os.path.join(BASE, "data", "scrape_staging", "pbc_laws.json")  # 暂存区保留 per-source（纪律）
# 5b（2026-09-04）：主库写入点收敛至统一 data/raw/
MAIN = os.path.join(REPO_ROOT, "data", "raw", "pbc_laws.json")

def has_body(rec):
    c = (rec.get("content") or "").strip()
    if not c:
        return False
    if c.lower().endswith(".pdf") or all(l.strip().lower().endswith(".pdf") for l in c.splitlines() if l.strip()):
        return False
    return len(c) >= 50

def better_content(existing, stage):
    """返回应保留的 (content, summary, file_type, local_path, fetch_status)。"""
    e_ok = has_body(existing)
    s_ok = has_body(stage)
    if e_ok:
        return (existing.get("content"), existing.get("summary"),
                existing.get("file_type"), existing.get("local_path"),
                existing.get("fetch_status"))
    if s_ok:
        return (stage.get("content"), stage.get("summary"),
                stage.get("file_type"), stage.get("local_path"),
                stage.get("fetch_status"))
    # 两边都无正文：保留主库原值（可能是 ok/attachment 等）
    return (existing.get("content"), existing.get("summary"),
            existing.get("file_type"), existing.get("local_path"),
            existing.get("fetch_status"))

def main():
    if not os.path.exists(STAGE):
        print(f"[!] 暂存文件不存在：{STAGE}，请先运行 pbc_law_scraper.py --out scrape_staging")
        sys.exit(2)
    main_data = []
    if os.path.exists(MAIN):
        try:
            main_data = json.load(open(MAIN, encoding="utf-8"))
        except Exception as e:
            print(f"[!] 主库读取失败：{e}")
            sys.exit(3)
    stage_data = json.load(open(STAGE, encoding="utf-8"))

    main_map = {r.get("detail_url"): r for r in main_data if r.get("detail_url")}
    stage_map = {r.get("detail_url"): r for r in stage_data if r.get("detail_url")}

    merged = {}
    # 1) 主库所有条目
    for url, rec in main_map.items():
        merged[url] = dict(rec)
    # 2) 合并暂存条目
    added, updated, content_kept = 0, 0, 0
    for url, srec in stage_map.items():
        if url in merged:
            erec = merged[url]
            # 元数据更新（标量字段）
            for k in ("publish_date", "document_number", "issuing_authority",
                      "effective_date", "category", "title", "link_type"):
                if srec.get(k):
                    erec[k] = srec[k]
            # 正文保护
            content, summary, ftype, lpath, fstatus = better_content(erec, srec)
            if has_body(erec) and has_body(srec) is False:
                content_kept += 1
            erec["content"] = content
            erec["summary"] = summary
            erec["file_type"] = ftype
            erec["local_path"] = lpath
            erec["fetch_status"] = fstatus
            erec["error"] = srec.get("error")
            merged[url] = erec
            updated += 1
        else:
            merged[url] = dict(srec)
            added += 1

    result = list(merged.values())
    # 备份主库（backups/ 目录可能被清理删除——自动创建，杜绝隐式目录依赖）
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak_dir = os.path.join(BASE, "backups")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, f"pbc_laws.bak_{ts}.json")
    if os.path.exists(MAIN):
        shutil.copy2(MAIN, bak)
    json.dump(result, open(MAIN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[✓] 合并完成：主库 {len(main_data)} → 合并后 {len(result)} 条")
    print(f"    新增 {added} 条；更新元数据 {updated} 条；正文受保护(未降级) {content_kept} 条")
    print(f"    主库已备份：{bak}")

if __name__ == "__main__":
    main()
