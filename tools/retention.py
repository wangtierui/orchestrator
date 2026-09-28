# -*- coding: utf-8 -*-
"""
tools/retention.py — 数据生命周期与保留策略（v2 §3.10，P2-3）

原状（v2 §3.10）：备份目录**无清理策略**（`modules/regulatory_classifier/backups` 实测峰值 256 项），
`reports/_tmp/*.err` 与 `timeliness_review/*.log.err` 同样无策略 → 磁盘随时间单调增长，
且"哪些能删"只存在于人脑。

本工具把策略**声明化 + 可审计**：
  · `--dry-run`（**默认**）：只统计与出计划，不动文件；
  · `--apply`：把超出保留量的条目**移动**到仓根 `archive/<类目>/`（不删除 → 可人工复核后再清）；
  · 每次运行落 `reports/retention_<date>.json` 台账（入库、可审计）。

策略（`POLICY`，唯一事实源）
---------------------------
| 类目 | 目标 | 规则 |
|---|---|---|
| `classifier_backups` | `modules/regulatory_classifier/backups/` | 保留最近 `keep` 项；`incident_*` 保留 `incident_days` 天 |
| `ipb_backups` | `modules/internal_policy_base/backups/` | 同上 |
| `reports_tmp` | `reports/_tmp/` | 保留 `keep_days` 天（运行日志/临时产物） |
| `timeliness_err` | `modules/regulatory_scrapers/timeliness_review/*.log.err` | 移入 `reports/_tmp/timeliness_err/`（§3.10 要求"运行日志统一落 reports/_tmp"） |
| `repo_baks` | 全仓 `*.bak_*` / `*.bak2_*` | 只统计（`.gitignore` 已忽略），`--include-baks` 才归档 |

纪律：`archive/` 与 `reports/` 均在门禁排除集内（不新增盲区）；本工具**只移动不删除**。
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import glob
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
from config.exitcodes import ExitCode

ARCHIVE_ROOT = os.path.join(paths.ROOT, "archive")
LEDGER_DIR = os.path.join(paths.ROOT, "reports")

POLICY: tuple[dict, ...] = (
    {
        "name": "classifier_backups",
        "dir": "modules/regulatory_classifier/backups",
        "keep": 10,
        "incident_glob": "incident_*",
        "incident_days": 365,
    },
    {
        "name": "ipb_backups",
        "dir": "modules/internal_policy_base/backups",
        "keep": 10,
        "incident_glob": "incident_*",
        "incident_days": 365,
    },
    {
        "name": "reports_tmp",
        "dir": "reports/_tmp",
        "keep_days": 7,
        "exclude_names": ("schedule", "logs", "timeliness_err"),
    },
    {
        "name": "timeliness_err",
        "dir": "modules/regulatory_scrapers/timeliness_review",
        "glob": "*.log.err",
        "keep": 0,
        "move_to": "reports/_tmp/timeliness_err",
    },
    {
        # N-73b（2026-09-28）：**台账自管**。本工具每次运行落 `reports/retention_<date>.json`
        # （入库、可审计）；自第十三批起其 dry-run 计划**入链**（`retention:plan`），故台账逐日
        # 累积 → 必须纳入策略自身，否则"生命周期工具制造无主文件"自相矛盾。
        # 保留最近 30 份（约一个月），更早的按 `--apply` 移入 `archive/`（只移动不删除）。
        "name": "retention_ledgers",
        "dir": "reports",
        "include_glob": "retention_*.json",
        "keep": 30,
    },
    {
        # N-100（2026-09-28）：清洗**隔离件**同源多份 → 按源**分组**保留最新 1 份。
        # 背景：`quarantine_triage`（N-93）实测 nfra 残留 09-26/09-27/09-28 三份（字节数相同）、
        # supp 仅 09-26 一份而 cleaned 已 09-28 → 历史隔离件未随快照轮转，使"当前问题量"被虚增、
        # 且人工易误读。修复须**分组保留**（全局 keep 会误归档其他源的当前隔离件，见 `_plan_dir`）。
        # 只移动不删除：超期件进 `archive/`，`--apply` 仍为人工闸门。
        "name": "clean_quarantine_stale",
        "dir": "modules/regulatory_scrapers/data/cleaned",
        "include_glob": "*.quarantine.jsonl",
        "group_by_regex": "^([a-z]+)_cleaned_",
        "keep": 1,
        "move_to": "archive/clean_quarantine",
    },
)

SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "archive",
    "backups",
    "data",
    "reports",
    "node_modules",
    ".venv",
    "venv",
    ".codebuddy",
    "tessdata",
}


def _rel(p: str) -> str:
    return os.path.relpath(p, paths.ROOT).replace(os.sep, "/")


def _age_days(p: str) -> float:
    return (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400.0


def _plan_dir(spec: dict) -> tuple[list[dict], dict]:
    """按 `keep` 计划某目录：返回 (待归档条目, 统计)。"""
    base = os.path.join(paths.ROOT, spec["dir"])
    if not os.path.isdir(base):
        return [], {"exists": False}
    entries = [os.path.join(base, n) for n in os.listdir(base)]
    entries = [e for e in entries if os.path.isfile(e)]
    excluded = set(spec.get("exclude_names") or ())
    entries = [e for e in entries if os.path.basename(e) not in excluded]
    # N-73b（2026-09-28）：**包含过滤**（本类目只针对匹配名，如 `reports/retention_*.json`）——
    # 与 `exclude_names`（排除）互补，二者叠加使用；复用已导入的 `fnmatch`（不与 `glob.fnmatch` 混用）。
    inc_only = spec.get("include_glob", "")
    if inc_only:
        entries = [e for e in entries if fnmatch.fnmatch(os.path.basename(e), inc_only)]
    entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    keep = int(spec.get("keep", 10))
    # N-100（2026-09-28）：**分组保留**（`group_by_regex` 捕获组为分组键，`keep` 为**每组**保留量）。
    # 背景：清洗隔离件形如 `{src}_cleaned_{date}.quarantine.jsonl`，同一源会随快照滚动累积
    # （实测 nfra 09-26/09-27/09-28 三份、supp 仅 09-26 而 cleaned 已 09-28）。若用**全局** `keep`，
    # 只有全仓最新的一个文件被保留 → 会**误归档其他源的当前隔离件**（把在用数据移走）。
    # 故必须先按源分组，再在组内保留最新 `keep` 个。
    grp_re = spec.get("group_by_regex", "")
    group_keep: set = set()
    if grp_re:
        _by_grp: dict = {}
        for p in entries:
            m = re.match(grp_re, os.path.basename(p))
            _by_grp.setdefault(m.group(1) if m else "", []).append(p)
        for _ps in _by_grp.values():
            group_keep.update(_ps[:keep])
    inc_glob = spec.get("incident_glob", "")
    inc_days = int(spec.get("incident_days", 0))
    out: list[dict] = []
    n_incident_kept = 0
    kept = 0
    for i, p in enumerate(entries):
        name = os.path.basename(p)
        # N-70（2026-09-28）：原为 `glob.fnmatch.fnmatch(...)` —— `glob` 并无该**公开**属性
        # （仅因其实现在内部 `import fnmatch` 而偶然可见，属依赖实现细节；若上游改为
        # `from fnmatch import fnmatch` 即 AttributeError）。改显式导入，健壮且消 mypy attr-defined。
        if inc_glob and fnmatch.fnmatch(name, inc_glob):
            if inc_days and _age_days(p) <= inc_days:
                n_incident_kept += 1
                continue
        if grp_re:
            # 分组模式：保留判定完全由 `group_keep`（每组最新 keep 个）决定
            if p in group_keep:
                kept += 1
                continue
            _gm = re.match(grp_re, name)  # walrus/一次匹配：同时收窄类型并给出组名
            out.append(
                {
                    "file": _rel(p),
                    "size": os.path.getsize(p),
                    "age_days": round(_age_days(p), 1),
                    "reason": f"超出**分组**保留量 {keep}（组 {_gm.group(1) if _gm else '?'}）",
                }
            )
            continue
        if kept < keep:
            kept += 1
            continue
        out.append(
            {
                "file": _rel(p),
                "size": os.path.getsize(p),
                "age_days": round(_age_days(p), 1),
                "reason": f"超出保留量 {keep}（第 {i + 1} 新）",
            }
        )
    return out, {
        "exists": True,
        "total": len(entries),
        "kept": kept,
        "incident_kept": n_incident_kept,
    }


def _plan_age(spec: dict) -> tuple[list[dict], dict]:
    """按 `keep_days` 计划某目录（含子目录，但遵守 `exclude_names`）。"""
    base = os.path.join(paths.ROOT, spec["dir"])
    if not os.path.isdir(base):
        return [], {"exists": False}
    days = int(spec.get("keep_days", 7))
    excluded = set(spec.get("exclude_names") or ())
    out: list[dict] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            total += 1
            if _age_days(fp) > days:
                out.append(
                    {
                        "file": _rel(fp),
                        "size": os.path.getsize(fp),
                        "age_days": round(_age_days(fp), 1),
                        "reason": f"超过 {days} 天",
                    }
                )
    return out, {"exists": True, "total": total, "older_than_days": days}


def _plan_glob(spec: dict) -> tuple[list[dict], dict]:
    base = os.path.join(paths.ROOT, spec["dir"])
    if not os.path.isdir(base):
        return [], {"exists": False}
    hits = sorted(glob.glob(os.path.join(base, spec.get("glob", "*"))))
    out = [
        {
            "file": _rel(p),
            "size": os.path.getsize(p),
            "age_days": round(_age_days(p), 1),
            "reason": f"匹配 {spec.get('glob')}（§3.10：运行日志统一落 reports/_tmp）",
        }
        for p in hits
    ]
    return out, {"exists": True, "total": len(hits)}


def _plan_repo_baks(include: bool) -> tuple[list[dict], dict]:
    out: list[dict] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(paths.ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for fn in filenames:
            if ".bak_" in fn or ".bak2_" in fn:
                total += 1
                if include:
                    fp = os.path.join(dirpath, fn)
                    out.append(
                        {
                            "file": _rel(fp),
                            "size": os.path.getsize(fp),
                            "age_days": round(_age_days(fp), 1),
                            "reason": "*.bak_* / *.bak2_*（.gitignore 已忽略）",
                        }
                    )
    return out, {"total": total, "included": include}


def build_plan(*, include_baks: bool = False) -> dict:
    plan: list[dict] = []
    stats: dict = {}
    for spec in POLICY:
        if "keep_days" in spec:
            rows, st = _plan_age(spec)
        elif "glob" in spec:
            rows, st = _plan_glob(spec)
        else:
            rows, st = _plan_dir(spec)
        stats[spec["name"]] = st
        for r in rows:
            plan.append(
                {
                    **r,
                    "category": spec["name"],
                    "move_to": spec.get("move_to") or f"archive/{spec['name']}",
                }
            )
    rows, st = _plan_repo_baks(include_baks)
    stats["repo_baks"] = st
    for r in rows:
        plan.append({**r, "category": "repo_baks", "move_to": "archive/repo_baks"})
    return {"plan": plan, "stats": stats, "bytes": sum(r["size"] for r in plan)}


def apply_plan(plan: dict) -> dict:
    """执行归档（**移动**，不删除）。返回 {moved, failed, dests}。"""
    moved, failed, dests = 0, [], {}
    for row in plan["plan"]:
        src = os.path.join(paths.ROOT, row["file"].replace("/", os.sep))
        dest_dir = row["move_to"]
        dest_dir = dest_dir if os.path.isabs(dest_dir) else os.path.join(paths.ROOT, dest_dir)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(src))
            if os.path.exists(dest):
                stem, ext = os.path.splitext(dest)
                dest = f"{stem}_{datetime.datetime.now().strftime('%H%M%S')}{ext}"
            shutil.move(src, dest)
            dests[row["file"]] = _rel(dest)
            moved += 1
        except OSError as e:
            failed.append({"file": row["file"], "error": f"{type(e).__name__}: {e}"})
    return {"moved": moved, "failed": failed, "dests": dests}


def write_ledger(res: dict) -> str:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    fp = os.path.join(LEDGER_DIR, f"retention_{datetime.date.today().strftime('%Y%m%d')}.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return fp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据生命周期与保留策略（v2 §3.10；默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="执行归档（移动到 archive/，不删除）")
    ap.add_argument("--include-baks", action="store_true", help="把 *.bak_*/*.bak2_* 一并归档")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    plan = build_plan(include_baks=args.include_baks)
    res: dict = {"mode": "apply" if args.apply else "dry-run", "plan": plan, "applied": None}
    if args.apply:
        res["applied"] = apply_plan(plan)
    fp = write_ledger(res)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    else:
        print(
            f"[retention] 模式={res['mode']}  待归档 {len(plan['plan'])} 项 / "
            f"{plan['bytes'] / 1048576:.1f} MB"
        )
        for name, st in plan["stats"].items():
            print(f"  {name:<20} {st}")
        for row in plan["plan"][:12]:
            print(f"    → {row['file']}  ({row['age_days']}d, {row['size']}B) {row['reason']}")
        if len(plan["plan"]) > 12:
            print(f"    …（其余 {len(plan['plan']) - 12} 项见台账）")
        if res["applied"]:
            print(
                f"[retention] 已归档 {res['applied']['moved']} 项 → archive/；"
                f"失败 {len(res['applied']['failed'])}"
            )
        else:
            print("[retention] 未动文件（默认 dry-run）；确认后加 --apply（**只移动不删除**）")
        print(f"[retention] 台账：{_rel(fp)}")
    return ExitCode.OK


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
