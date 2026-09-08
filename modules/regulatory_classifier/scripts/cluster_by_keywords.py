# -*- coding: utf-8 -*-
"""
cluster_by_keywords.py — 关键词驱动主题聚类工具（通用化沉淀版）

将 cluster_independent/v2/v3.py 的关键词聚类逻辑改造为通用工具：
  - 关键词表外置为 JSON（config/cluster_keywords.json），按主题配置多轮关键词与人工修正
  - 多轮匹配：第 1 轮命中即归类；未命中进入下一轮（用于"总精算师"先于"精算"等优先级场景）
  - u_fix：人工复核修正映射（seq -> cluster），处理关键词无法覆盖的边界文件
  - 幂等：输出 final.json 追加 cluster 字段，可重复运行

关键词表结构（cluster_keywords.json）：
{
  "T3": {
    "passes": [
      {"C1偿二代体系": ["偿付能力", "偿二代"], ...},   # 第1轮（优先级最高）
      {"C2资本补充": ["次级定期债务", ...], ...}        # 第2轮（补充细分）
    ],
    "u_fix": {"5": "C3", "12": "C1"}                   # 人工修正 seq->cluster
  }
}

用法：
  python cluster_by_keywords.py --input _3_base.json --output _3_final.json --theme T3
  python cluster_by_keywords.py --input _3_base.json --output _3_final.json --theme T3 --keywords config/cluster_keywords.json
"""
import argparse
import collections
import json
import os
import sys

DEFAULT_KEYWORDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "cluster_keywords.json")


def classify(title, passes):
    """多轮关键词匹配：命中第一轮即返回。"""
    for kw_map in passes:
        for cluster, kws in kw_map.items():
            for kw in kws:
                if kw in title:
                    return cluster
    return "U未分类"


def main():
    ap = argparse.ArgumentParser(description="关键词驱动主题聚类工具")
    ap.add_argument("--input", required=True, help="主题清单 base.json（seq/title 等）")
    ap.add_argument("--output", required=True, help="输出 final.json（追加 cluster 字段）")
    ap.add_argument("--theme", required=True, help="主题键（如 T1/T2/.../T8），对应关键词表配置")
    ap.add_argument("--keywords", default=DEFAULT_KEYWORDS, help="关键词表 JSON（默认 scripts/config/cluster_keywords.json）")
    args = ap.parse_args()

    # 加载关键词表
    kw_cfg = json.load(open(args.keywords, encoding="utf-8"))
    if args.theme not in kw_cfg:
        print(f"❌ 关键词表无主题 {args.theme} 配置（现有: {list(kw_cfg.keys())}）")
        sys.exit(1)
    cfg = kw_cfg[args.theme]
    passes = cfg.get("passes", [cfg.get("keywords", {})])
    u_fix = cfg.get("u_fix", {})

    # 聚类
    recs = json.load(open(args.input, encoding="utf-8"))
    print(f"输入清单: {len(recs)} 条 | 主题 {args.theme} | 关键词轮次: {len(passes)}")
    for r in recs:
        cl = classify(r["title"], passes)
        if str(r["seq"]) in u_fix:
            cl = u_fix[str(r["seq"])]
        r["cluster"] = cl

    json.dump(recs, open(args.output, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 统计
    c = collections.Counter(r["cluster"] for r in recs)
    print(f"\n聚类分布（{args.output}）:")
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    u_left = [r["seq"] for r in recs if r["cluster"] == "U未分类"]
    if u_left:
        print(f"⚠️ 剩余 U 未分类 {len(u_left)} 条: {u_left}")
        for r in recs:
            if r["cluster"] == "U未分类":
                print(f"    seq{r['seq']} | {r['title'][:50]} | {r.get('doc_no','')[:24]}")
    else:
        print("✅ 无 U 未分类残留")


if __name__ == "__main__":
    main()
