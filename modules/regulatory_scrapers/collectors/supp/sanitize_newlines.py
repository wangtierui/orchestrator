# -*- coding: utf-8 -*-
"""
sanitize_newlines.py —— 交付物行对齐修复（错行治理）

问题根因：
  清洗管道 run_pipeline 在 map_unified 之后还会执行 clean_body / sentence_split
  等步骤，这些步骤会在 body_text、split_sentences 等字段内部写入换行符（\\n / \\r\\n）。
  记录分隔符本身用 \\r\\n（表头+56 行 = 57 处），而字段内部另有大量裸 \\n。
  任何按 \\n 切分的读取器（Excel 多配置、grep/diff 类 QC、LF 模式 pandas、纯文本
  编辑器）会把字段内 \\n 误判为行分隔，表现为「错行 / 上万行」。

修复策略（不改动共享库 std_lib，保持四项目漂移 0）：
  在 run_pipeline 完成 CSV/JSONL 落盘之后，对交付物做「字段级换行归一」——
  将每个字段值内部的 \\r\\n / \\r / \\n 统一替换为单个空格并压缩连续空白。
  结果：CSV 仅剩 57 个 \\r\\n 记录分隔符（表头+56 行），彻底消除错行；
        JSONL 同为字段级归一，正文事实性内容由 raw JSON / 落盘 PDF 完整保留。

本脚本可独立运行（python scripts/sanitize_newlines.py <csv> [<jsonl>]），
也可被 run_clean_pipeline.py 在落盘后调用，保证重跑管道结果一致。

幂等：对已是单行文本的字段无副作用，可重复执行。
"""
from __future__ import annotations

# ---- P3b 仓库引导（migrate_collectors_p3b.py 注入）：使 std_lib/config 可导入 ----
import os as _os
import sys as _sys

_GUIDE_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", ".."))
if _GUIDE_ROOT not in _sys.path:
    _sys.path.insert(0, _GUIDE_ROOT)
del _GUIDE_ROOT, _os, _sys
import csv
import json
import os
import sys

csv.field_size_limit(10 ** 9)

HERE = os.path.dirname(os.path.abspath(__file__))
UTILS = os.path.join(os.path.dirname(HERE), "utils")
for _p in (UTILS, os.path.dirname(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用 supp_normalize 的归一逻辑，保证字段级规则单一来源
try:
    from supp_normalize import _flatten_text as _flatten  # noqa: E402
except Exception:  # pragma: no cover - 退化实现
    import re

    def _flatten(v):
        if isinstance(v, str):
            s = v.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
            return re.sub(r"\s+", " ", s).strip()
        if isinstance(v, dict):
            return {k: _flatten(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(_flatten(x) for x in v)
        return v

def sanitize_csv(path: str) -> int:
    """消除 CSV 所有字段值内部换行；保持 UTF-8 BOM 与 39 列结构。返回数据行数。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return 0
    header, data = rows[0], rows[1:]
    out = [header]
    for row in data:
        out.append([_flatten(c) for c in row])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerows(out)
    return len(data)

def sanitize_jsonl(path: str) -> int:
    """消除 JSONL 每条记录所有字符串值内部换行；保持一行一 JSON。返回记录数。"""
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))

    def walk(o):
        if isinstance(o, str):
            return _flatten(o)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    recs = [walk(r) for r in recs]
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)

def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法: python scripts/sanitize_newlines.py <csv路径> [<jsonl路径>]")
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
