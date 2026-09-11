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
    # R10 provenance：final 派生批次（base 的 generated_* 保留显性溯源；final 追加 finalized_*）
    import time as _t  # noqa: PLC0415
    _FIN = _t.strftime("%Y-%m-%d %H:%M:%S")
    # u_fix 键形态自适应（A 项 2026-09-08）：T1-T8 用 seq 键；T9/T10 历史 final 无 seq（旧链产物），
    # 其 config 以 RFN 为冻结键（build_base 链与 scan 链共有键），保证重跑分类零漂移。
    _fix_by_rfn = any(k.startswith("RFN-") for k in u_fix)
    # F-D03：u_fix 短别名（如 "S3"）归一为 passes 完整组名（"S3银邮渠道"），防 final 双值域
    # （实测同源双值：关键词产出长值 vs u_fix 修正短值）。
    group_names = [g for kw_map in passes for g in kw_map]
    for r in recs:
        cl = classify(r["title"], passes)
        _fix_k = r.get("监管文件编号", "") if _fix_by_rfn else str(r["seq"])
        if _fix_k in u_fix:
            cl = u_fix[_fix_k]
        if cl and cl not in group_names:
            cand = [g for g in group_names if g.startswith(cl)]
            if cand:
                cl = min(cand, key=len)
        r["cluster"] = cl
        # 契约字段（2026-09-08 R8 对齐 FINAL_KEYS）：source_origin/src_mark 语义=「scan 补充来源
        # 标记」，纯归属表投影（base）生成的 final 无该信息 → 补空串保持 schema 恒真（scan 补充路径另填）。
        r.setdefault("source_origin", "")
        r.setdefault("src_mark", "")
        r["finalized_by"] = "cluster_by_keywords"
        r["finalized_at"] = _FIN

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
