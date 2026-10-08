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
    """消除 CSV 所有字段值内部换行；保持 UTF-8 BOM 与列结构。返回数据行数。

    N-199（2026-10-09）**改流式 + 原子写**（原实现两处缺陷）：
      ① **内存**：`rows = list(csv.reader(f))` 把整表载入（gov cleaned CSV 588MB，对象膨胀后远超）；
         本函数在 `run_clean_pipeline` 里是**无条件执行**（交付展示态的硬保证）⇒ 属热路径 ✓
         现改为「逐行读 → 归一 → 逐行写 .tmp」，峰值与语料体积解耦。
      ② **原子性**：原实现**就地覆写**同一文件 —— 中途被中断即**损坏交付产物**（且 clean 步无备份）；
         现写 `.tmp` 后 `os.replace` 原子替换（与仓内其它产物一致）。
    """
    tmp = path + ".tmp"
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f, \
            open(tmp, "w", encoding="utf-8-sig", newline="") as out:
        rd = csv.reader(f)
        w = csv.writer(out, lineterminator="\r\n")
        wrote_header = False
        for i, row in enumerate(rd):
            if i == 0:
                w.writerow(row)
                wrote_header = True
                continue
            w.writerow([flatten_text(c) for c in row])
            n += 1
    if not wrote_header:          # 空文件：不替换（保持原行为：不产生空覆盖）
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 0
    os.replace(tmp, path)
    return n


def sanitize_jsonl(path: str) -> int:
    """（可选）消除 JSONL 所有字符串值内部换行。返回记录数（N-199：流式 + 原子写）。"""
    tmp = path + ".tmp"
    n = 0
    with open(path, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
        for line in f:
            if not line.strip():
                continue
            out.write(json.dumps(flatten_text(json.loads(line)), ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


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
