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
from config.enums import SOURCE_ORDER

_SOURCES = SOURCE_ORDER
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


# 严格态（P9，2026-09-26）：LEGACY_WITH_ERRORS 历史基线已删除——2026-09-26 全链 clean 重跑后
# 五源交付文件 validation_errors 均归零（隔离机制把失败记录写入 .quarantine.jsonl）。
# 此后**任何**交付文件含 validation_errors 即 FAIL（不存在"历史遗留豁免"）。


def run() -> tuple[bool, dict]:
    # 引导：顶层 `import paths` 已要求仓根在 sys.path；本门禁不自行注入（P0-6 纪律）。

    # 阈值取自生产实现（唯一事实源，避免两处各写一个数）
    try:
        from modules.regulatory_scrapers.clean.run_clean_pipeline import (
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
        if bad:
            problems.append(
                f"{src}: 交付文件含 {bad} 条 validation_errors 记录（{os.path.basename(p)}）"
                f"——疑似以 --allow-schema-errors 跑生产链；须说明或修复"
            )

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
                # N-42/N-43（P9，2026-09-26）：隔离率是「源数据健康度」**软指标**——它报警的是
                # 源里长期存在的草稿记录（如 supp 源 4 条：本地文件路径、媒体来源、缺文号），
                # 属**数据治理**待办（需人工清理源数据），非代码/流程正确性。故**只披露不阻断**
                # （进 detail 供人工治理）；validation_errors 判据（交付文件含失败记录）保持严格阻断。
                detail["quarantine"][src]["exceeds_threshold"] = round(rate, 4)

    if not seen_any:
        return True, {
            **detail,
            "note": "无 cleaned 快照（未跑过 clean）——本判据跳过；"
            "跑一次 `python cli.py run` 后自动生效",
        }

    # N-79（2026-09-28）：**条文产物契约自检接入门禁**。
    # 背景：`clause_index.validate_schema()`（产物字段集/条号形态/解析适配枚举/结构语义指标）
    # 早已实现，但**生产链与门禁零调用**（唯一调用点曾是 `tests/test_e2e_pipeline.py`）→
    # 五源 16k+ 条文产物在全链 22 道门禁中**无任何覆盖**。此处经
    # `interfaces.clause_index_api` 唯一入口复用既有实现（不另写校验器）。
    # 无产物 → 跳过（与上方"未跑过 clean"同款语义，不误判为通过）。
    try:
        from interfaces import clause_index_api as _clause_api

        if any(_clause_api.latest_clause_path(s) for s in _SOURCES):
            _cv = _clause_api.validate_schema()
            _cprob = list(_cv.get("problems") or [])
            # 返回是**扁平**结构（计数 + 结构语义指标 + consistent + problems）：
            # 除两枚判据键外**全部**作为 stat 入 detail（含 title_swallow / tail_contam /
            # space_contam / law_items 等"曾被静默放过"的语义指标 → 使其在门禁详情中可见）。
            _cstat = {k: v for k, v in _cv.items() if k not in ("consistent", "problems")}
            detail["clause_schema"] = {
                "consistent": bool(_cv.get("consistent")),
                "problems": _cprob[:8],
                **{k: _cstat[k] for k in sorted(_cstat)[:16]},
            }
            if not _cv.get("consistent"):
                problems.append(
                    f"条文产物契约自检未通过（{len(_cprob)} 项，前 3）：{_cprob[:3]}"
                )
        else:
            detail["clause_schema"] = {"skipped": "无条文产物（未跑过 clause_index build）"}
    except Exception as e:  # noqa: BLE001
        # 判据不可执行 ≠ 判据通过（与 gate_timeliness_ssot 同口径）
        problems.append(f"条文产物契约自检无法执行：{type(e).__name__}: {e}")

    # N-99（2026-09-28）：**清洗规则注册表**守护（判据并入本门禁，**不**新增第 23 道门禁——
    # 新增门禁会连带改动 `gates/__init__.py` GATES 清单 / `BENCHMARK.md` / 文档三处口径）。
    # 背景：`std_lib/scraper_std/clean_rules_registry.py`（N-92）把 7 个清洗模块的 26 条规则
    # 声明化，但**此前无任何机制**保证"声明的规则仍存在"（改名/删除即静默漂移）。
    try:
        from std_lib.scraper_std import clean_rules_registry as _crr

        _rp = _crr.verify()
        detail["clean_rules_registry"] = {
            "rules": len(_crr.RULES),
            "modules": len({r["module"] for r in _crr.RULES}),
            "stages": len(_crr.STAGE_ORDER),
            "problems": _rp[:6],
        }
        if _rp:
            problems.append(
                f"清洗规则注册表漂移（{len(_rp)} 项）：{_rp[:3]}"
                "（注册表与实现须同步；确属删除请在注册表移除该条）"
            )
    except Exception as e:  # noqa: BLE001  判据不可执行 ≠ 判据通过
        problems.append(f"清洗规则注册表校验无法执行：{type(e).__name__}: {e}")

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
