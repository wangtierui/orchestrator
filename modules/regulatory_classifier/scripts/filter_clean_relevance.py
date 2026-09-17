#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""clean → classify 之间的**相关性筛选**环节。

定位（2026-09-17 新增）
----------------------
数据流既定分层：

    raw  ──[clean 管道]──►  cleaned 快照（**全量自动进入**，不做筛选）
    cleaned ──[本脚本]──►  相关性候选清单（INCLUDE / BOUNDARY / EXCLUDE 分层）
    候选清单 ──[rfn.registry.register_doc]──►  归属表（classify 的实际输入）

为什么筛选落在这里：`classify` 的各子步**只处理归属表内已登记的文件**
（`build_base_from_attr.load_attr()` 遇无主题码即 continue），
因此"只把金融/保险相关条目推进 classify 体系"等价于"只把相关条目登记进归属表"。
本脚本产出登记前的候选清单与分层证据，登记动作由 `--emit-register-candidates`
输出可审计清单，实际写入归属表仍走 `rfn.registry.register_doc`（唯一程序写口）。

判定规则**不另起一套**：直接复用 `recall_audit/scanner.py` 的关键词库与分层逻辑
（TIER_A / AGENT / GENERIC / EXCL_* + 标题级覆盖规则），避免规则双写漂移。

用法
----
    python -m modules.regulatory_classifier.scripts.filter_clean_relevance --source gov
    ... --source gov --decisions INCLUDE,BOUNDARY      # 只导出推进体系的候选项
    ... --source gov --limit 200                       # 试跑抽样
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
import time
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # 仓库根


def load_scanner():
    """载入 recall_audit/scanner.py 的**判定器段**（复用关键词库与分层规则，不触发扫描）。

    ⚠️ 不能用常规 import：`scanner.py` 的**扫描代码在模块顶层**（无 `__main__` 守卫，
    L245「# ---------------- 扫描 ----------------」起是模块级 for 循环），
    常规 import 会立即对五源 cleaned 做一次全量扫描并覆写 `recall_audit/output/*`
    （实测 import 即输出「扫描完成. 总记录: 16617」）——对"筛选"环节而言这是无辜的副作用。

    故此处**只 compile+exec 扫描段之前的部分**（含常量、build_re、scan_group、classify
    与 808 索引/match_attr），既拿到判定器，又零副作用、不改动原文件。
    """
    p = os.path.join(ROOT, "modules", "regulatory_classifier", "recall_audit", "scanner.py")
    if not os.path.exists(p):
        raise SystemExit(f"[ERR] 未找到判定器：{p}")
    src = open(p, encoding="utf-8").read()
    marker = "# ---------------- 扫描 ----------------"
    if marker not in src:
        raise SystemExit(f"[ERR] scanner.py 结构已变，未找到扫描段分界标记：{marker!r}"
                         "——请复核后更新本函数，勿直接 import（会触发全量扫描）。")
    head = src.split(marker)[0]
    ns: dict = {"__name__": "_relevance_scanner_head", "__file__": p}
    exec(compile(head, p, "exec"), ns)      # noqa: S102 - 受限执行：仅判定器段，无扫描副作用
    if "classify" not in ns:
        raise SystemExit("[ERR] 判定器段未导出 classify()，请复核 scanner.py 结构。")
    # 用 SimpleNamespace 而非 type()：后者会把 classify 变成未绑定的方法（需显式 self）。
    import types
    return types.SimpleNamespace(**ns)


