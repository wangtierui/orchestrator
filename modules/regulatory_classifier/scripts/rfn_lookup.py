# -*- coding: utf-8 -*-
"""
rfn_lookup.py — 监管文件编号（RFN）查询工具（基于 rfn 唯一事实源模块）

编号数据一律经 rfn 包 get_index() 获取（唯一事实源=regulatory_classifier 归属表），
本脚本不定义/不硬编码任何编号。

用法：
  python rfn_lookup.py --rfn RFN-T1-142          # 按编号精确查询
  python rfn_lookup.py --title 保险销售行为      # 按标题关键词查询
  python rfn_lookup.py --docno 银保监规[2022]24号  # 按发文字号查询
  python rfn_lookup.py --theme T1                # 按主题列出全部
  python rfn_lookup.py --list-ranges             # 列出各主题编号范围
"""
import argparse
import os
import sys

# 使 rfn 包可导入（仓库根 = 本脚本上级）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfn import THEME_MAP, get_index, theme_key  # noqa: E402


def show(r, idx=None):
    if idx is not None:
        print(f"[{idx}]", end=" ")
    print(f"{r.get('监管文件编号','')} | {r.get('主题','')} | {r.get('文件名称','')}")
    print(f"      文号: {r.get('发文字号','')} | 发布: {r.get('发布日期','')} | 时效: {r.get('时效状态','')}"
          f" | 来源: {r.get('文件来源','')}")


def main():
    ap = argparse.ArgumentParser(description="监管文件编号（RFN）查询工具（唯一事实源 rfn 模块）")
    ap.add_argument("--rfn", default="", help="监管文件编号精确查询（如 RFN-T1-142）")
    ap.add_argument("--title", default="", help="标题关键词查询")
    ap.add_argument("--docno", default="", help="发文字号查询")
    ap.add_argument("--theme", default="", help="主题查询（如 T1/T2/.../T8 或主题名）")
    ap.add_argument("--list-ranges", action="store_true", help="列出各主题编号范围")
    ap.add_argument("--limit", type=int, default=0, help="结果条数上限（0 不限制）")
    args = ap.parse_args()

    idx = get_index()  # 唯一事实源单例

    if args.list_ranges:
        for theme, (first, last, n) in idx.ranges().items():
            print(f"{theme}: {first} ~ {last}（{n} 个）")
        return

    results = []
    if args.rfn:
        r = idx.by_rfn(args.rfn)
        if r:
            results.append(r)
    if args.title:
        for row in idx.rows():
            if args.title in row.get("文件名称", ""):
                results.append(row)
    if args.docno:
        results.extend(idx.by_docno(args.docno))
    if args.theme:
        # 主题码→主题全名：单一事实源 THEME_MAP（2026-09-01 移除本地硬编码副本）
        tkey = theme_key(args.theme) or args.theme
        results.extend(idx.by_theme(THEME_MAP.get(tkey, args.theme)))

    # 去重保序
    seen, uniq = set(), []
    for r in results:
        if r["监管文件编号"] not in seen:
            seen.add(r["监管文件编号"])
            uniq.append(r)

    if not uniq:
        print(f"❌ 未找到匹配记录（共 {len(idx.rows())} 条）")
        sys.exit(1)

    print(f"匹配 {len(uniq)} 条:")
    for n, r in enumerate(uniq[: args.limit if args.limit else len(uniq)], 1):
        show(r, n)
    if args.limit and len(uniq) > args.limit:
        print(f"  ...（另有 {len(uniq) - args.limit} 条未显示）")


if __name__ == "__main__":
    main()
