# -*- coding: utf-8 -*-
"""
modules.regulatory_scrapers.clean.sanitize — CSV/JSONL 行对齐修复（N2 五源统一）

问题根因：清洗管道 clean_body / sentence_split 会在 body_text、split_sentences 等字段内部
写入换行；CSV 按 \\r\\n 记录分隔，但 LF 类读取器会把字段内 \\n 误判为行分隔 → 错行。
修复：字段级换行归一（\\r\\n|\\r|\\n → 空格并压缩连续空白）。

设计（N2，2026-09-08）：
  - CSV：**统一执行** sanitize_csv —— 交付展示态保证一行一记录，彻底消除错行。
  - JSONL：**默认保留字段内原始换行**（JSON 字符串换行合法、读取不产生错行；
    真类型/原文由 jsonl + raw 完整保留）。提供 --sanitize-jsonl 显式开启兼容旧 supp 行为。

实现为纯函数（不依赖 supp_normalize / 共享库），可独立运行或由 run_clean_pipeline 落盘后调用。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys

csv.field_size_limit(10 ** 9)


def flatten_text(v):
    """字段值内部换行归一（str/dict/list 递归）；非字符串原样返回。"""
    if isinstance(v, str):
        s = v.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        return re.sub(r"\s+", " ", s).strip()
    if isinstance(v, dict):
        return {k: flatten_text(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(flatten_text(x) for x in v)
    return v


def sanitize_csv(path: str) -> int:
    """消除 CSV 所有字段值内部换行；保持 UTF-8 BOM 与列结构。返回数据行数。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return 0
    header, data = rows[0], rows[1:]
    out = [header]
    for row in data:
        out.append([flatten_text(c) for c in row])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerows(out)
    return len(data)


def sanitize_jsonl(path: str) -> int:
    """（可选）消除 JSONL 所有字符串值内部换行。返回记录数。"""
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    recs = [flatten_text(r) for r in recs]
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法: python -m modules.regulatory_scrapers.clean.sanitize <csv> [jsonl]")
        return 2
    csv_path = argv[0]
    jsonl_path = argv[1] if len(argv) > 1 else ""
    n_csv = sanitize_csv(csv_path)
    print(f"[sanitize] CSV 已归一：{csv_path} （{n_csv} 行）")
    if jsonl_path and os.path.exists(jsonl_path):
        n_jl = sanitize_jsonl(jsonl_path)
        print(f"[sanitize] JSONL 已归一：{jsonl_path} （{n_jl} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
