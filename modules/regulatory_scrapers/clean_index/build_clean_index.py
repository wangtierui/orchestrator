# -*- coding: utf-8 -*-
"""
build_clean_index.py — 生成五源 clean 产物结构化索引（index.json）

用法：
    python build_clean_index.py [--root D:/WorkBuddy/regulatory_scrapers] [--no-hash]

说明：
    - 扫描 SCRAPER_ROOT 下五源 {source}_regulations_scraper/data/cleaned/
      （supp 为 supplementary_regulations_scraper）的 {source}_cleaned_{date}.{csv,jsonl}。
    - 计算每份文件的 size_bytes / sha256 / record_count（CSV 用 csv.reader 精确计数，
      JSONL 按非空行计数），并提取同日期配套文档（数据字典/合规记录/测试报告等）。
    - 写入 clean_index/index.json（原子替换），供 regulatory_classifier 直接加载。
    - 建议接入抓取交付流水线（拍平/清洗后）定期重跑，或手动 python build_clean_index.py。
"""

from __future__ import annotations

import argparse
import os
import sys

# 允许以脚本方式直接运行：把包所在目录的「父目录」(regulatory_scrapers) 加入 sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from clean_index import (  # noqa: E402
    SCRAPER_ROOT,
    _write_index,
    build_index_dict,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成五源 clean 产物结构化索引 index.json")
    ap.add_argument("--root", default=SCRAPER_ROOT,
                    help="regulatory_scrapers 根目录（默认自动探测）")
    ap.add_argument("--no-hash", action="store_true",
                    help="跳过 sha256 计算（仅记录 size/modified_at），加快构建")
    args = ap.parse_args(argv)

    root = os.path.normpath(args.root)
    print(f"[build_clean_index] 扫描根目录: {root}")
    data = build_index_dict(root, hash_files=not args.no_hash)
    _write_index(data)

    s = data["summary"]
    print(f"[build_clean_index] 数据源={s['source_count']} 快照={s['snapshot_count']} "
          f"CSV={s['csv_count']} JSONL={s['jsonl_count']} 记录总数={s['total_record_count']}")
    for src_id, src in data["sources"].items():
        print(f"    {src_id:6s} latest={src['latest_date']} records={src['total_record_count']}")
    print(f"[build_clean_index] 已写入: {os.path.join(_HERE, 'index.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
