# -*- coding: utf-8 -*-
"""gov 源「国务院政策文件库·部门文件」单条补录入口（从 collectors/gov/zhengceku.py 抽出）。

原模块内的 build_record/main 属"采集主链路之外的辅助工具"，现归并至 collectors/scripts，
与 GovCollector.collect() 主流程分离。批量抓取请改用 collectors/gov/collector.py。
"""
import argparse
import json
import logging

from ..gov.zhengceku import ZhengcekuScraper  # 跨包导入：脚本已归并至 collectors/scripts


def build_record(url: str, with_attachments: bool = True) -> dict:
    """独立入口：单条 URL 补录（便于人工核验），返回 gov 主库契约记录。"""
    class _Cfg:
        pass
    return ZhengcekuScraper(_Cfg()).fetch_one(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="gov 源「国务院政策文件库·部门文件」子采集器（单条补录入口；批量请用 gov_collector.py）")
    ap.add_argument("--url", required=True, help="详情页 URL")
    ap.add_argument("--out", default="", help="输出 JSON 路径（可选，默认仅打印）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rec = build_record(args.url)
    print(f"[ok] {rec['document_number'] or '(无文号)'} | 正文 {len(rec['full_text'])} 字 | "
          f"附件 {rec['attachment_count']} 个（附件原文 {len(rec['attachment_text'])} 字） | {rec['title'][:50]}")
    if args.out:
        json.dump([rec], open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[out]", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
