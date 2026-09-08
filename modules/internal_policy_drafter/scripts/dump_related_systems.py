# -*- coding: utf-8 -*-
"""
dump_related_systems.py —— 关联制度/体系梳理 xlsx 内容转储（I-4 路径参数化）

用途：将「保险销售行为管理办法」相关的关联管理制度清单与体系梳理表
转储为纯文本，供 LLM 直接读取分析（避免逐个单元格解析）。

路径纪律（I-4，2026-08-29）：**禁止在代码中硬编码绝对路径**。
默认目标通过「默认目录 + 文件名」组装，默认目录可用环境变量
`WIP_SALES_DIR` 覆盖；更推荐直接以位置参数传入任意 xlsx 路径。

用法：
  # 显式传入（推荐，最明确）
  python dump_related_systems.py "<路径>/关联管理制度清单.xlsx"

  # 一次多个；--max-lines 控制每个文件最大行数
  python dump_related_systems.py a.xlsx b.xlsx --max-lines 250

  # 不传参数：使用默认目录下的两个默认文件（须先设置 WIP_SALES_DIR 环境变量）
  set WIP_SALES_DIR=<制度梳理表所在目录>
  python dump_related_systems.py

依赖：openpyxl（未安装时给出明确提示，而非抛裸 ImportError）
"""
import argparse
import os
import sys

try:
    import openpyxl
except ImportError:  # pragma: no cover
    print("[error] 缺少依赖 openpyxl，请先安装：pip install openpyxl", file=sys.stderr)
    raise SystemExit(2) from None

# 默认目录下的默认文件（文件名 → 该文件的最大转储行数）
DEFAULT_FILES = [
    ("阳光人寿保险股份有限公司保险销售行为管理办法关联管理制度清单.xlsx", 400),
    ("保险销售行为管理办法-体系梳理.xlsx", 250),
]
# 默认目录：仅经 WIP_SALES_DIR 环境变量注入（P7：禁止盘符默认值）
DEFAULT_DIR = os.environ.get("WIP_SALES_DIR", "")


def dump(path, max_lines=400):
    """转储单个 xlsx：逐 sheet 打印非空行（每行截断 300 字符）。"""
    print("=" * 80)
    print("FILE:", path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            print("-" * 70)
            print("SHEET:", ws.title, "max_row:", ws.max_row, "max_col:", ws.max_column)
            n = 0
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                line = " | ".join(cells).rstrip(" |")
                if line.strip() == "":
                    continue
                print(line[:300])
                n += 1
                if n >= max_lines:
                    print("...(truncated)")
                    break
    finally:
        wb.close()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="关联制度/体系梳理 xlsx 转储（路径参数化）")
    ap.add_argument("files", nargs="*",
                    help="待转储的 xlsx 路径；不给则用 --dir 下的默认文件")
    ap.add_argument("--dir", default=DEFAULT_DIR,
                    help=f"默认目录（默认环境变量 WIP_SALES_DIR 或 {DEFAULT_DIR}）")
    ap.add_argument("--max-lines", type=int, default=0,
                    help="覆盖默认文件的最大行数（0=用各文件内置默认）")
    args = ap.parse_args(argv)

    if args.files:
        targets = [(p, args.max_lines or 400) for p in args.files]
    else:
        targets = [(os.path.join(args.dir, name),
                    args.max_lines or ml) for name, ml in DEFAULT_FILES]

    missing = 0
    for path, ml in targets:
        if not os.path.exists(path):
            print(f"[skip] 文件不存在：{path}")
            missing += 1
            continue
        dump(path, max_lines=ml)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
