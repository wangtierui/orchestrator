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


# norm-specialization: JSONL 原子写**登记 SSOT**（B6/W4，README:79 明载；原 scraper_std
# 侧非原子副本与 tools 侧私有副本均已删——收敛自两处，见函数 docstring L2 标注）
def write_jsonl(path: str, rows) -> int:
    """按行写 JSONL（**原子替换**：先写 `.tmp` 再 `os.replace`）→ 返回行数。

    R-1（2026-09-30）**唯一实现**：`build_external._write_jsonl` 与 `build_internal._write_jsonl`
    曾**逐字重复**（同包两处）。风险不是"多 8 行"，而是**改一处漏一处** ——
    原子替换是**交付安全属性**（中断写不得留半截发布件），只改一处会让另一个底座
    在中断时留下**半截文件**，而两处代码**看起来一样**，审查极易放过。

    L2（W4，2026-10）SSOT 标注——**收敛自**：`tools/extract_relations._write_jsonl`
    （tmp+replace 语义一致的跨层副本，已删）；`std_lib/scraper_std/common/file_io.write_jsonl`
    （**语义分化**：非原子便捷写入，零调用，已删——JSONL 落盘一律走本 SSOT）。
    门禁 `gate_no_duplicate_libs` 对同名函数要求本标注存在（无标注 → 告警候选）。
    """
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


# W4（方案 §6.3.3）：发布**版本史**（append-only）。
# `publish_manifest.json` 是**单版**（每次发布被覆盖）——"上次发布长什么样"随之丢失，
# 条款级变更追踪（用途十六：某条款何时进/出发布件）与审计回溯（某次发布为何计数跳变）
# 都无可判据。版本史补这一面：每次发布追加一行（manifest 自身 sha + 各件 sha/计数 +
# 时间），成本极低（一次一行），且与 manifest 同目录、同发布事务写入。
def append_publish_history(manifest_path: str, manifest: dict) -> str:
    """把一次发布的指纹摘要追加到 `publish_manifest_history.jsonl`（同目录）。

    返回版本史路径。行形态：
        {ts, base, snapshot_date, manifest_sha256, counts, files: {name: {sha256, count}}}
    `manifest_sha256` 对**刚落盘的 manifest 文件**取哈希（版本史自证：该行描述的
    就是那一版 manifest）。写失败**向上抛**（调用方决定是否阻断——发布已完成，
    版本史缺失不应回滚发布，但必须显式暴露，不得静默）。
    """
    import hashlib

    hist = os.path.join(os.path.dirname(manifest_path), "publish_manifest_history.jsonl")
    h = hashlib.sha256()
    with open(manifest_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    row = {
        "ts": manifest.get("generated_at", ""),
        "base": manifest.get("base", ""),
        "snapshot_date": manifest.get("snapshot_date", ""),
        "schema_version": manifest.get("schema_version", ""),
        "manifest_sha256": h.hexdigest(),
        "counts": manifest.get("counts", {}),
        "files": manifest.get("files", {}),
    }
    with open(hist, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return hist
