# -*- coding: utf-8 -*-
"""tools.backfill_extract_version — 给历史附件**盖抽取版本号**（一次性迁移，默认 dry-run）。

为什么需要
----------
`collect:mof` 的耗时修复（见 `attachments._reuse_extraction`）依赖"本地副本未变且
`extract_version` 一致"才复用历史抽取结果。而**存量 raw 里的附件没有这个字段**
（实测 246 个附件，带版本号的 **0** 个）⇒ 升级后的**第一次**运行仍会全量重抽
（仍是小时级），第二次起才快。

本工具把"当前 raw 里既有的抽取结果"盖上官方的 `EXTRACT_VERSION`，
让复用**从下一次运行起立即生效**。

**为什么这是安全的**（人工判断依据，必须显式 `--apply` 才会写）
------------------------------------------------------------
复用的语义是"**沿用已有产物，不做新的解析**"：
  · 如果存量文本是**旧版抽取器**产出的，盖章不会让它变差 —— 它本来就在数据里，
    下游一直按它工作；重抽才可能改变它（那是另一次有意的升级动作）。
  · 反之，**不盖章**的代价是每次运行重复解析 500MB 附件（实测 50MB 税则 PDF
    单次 8–9.5s，抽样均值 4.31s/附件）。
  · 因此盖章**不引入新的不准确**，只是把"要不要重抽"的决定权从"每次运行"
    收敛为"抽取器升级时显式做一次"。

**什么时候不该用**：刚改过 `extract_document_text` / `structured_table_fields` /
`rich_object_fields` 的语义 —— 那时应让它们自然重抽（并递增 `EXTRACT_VERSION`）。

用法
----
    python -m tools.backfill_extract_version                 # dry-run（只统计）
    python -m tools.backfill_extract_version --apply         # 写入（带备份）
    python -m tools.backfill_extract_version --apply --file mof_laws.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from bootstrap import bootstrap

bootstrap("all")

import paths
from config.exitcodes import ExitCode

RAW = os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "raw")

#: 默认目标（只处理 mof；其它源如有同构需求可再加）
DEFAULT_FILE = "mof_laws.jsonl"


def _stamp_records(recs: list[dict]) -> tuple[int, int, int]:
    """就地盖章。返回 (已盖, 缺抽取结果跳过, 已有版本跳过)。"""
    from modules.regulatory_scrapers.collectors.mof.attachments import EXTRACT_VERSION

    stamped = skipped_no_result = skipped_has_ver = 0
    for d in recs:
        if d.get("_meta"):
            continue
        atts = d.get("attachments") or []
        for a in atts:
            if not isinstance(a, dict):
                continue
            if a.get("extract_version"):
                skipped_has_ver += 1
                continue
            has_result = ("text" in a) or (a.get("extracted") is not None)
            if not has_result:
                skipped_no_result += 1
                continue
            a["extract_version"] = EXTRACT_VERSION
            a["extract_reused"] = True       # 溯源：这条文本不是本轮解析出来的
            stamped += 1
    return stamped, skipped_no_result, skipped_has_ver


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="backfill_extract_version",
        description="给历史附件盖抽取版本号（默认 dry-run）")
    ap.add_argument("--file", default=DEFAULT_FILE, help=f"raw 文件名（默认 {DEFAULT_FILE}）")
    ap.add_argument("--apply", action="store_true", help="真的写入（默认只统计）")
    a = ap.parse_args(argv)

    p = os.path.join(RAW, a.file)
    if not os.path.exists(p):
        sys.stderr.write(f"[backfill] 找不到 {p}\n")
        return ExitCode.DATA

    t0 = time.time()
    recs: list[dict] = []
    for ln in open(p, encoding="utf-8", errors="replace"):
        if not ln.strip():
            continue
        recs.append(json.loads(ln))
    print(f"[backfill] 读取 {len(recs)} 行 / {os.path.getsize(p)/1048576:.1f} MB "
          f"（{time.time()-t0:.1f}s）")

    stamped, skip_no, skip_ver = _stamp_records(recs)
    print(f"[backfill] 已盖版本号：{stamped} 个附件")
    print(f"           跳过（无抽取结果）：{skip_no}")
    print(f"           跳过（已有版本号）：{skip_ver}")
    if not a.apply:
        print("[backfill] **dry-run**，未写入。加 --apply 落盘（会先备份）。")
        return ExitCode.OK
    if stamped == 0:
        print("[backfill] 无需写入。")
        return ExitCode.OK

    bak = p + time.strftime(".bak_%Y%m%d_%H%M%S")
    shutil.copy2(p, bak)
    print(f"[backfill] 已备份 → {os.path.basename(bak)}")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for d in recs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    print(f"[backfill] 已写入 {p}（{os.path.getsize(p)/1048576:.1f} MB）")
    print("[backfill] 提示：下次运行 `collect:mof` 将走复用路径（跳过 PDF 解析）。")
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
