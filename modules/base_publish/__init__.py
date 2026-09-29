# -*- coding: utf-8 -*-
"""base_publish — 双底座发布层（Base Contract v1 实装，2026-09-12）

架构方案 §4.1/§5 落地：把双底座"工作目录"整理为**发布契约**（published/ 发布件）：
  - 外部底座（modules/regulatory_scrapers/published/）：external_records / external_clauses /
    external_attachments + publish_manifest.json + external_index.sqlite（SQLite+FTS5）
  - 内部底座（modules/internal_policy_base/published/）：internal_policies / internal_clauses
    + publish_manifest.json + internal_index.sqlite

调用纪律：应用模块只经 `interfaces/base_api`（进程内）或 `cli.py base ...`（跨进程）
读发布件；禁止裸读底座内部目录。
"""
from __future__ import annotations

import json
import os

SCHEMA_VERSION = "1.0.0"


def write_jsonl(path: str, rows) -> int:
    """按行写 JSONL（**原子替换**：先写 `.tmp` 再 `os.replace`）→ 返回行数。

    R-1（2026-09-30）**唯一实现**：`build_external._write_jsonl` 与 `build_internal._write_jsonl`
    曾**逐字重复**（同包两处）。风险不是"多 8 行"，而是**改一处漏一处** ——
    原子替换是**交付安全属性**（中断写不得留半截发布件），只改一处会让另一个底座
    在中断时留下**半截文件**，而两处代码**看起来一样**，审查极易放过。
    """
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n