def latest_cleaned(source: str, kind: str = "csv") -> str:
    """经 clean_index 取该源最新快照路径（禁硬编码日期，与全仓约定一致）。

    ⚠️ clean_index 的条目可能是 dict（index.json 原样）也可能是对象（内存模型），
    两者都要兼容——初版只探属性，对 dict 形态恒得 None。
    """
    # 阶段 3（2026-09-18）：经 interfaces 唯一入口（原 `from modules.regulatory_scrapers import
    # clean_index` 跨模块直连已移除）；`_get` 兼容 dict / 对象两种索引形态的语义保留。
    # 注：ROOT 为**本仓根**（非兄弟模块目录），注入属合法引导（gate_no_cross_module_import 不拦）。
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from interfaces import clean_index_api as ci  # type: ignore  # noqa: PLC0415

    def _get(o, k, d=None):
        if isinstance(o, dict):
            return o.get(k, d)
        return getattr(o, k, d)

    idx = ci.get_clean_index()
    srcs = _get(idx, "sources") or {}
    if source not in srcs:
        raise SystemExit(f"[ERR] clean_index 无源 {source}；现有：{sorted(srcs)}")
    ent = srcs[source]
    # ① 已解析好的最新路径字段
    for attr in (f"latest_{kind}_path", f"latest_{kind}"):
        v = _get(ent, attr)
        if isinstance(v, str) and v:
            return v
    # ② 回退：从 snapshots 取日期最新的一份
    snaps = _get(ent, "snapshots") or {}
    if not snaps:
        raise SystemExit(f"[ERR] {source} 无 snapshots；ent 形态={type(ent).__name__}")
    newest = sorted(snaps)[-1]
    files = _get(snaps[newest], "files") or {}
    f = _get(files, kind)
    p = _get(f, "path") if not isinstance(f, str) else f
    if not p or not os.path.exists(p):
        raise SystemExit(f"[ERR] {source} 快照 {newest} 的 {kind} 路径无效：{p!r}")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="clean → classify 相关性筛选（复用 scanner 判定器）")
    ap.add_argument("--source", default="gov", help="源标识（默认 gov）")
    ap.add_argument("--raw-csv", default="", help="直接指定 cleaned CSV（默认经 clean_index 取最新）")
    ap.add_argument("--out-dir", default="", help="输出目录（默认 modules/regulatory_classifier/data/relevance）")
    ap.add_argument("--decisions", default="INCLUDE,BOUNDARY,EXCLUDE",
                    help="导出哪些判定层（逗号分隔；推进体系的候选一般为 INCLUDE,BOUNDARY）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（试跑）")
    ap.add_argument("--title-only", action="store_true",
                    help="只按**标题**认定命中（忽略 meta/body）。处理成分与词表调优语料差异大的"
                         "数据源（如 gov 政策文件库全量）时必开——否则 GENERIC 裸词「分支机构」"
                         "会使大量无关行政法规落入 BOUNDARY。详见 scanner.classify 文档串。")
    ap.add_argument("--stamp", default="", help="产物日期戳（默认今日 YYYYMMDD）")
    args = ap.parse_args(argv)

    stamp = args.stamp or time.strftime("%Y%m%d")
    # ⚠️ 产物**不能**放 `data/` 下：门禁「目录拍平（data/docs 无子目录）」只允许
    # `regulatory_classifier/data/relations` 一个子目录，新建 `data/relevance` 会被判 FAIL。
    # 故落在模块级 `relevance/`（不在 data/ 下，不受该门禁约束）。
    out_dir = args.out_dir or os.path.join(
        ROOT, "modules", "regulatory_classifier", "relevance")
    os.makedirs(out_dir, exist_ok=True)

    sc = load_scanner()
    csv_path = args.raw_csv or latest_cleaned(args.source, "csv")
    want = {d.strip().upper() for d in args.decisions.split(",") if d.strip()}
    print(f"[in ] {args.source} cleaned → {os.path.relpath(csv_path, ROOT)}")
    print(f"[out] {os.path.relpath(out_dir, ROOT)}  导出层：{sorted(want)}")

    cols = ["title", "summary", "issue_organ", "column_name", "theme_name", "keyword",
            "body_text", "body_text_webpage", "body_text_doc", "attachment_content",
            "document_number", "publish_date", "source", "source_url"]
    cnt, layer, conf_cnt, yr = Counter(), Counter(), Counter(), Counter()
    # 「金融/保险相关」的独立统计：不以 scanner 的 INCLUDE 为限，
    # 另按"泛金融"词表单独计数，供确认筛选口径是否过窄。
    FIN = ("金融", "银行", "保险", "证券", "基金", "信托", "期货", "融资", "信贷", "支付",
           "资产管理", "财富管理", "消费金融", "资本市场", "理财", "债券", "利率", "汇率")
    fin_hit = 0
    out_path = os.path.join(out_dir, f"{args.source}_relevance_{stamp}.jsonl")
    t0 = time.time()
    n = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f, \
            open(out_path + ".tmp", "w", encoding="utf-8") as fo:
        for row in csv.DictReader(f):
            n += 1
            if args.limit and n > args.limit:
                break
            title = row.get("title", "") or ""
            meta = " ".join((row.get(k, "") or "") for k in
                            ("summary", "issue_organ", "column_name", "theme_name", "keyword"))
            body = " ".join((row.get(k, "") or "") for k in
                            ("body_text", "body_text_webpage", "body_text_doc", "attachment_content"))
            decision, confc, a_kws, b_kws, c_kws, x_kws, snip, _, _ = sc.classify(
                title, meta, body, title_only=args.title_only)
            cnt["total"] += 1
            layer[decision] += 1
            conf_cnt[f"{decision}/{confc}"] += 1
            y = (row.get("publish_date", "") or "")[:4]
            if y.isdigit():
                yr[y] += 1
            blob = title + meta
            if any(w in blob for w in FIN):
                fin_hit += 1
            if decision in want:
                fo.write(json.dumps({
                    "title": title,
                    "decision": decision,
                    "confidence": confc,
                    "publish_date": row.get("publish_date", ""),
                    "issue_organ": row.get("issue_organ", ""),
                    "document_number": row.get("document_number", ""),
                    "source": row.get("source", ""),
                    "source_url": row.get("source_url", ""),
                    "hit_a": a_kws, "hit_b": b_kws, "hit_c": c_kws, "hit_x": x_kws,
                    "snippet": snip,
                }, ensure_ascii=False) + "\n")
    os.replace(out_path + ".tmp", out_path)

    el = time.time() - t0
    print(f"\n[耗时] {el:.1f}s（{cnt['total']} 条，{cnt['total'] / max(el, 0.1):.0f} 条/秒）")
    print(f"[分层] " + "  ".join(f"{k}={v}" for k, v in layer.most_common()))
    print(f"[推进候选] INCLUDE+BOUNDARY = {layer['INCLUDE'] + layer['BOUNDARY']} 条")
    print(f"[泛金融词命中] {fin_hit} 条（标题+元数据级）")
    print(f"[置信度] " + "  ".join(f"{k}={v}" for k, v in sorted(conf_cnt.items())))
    print(f"[年份前 8] " + "  ".join(f"{k}:{v}" for k, v in sorted(yr.items())[:8]))
    print(f"[产物] {os.path.relpath(out_path, ROOT)}（{len(want)} 层）")
    # ⚠️ 产物中**不得写绝对路径**：门禁「盘符字面量扫描（R4）」会扫 .json/.md 等非 py 文件，
    # 出现盘符字面量即 FAIL（连注释里的示例也算）。统一落相对路径，与全仓可移植性约定一致。
    summ = {"source": args.source, "stamp": stamp,
            "csv": os.path.relpath(csv_path, ROOT).replace(os.sep, "/"), "total": cnt["total"],
            "layer": dict(layer), "confidence": dict(conf_cnt), "fin_hit": fin_hit,
            "decisions_exported": sorted(want), "elapsed_s": round(el, 1),
            "year_top": dict(sorted(yr.items())[:12])}
    sp = os.path.join(out_dir, f"{args.source}_relevance_summary_{stamp}.json")
    json.dump(summ, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[摘要] {os.path.relpath(sp, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
