# -*- coding: utf-8 -*-
"""gate_clean_schema — 清洗校验质量（v2 §3.4 V2）

背景（v2 §2.3.1，本仓最严重的"假成功"）：
    `std_lib/scraper_std/pipeline.py` 原先对 `validate_record` 失败的记录只写
    `_metadata.validation_errors`，随后**照常 append 进交付序列并无条件写盘**——
    schema 校验失败既不影响交付、也不影响退出码。

已于 2026-09-26 修复（v2 §3.4 V1）：失败记录转 `_quarantine` 隔离落盘
（`{src}_cleaned_{date}.quarantine.jsonl`），并新增失败率阈值 rc=2
（`run_clean_pipeline.SCHEMA_FAIL_RATE_MAX`，默认 2%）。

本门禁为其**结果侧**断言（防回退）：
    1) 每个源的 latest cleaned jsonl 中，带 `_metadata.validation_errors` 的记录
       必须**不存在**（若存在 → 说明有人用 --allow-schema-errors 跑了生产链，需在
       detail 中披露并要求说明）；
    2) 若存在隔离文件 `*.quarantine.jsonl`，其记录数 / (交付数 + 隔离数) ≤ 阈值
       （默认取 run_clean_pipeline.SCHEMA_FAIL_RATE_MAX）；
    3) 无数据环境（未跑过 clean）→ PASS + note（与 gate_watermark 同口径）。
"""

from __future__ import annotations

import json
import os
import re

import paths

ROOT = paths.ROOT
_SOURCES = ("gov", "mof", "nfra", "pbc", "supp")
_SNAP_RE = re.compile(r"cleaned_(\d{8})\.jsonl$")


def _cleaned_dir() -> str:
    return os.path.join(paths.MODULES_DIR, "regulatory_scrapers", "data", "cleaned")


def _latest_jsonl(src: str) -> str:
    d = _cleaned_dir()
    if not os.path.isdir(d):
        return ""
    best, best_date = "", ""
    for fn in os.listdir(d):
        m = _SNAP_RE.search(fn)
        if not m or not fn.startswith(src + "_"):
            continue
        if m.group(1) > best_date:
            best, best_date = os.path.join(d, fn), m.group(1)
    return best


def _count_lines(path: str) -> int:
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for _ in fh:
            n += 1
    return n


# 历史交付文件基线（2026-09-26 实测：本修复上线**之前**已生成的 cleaned 快照仍含失败记录；
# 隔离机制只在下次 clean 重跑时生效）。语义：仅当**超出**基线才 FAIL —— 与 v2 D-8
# "基线冻结、只减不增"一致。下一次全链 clean 重跑后应归零，届时删除本表并转为严格。
LEGACY_WITH_ERRORS: dict[str, int] = {"nfra": 5, "supp": 4}


def run() -> tuple[bool, dict]:
    # 引导：顶层 `import paths` 已要求仓根在 sys.path；本门禁不自行注入（P0-6 纪律）。

    # 阈值取自生产实现（唯一事实源，避免两处各写一个数）
    try:
        from modules.regulatory_scrapers.clean.run_clean_pipeline import (  # noqa: PLC0415
            SCHEMA_FAIL_RATE_MAX,
        )
    except Exception:  # noqa: BLE001  无源码树/导入失败时退回默认
        SCHEMA_FAIL_RATE_MAX = 0.02

    problems: list[str] = []
    detail: dict = {"threshold": SCHEMA_FAIL_RATE_MAX, "checked": {}, "quarantine": {}}
    seen_any = False

    for src in _SOURCES:
        p = _latest_jsonl(src)
        if not p:
            continue
        seen_any = True
        bad = 0
        total = 0
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                meta = rec.get("_metadata") or {}
                if meta.get("validation_errors"):
                    bad += 1
        detail["checked"][src] = {
            "file": os.path.basename(p),
            "total": total,
            "with_validation_errors": bad,
        }
        legacy = LEGACY_WITH_ERRORS.get(src, 0)
        if bad > legacy:
            problems.append(
                f"{src}: 交付文件含 {bad} 条 validation_errors 记录（{os.path.basename(p)}）"
                f"，超历史基线 {legacy}——疑似以 --allow-schema-errors 跑生产链；须说明或修复"
            )
        elif bad:
            detail.setdefault("legacy", {})[src] = {
                "with_validation_errors": bad,
                "baseline": legacy,
                "note": "修复前生成的快照；下次 clean 重跑后应归零（届时删 LEGACY_WITH_ERRORS）",
            }

        q = os.path.join(_cleaned_dir(), os.path.basename(p).replace(".jsonl", ".quarantine.jsonl"))
        if os.path.exists(q):
            qn = _count_lines(q)
            rate = qn / max(1, qn + total)
            detail["quarantine"][src] = {
                "file": os.path.basename(q),
                "count": qn,
                "rate": round(rate, 4),
            }
            if rate > SCHEMA_FAIL_RATE_MAX:
                problems.append(f"{src}: 校验隔离率 {rate:.2%} > 阈值 {SCHEMA_FAIL_RATE_MAX:.2%}")

    if not seen_any:
        return True, {
            **detail,
            "note": "无 cleaned 快照（未跑过 clean）——本判据跳过；"
            "跑一次 `python cli.py run` 后自动生效",
        }

    detail["problems"] = problems
    return (not problems), detail


if __name__ == "__main__":
    passed, det = run()
    print(
        "[clean_schema]",
        "PASS" if passed else "FAIL",
        json.dumps(det, ensure_ascii=False, indent=1),
    )
    raise SystemExit(0 if passed else 1)
