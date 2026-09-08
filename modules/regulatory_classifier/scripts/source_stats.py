# -*- coding: utf-8 -*-
"""
source_stats.py —— 归属表「文件来源(file_src)」分布统计（C-8 代码化）

背景（三模块架构审查 P3/C-8）：
  README 中 `file_src` 的来源分布曾为**手写字面量**，随召回补录发生漂移而总量不变
  （2026-08-29 实测：README 声明 官方发布 4 / supp 2 / '-' 85，实存 官方发布 0 /
  supp 9 / '-' 82 —— 合计恰仍为 1076，**静态阅读无法发现该偏差**）。

本脚本将其确立为**归属表的可复现派生统计**（SSOT 纪律），供 README 维护时重新生成/核对。

用法：
  python scripts/source_stats.py                # 打印分布（Markdown 表格行可直接粘贴）
  python scripts/source_stats.py --expect 929,82,25,16,14,9,1   # 与期望值比对（顺序按条数降序）
退出码：0 = 一致/统计成功；1 = 与 --expect 不一致。
"""
import argparse
import csv
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # regulatory_classifier/
ATTR_CSV = os.path.join(ROOT, "data", "人身保险公司-文件归属表.csv")


def distribution(attr_rows=None):
    """返回 [(来源, 条数)]，按条数降序、来源名升序（保证输出稳定可比对）。"""
    if attr_rows is None:
        with open(ATTR_CSV, encoding="utf-8-sig", newline="") as fh:
            attr_rows = list(csv.DictReader(fh))
    c = Counter((r.get("文件来源") or "").strip() for r in attr_rows)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0])), len(attr_rows)


def main():
    ap = argparse.ArgumentParser(description="归属表 file_src 来源分布统计（C-8）")
    ap.add_argument("--expect", default="",
                    help="期望条数（逗号分隔，按条数降序）；用于 CI/README 核对")
    args = ap.parse_args()

    if not os.path.exists(ATTR_CSV):
        print(f"[error] 权威归属表不存在：{ATTR_CSV}")
        return 1
    dist, total = distribution()
    print(f"归属表总条数：{total} ｜ 来源种类：{len(dist)}")
    print()
    for src, n in dist:
        print(f"  {src or '(空)'!s:<12} {n:>5}  ({n / total * 100:.1f}%)")
    print()
    md = "、".join(f"`{s}`({n})" for s, n in dist)
    print("README 片段（可直接粘贴）：")
    print(f"  {len(dist)} 种：{md} ｜ {total}/{total}")

    if args.expect:
        want = [int(x) for x in args.expect.split(",") if x.strip()]
        got = [n for _, n in dist]
        if want != got:
            print(f"\n❌ 与期望不一致：期望 {want} ｜ 实存 {got}")
            return 1
        print(f"\n✅ 与期望一致：{got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
